"""Импорт из Miro – адреса API (этап 2 роадмапа 25.09).

  GET  /api/miro/status          – задан ли токен Miro на сервере
  POST /api/miro/import          – начать импорт: {link, launch, name}; → {job}
  GET  /api/miro/jobs/{job}      – ход импорта: этап, картинки N из M, итог
  POST /api/miro/jobs/{job}/apply – применить повторный импорт («было / стало» уже показано)

Новая воронка сохраняется в черновик сразу. Повторный импорт в существующую воронку
(та же доска) черновик не трогает, пока не нажали «Применить»: итог – слитый граф и
«было / стало» (`versions.diff`) против текущего черновика.

Картинки скачиваются оригиналами и кладутся в медиа Студии так же, как загрузка файла
(`media.save_upload`: без EXIF, ≤ 1920 px, ≤ 1 МБ). Снимок фрейма – в `data/miro_cache/`.
Токен – env MIRO_TOKEN, адрес API – env MIRO_API_BASE (для тестов).
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import yaml
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ..miro.build import build_funnel
from ..miro.client import MiroClient, MiroError, parse_link, save_snapshot
from ..miro.merge import merge_reimport, report_text
from ..miro.stickers import default_parser
from ..model.funnel import Funnel
from ..versions.diff import diff_graphs
from .media import MediaError, save_upload
from .storage import StorageError, make_name

log = logging.getLogger("studio.miro")

JOB_TTL = 3600


class ImportReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    link: str
    launch: str
    name: str


class _Job:
    def __init__(self, req: ImportReq):
        self.id = uuid.uuid4().hex
        self.req = req
        self.state = "running"          # running / done / error
        self.stage = "Снимаю фрейм с доски…"
        self.done = 0
        self.total = 0
        self.result: dict[str, Any] | None = None
        self.error = ""
        self.merged: Funnel | None = None
        self.started = time.time()

    def view(self) -> dict:
        out = {"job": self.id, "state": self.state, "stage": self.stage,
               "progress": {"done": self.done, "total": self.total}}
        if self.state == "done":
            out["result"] = self.result
        if self.state == "error":
            out["message"] = self.error
        return out


def _launch_cfg(repo_root: Path, launch: str) -> dict:
    p = repo_root / "launches" / launch / "launch.yaml"
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 – нет запуска или кривой yaml – просто без сверки
        return {}


def create_router(store, repo_root: Path, bad: Callable[..., None],
                  client_factory: Callable[[], MiroClient] | None = None) -> APIRouter:
    r = APIRouter()
    jobs: dict[str, _Job] = {}
    lock = threading.Lock()

    def new_client() -> MiroClient:
        return client_factory() if client_factory else MiroClient()

    @r.get("/api/miro/status")
    def status():
        return {"token": bool(os.environ.get("MIRO_TOKEN", "").strip()) or client_factory is not None,
                "claude": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())}

    def run(job: _Job) -> None:
        req = job.req
        try:
            ref = parse_link(req.link)
            if not ref.item_id:
                raise MiroError("в ссылке нет фрейма – в Miro выделите фрейм и скопируйте ссылку на него "
                                "(«Copy link to object»)")
            name = make_name(req.launch, req.name)
            with new_client() as cl:
                snap = cl.fetch_frame(ref)
                try:
                    save_snapshot(snap, store.root / "miro_cache" /
                                  f"{ref.board_id.rstrip('=')}_{snap['frame']['id']}.json")
                except OSError:
                    pass
                job.stage = "Раскладываю по сообщениям…"
                images = [i for i in snap.get("items", []) if i.get("type") == "image"]
                job.total = len(images)
                cache: dict[str, str] = {}

                def fetch(it) -> str:
                    url = (it.raw.get("data") or {}).get("imageUrl") or ""
                    if not url:
                        raise MiroError("у картинки нет адреса")
                    if url in cache:
                        return cache[url]
                    job.stage = "Скачиваю картинки…"
                    data, ctype = cl.image_original(url)
                    ext = ".png" if "png" in ctype else ".jpg"
                    try:
                        info = save_upload(store.media, f"miro{ext}", data)
                    except MediaError as e:
                        raise MiroError(str(e)) from None
                    cache[url] = info["ref"]
                    job.done += 1
                    return info["ref"]

                res = build_funnel(snap, launch=req.launch, funnel_id=req.name, parser=default_parser(),
                                   fetch_image=fetch, link=ref.item_url(str(snap["frame"]["id"])),
                                   launch_cfg=_launch_cfg(repo_root, req.launch))
            job.stage = "Готово"
            job.total = job.done     # картинка первой колонки (описание бота) не скачивается
            fresh = res.funnel
            result: dict[str, Any] = {"name": name, "stats": res.stats, "doubts": res.doubts(),
                                      "frame": res.board.frame_title}
            if store.exists(name):
                cur = store.load(name)
                if not cur.miro or cur.miro.frame_id != fresh.miro.frame_id:
                    raise MiroError(f"воронка «{name}» уже есть и собрана не из этого фрейма – "
                                    "выберите другое имя")
                merged, rep = merge_reimport(cur, fresh)
                job.merged = merged
                d = diff_graphs(cur.model_dump(mode="json", by_alias=True),
                                merged.model_dump(mode="json", by_alias=True))
                result.update({"created": False, "diff": d, "report": rep, "report_text": report_text(rep)})
            else:
                store.save(name, fresh)
                result.update({"created": True})
            job.result = result
            job.state = "done"
        except (MiroError, StorageError) as e:
            job.state, job.error = "error", str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("импорт из Miro упал")
            job.state, job.error = "error", f"Внутренняя ошибка импорта: {type(e).__name__}: {e}"

    @r.post("/api/miro/import")
    def start(req: ImportReq):
        if not req.link.strip() or not req.launch.strip() or not req.name.strip():
            bad(400, "нужны ссылка на фрейм, запуск и имя воронки")
        try:
            make_name(req.launch, req.name)
        except StorageError as e:
            bad(400, str(e))
        with lock:
            now = time.time()
            for k in [k for k, j in jobs.items() if now - j.started > JOB_TTL]:
                jobs.pop(k, None)
            job = _Job(req)
            jobs[job.id] = job
        threading.Thread(target=run, args=(job,), daemon=True, name=f"miro-{job.id[:6]}").start()
        return {"job": job.id}

    @r.get("/api/miro/jobs/{job_id}")
    def job_state(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            bad(404, "импорт не найден – запустите снова")
        return job.view()

    @r.post("/api/miro/jobs/{job_id}/apply")
    def apply(job_id: str):
        job = jobs.get(job_id)
        if job is None or job.state != "done" or job.merged is None:
            bad(409, "применять нечего – запустите импорт снова")
        name = job.result["name"]
        store.save(name, job.merged)
        job.merged = None
        return {"ok": True, "name": name}

    return r
