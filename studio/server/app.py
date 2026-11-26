"""FastAPI-приложение Студии (этап 1).

    uv run python -m studio.server            # http://127.0.0.1:8765 – холст из web/dist
    uv run python -m studio.server --dev      # только API; холст – `npm run dev` (Vite проксирует /api и /files)

Переменные окружения: STUDIO_DATA (по умолчанию <проект>/data), BOT_REPO_PATH,
STUDIO_RUNTIME_DSN (база движка: задан – «Опубликовать» отправляет версию и в движок).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ValidationError

from ..convert import import_file
from ..engine_ref.drift import engine_drift
from ..model.funnel import Funnel, SourceInfo
from ..paths import bot_repo_path
from .archive import build_archive, media_refs, yaml_name
from .errors import translate
from .media import MediaError, save_upload, thumbnail
from .roundtrip import export_and_check
from .simulate import create_router as create_sim_router
from .storage import StorageError, make_name

log = logging.getLogger("studio.server")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = ROOT / "data"
DEFAULT_DIST = ROOT / "web" / "dist"


class ImportReq(BaseModel):
    path: str                 # относительно корня bot-engine, как в /api/sources
    overwrite: bool = False


class CreateReq(BaseModel):
    launch: str
    name: str
    id: str = ""


class ExportReq(BaseModel):
    graph: dict[str, Any]
    compact: bool = False


def _source_files(repo_root: Path) -> list[Path]:
    root = repo_root / "launches"
    return sorted(p for p in root.glob("*/funnel*.yaml") if " " not in p.name)


def create_app(data_dir: Path | None = None, repo_root: Path | None = None,
               dist_dir: Path | None = None, runtime_dsn: str | None = None) -> FastAPI:
    """runtime_dsn – база движка; None – из STUDIO_RUNTIME_DSN, "" – движок не подключён."""
    data_dir = Path(data_dir or os.environ.get("STUDIO_DATA") or DEFAULT_DATA)
    repo_root = Path(repo_root or bot_repo_path())
    dist_dir = Path(dist_dir or DEFAULT_DIST)
    # черновики и версии – data/studio.db (этап 2A); импорт здесь – studio.versions сам импортирует studio.server
    from ..versions.store import VersionedStorage
    from ..versions.engine import EngineLink, EngineRecords
    from .versions import versions_router
    store = VersionedStorage(data_dir)
    engine = EngineLink(os.environ.get("STUDIO_RUNTIME_DSN") if runtime_dsn is None else runtime_dsn,
                        repo_root=repo_root)
    records = EngineRecords(store.versions)
    app = FastAPI(title="Студия воронок", version="0.1")
    app.state.store = store
    app.state.repo_root = repo_root
    app.state.engine = engine

    def bad(status: int, message: str, **extra):
        raise HTTPException(status, {"message": message, **extra})

    def validate(raw: Any) -> Funnel:
        try:
            return Funnel.model_validate(raw)
        except ValidationError as e:
            errors = translate(e, raw)
            head = "; ".join(x["message"] for x in errors[:3])
            more = f" (и ещё {len(errors) - 3})" if len(errors) > 3 else ""
            bad(422, f"Граф не прошёл проверку: {head}{more}", errors=errors)

    @app.exception_handler(HTTPException)
    async def _http_exc(_req, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return JSONResponse(detail, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _any_exc(req, exc: Exception):
        # Непредвиденная ошибка – всё равно понятный текст: интерфейс показывает `message`,
        # без него в диалоге было бы голое «HTTP 500». Трейсбек – в журнал сервера.
        log.error("ошибка в %s %s", req.method, req.url.path, exc_info=exc)
        text = " ".join(str(exc).split())[:300]
        return JSONResponse({"message": f"Внутренняя ошибка Студии: {type(exc).__name__}: {text}. "
                                        "Подробности – в журнале сервера Студии"}, status_code=500)

    @app.get("/api/health")
    def health():
        # engine_copy – совпадает ли копия движка Студии (studio/engine_ref) с bot-engine по полям
        # моделей. Не совпадает – сверка выгрузки с bot-engine не работает (см. drift.py).
        try:
            d = engine_drift(repo_root)
        except Exception as e:  # noqa: BLE001
            d = {"same": None, "text": f"сверить копию движка с bot-engine не удалось: {e}"}
        return {"ok": True, "bot_repo": str(repo_root), "engine": engine.where() or None,
                "bot_repo_found": (repo_root / "launches").exists(), "data": str(data_dir),
                "engine_copy_matches_repo_bot": d.get("same"),
                "engine_copy": {k: d.get(k) for k in ("text", "new", "gone", "studio_commit", "engine_commit")}}

    # ── список и источники ──
    @app.get("/api/funnels")
    def list_funnels():
        return store.list()

    @app.get("/api/sources")
    def sources():
        saved = {x["source_path"] for x in store.list()}
        out = []
        for p in _source_files(repo_root):
            rel = p.relative_to(repo_root).as_posix()
            out.append({"path": rel, "launch": p.parent.name, "file": p.name,
                        "name": make_name(p.parent.name, p.stem), "imported": rel in saved})
        return out

    @app.post("/api/import")
    def do_import(req: ImportReq):
        p = (repo_root / req.path).resolve()
        launches = (repo_root / "launches").resolve()
        if launches not in p.parents or not p.name.startswith("funnel") or p.suffix != ".yaml":
            bad(400, f"«{req.path}» – не воронка запуска (ожидаем launches/<запуск>/funnel*.yaml)")
        if not p.exists():
            bad(404, f"файла «{req.path}» нет в bot-engine")
        name = make_name(p.parent.name, p.stem)
        if store.exists(name) and not req.overwrite:
            return {"name": name, "existed": True}
        try:
            graph = import_file(p, repo_root=repo_root)
        except Exception as e:  # noqa: BLE001 – показываем как есть
            bad(422, f"Движок не принимает этот yaml: {type(e).__name__}: {e}")
        store.save(name, graph)
        return {"name": name, "existed": False}

    @app.post("/api/funnels")
    def create(req: CreateReq):
        try:
            name = make_name(req.launch, req.name)
        except StorageError as e:
            bad(400, str(e))
        if store.exists(name):
            bad(409, f"воронка «{name}» уже есть")
        f = Funnel(id=req.id or req.name, source=SourceInfo(launch=req.launch))
        store.save(name, f)
        return {"name": name}

    # ── граф ──
    @app.get("/api/funnels/{name}")
    def get_funnel(name: str):
        # Черновик, сохранённый старой Студией, может не знать новых полей модели –
        # отдаём его с умолчаниями, иначе интерфейс падает на undefined .
        from ..versions.publish import normalize
        try:
            return normalize(store.load_raw(name))
        except StorageError as e:
            bad(400, str(e))
        except FileNotFoundError:
            bad(404, f"воронки «{name}» нет")

    @app.put("/api/funnels/{name}")
    def put_funnel(name: str, graph: dict[str, Any] = Body(...)):
        try:
            store.path(name)
        except StorageError as e:
            bad(400, str(e))
        f = validate(graph)
        store.save(name, f)
        return {"ok": True, "name": name, "steps": len(f.steps), "edges": len(f.edges),
                "history": len(store.history_of(name))}

    @app.post("/api/export")
    def export(req: ExportReq):
        f = validate(req.graph)
        out = export_and_check(f, repo_root, compact=req.compact)
        # загруженные в Студию файлы, которые нужно положить в static запуска
        out["media"] = media_refs(f, store.media)
        return out

    @app.post("/api/export/archive")
    def export_archive(req: ExportReq):
        """zip: yaml + загруженные в Студию файлы воронки + инструкция «куда положить»."""
        f = validate(req.graph)
        res = export_and_check(f, repo_root, compact=req.compact)
        if not res["ok"]:
            bad(422, "Выгрузить нельзя: " + (res["export_error"] or res["engine"]["error"] or "движок не читает yaml"))
        data = build_archive(f, res["yaml"], store.media)
        stem = yaml_name(f).rsplit(".", 1)[0]
        launch = f.source.launch if f.source and f.source.launch else "funnel"
        fname = f"{launch}__{stem}.zip"
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    @app.post("/api/funnels/{name}/export")
    def export_saved(name: str, compact: bool = False):
        try:
            f = store.load(name)
        except StorageError as e:
            bad(400, str(e))
        except FileNotFoundError:
            bad(404, f"воронки «{name}» нет")
        return export_and_check(f, repo_root, compact=compact)

    # ── медиа ──
    @app.post("/api/media")
    async def upload(file: UploadFile = File(...)):
        data = await file.read()
        try:
            info = save_upload(store.media, file.filename or "", data)
        except MediaError as e:
            bad(400, str(e))
        info["url"] = f"files/_/static/{info['name']}"
        return info

    def _send(p: Path, w: int | None):
        # ?w=<ширина> – уменьшенная копия для карточки холста (кэш в data/thumbs)
        if w:
            t = thumbnail(p, max(64, min(w, 1920)), store.root / "thumbs")
            if t is not None:
                return FileResponse(t, headers={"Cache-Control": "max-age=3600"})
        return FileResponse(p)

    @app.get("/files/{launch}/static/{fpath:path}")
    def files(launch: str, fpath: str, w: int | None = None):
        """./static/<путь> шага: сначала загруженное в Студию, потом папка запуска в bot-engine."""
        if not fpath or ".." in fpath.replace("\\", "/").split("/") or fpath.startswith(("/", "\\")):
            bad(400, "неверное имя файла")
        if "/" not in fpath:
            own = store.media / fpath
            if own.is_file():
                return _send(own, w)
        if launch != "_" and not launch.startswith("."):
            static = (repo_root / "launches" / launch / "static").resolve()
            p = (static / fpath).resolve()
            if static in p.parents and p.is_file():
                return _send(p, w)
        bad(404, f"файла {fpath} нет")

    app.include_router(versions_router(store, repo_root, engine, records))
    # ── раздел «Боты»: профиль бота через Bot API, токены – в зашифрованном data/bots.json ──
    from .bots import bots_router
    app.include_router(bots_router(store, repo_root, engine, records))
    # ── симулятор расписания и тест-прогон (этап 2B) ──
    app.include_router(create_sim_router(store, repo_root, validate, bad))
    # ── импорт из Miro ──
    from .miro_api import create_router as create_miro_router
    app.include_router(create_miro_router(store, repo_root, bad))

    # ── холст ──
    if dist_dir.exists():
        index = dist_dir / "index.html"
        dist_root = dist_dir.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "files/")):
                bad(404, "нет такого адреса API")
            p = (dist_dir / path).resolve()
            if path and dist_root in p.parents and p.is_file():
                return FileResponse(p)
            return FileResponse(index)

    return app
