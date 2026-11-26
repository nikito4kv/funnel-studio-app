"""API версий: список, граф версии, дифф, проверка и публикация, откат.

| Адрес | Что |
|---|---|
| `GET  /api/funnels/{имя}/versions`          | версии (новые сверху) + состояние черновика |
| `GET  /api/funnels/{имя}/versions/{N}`      | граф версии N |
| `GET  /api/funnels/{имя}/diff?a=&b=`        | дифф: a, b – `draft` / `published` / номер |
| `POST /api/funnels/{имя}/diff`              | дифф несохранённого графа редактора против `against` |
| `POST /api/funnels/{имя}/publish/check`     | что опубликуется: дифф, блокеры, предупреждения, выгрузка |
| `POST /api/funnels/{имя}/publish`           | опубликовать черновик (409 – блокеры) |
| `POST /api/funnels/{имя}/rollback`          | черновик := копия версии N |

Публикация в движок (этап 3) – см. `studio/versions/engine.py`:

| Адрес | Что |
|---|---|
| `GET  /api/engine`                          | подключён ли движок, его запуски |
| `GET` / `PUT /api/funnels/{имя}/engine`     | запуск воронки (запоминается), что сейчас в движке, журнал отправок |
| `POST /api/funnels/{имя}/engine/check`      | пробная публикация: отчёт по живым людям, отказ, подсказка карты |
| `POST /api/funnels/{имя}/publish` + `engine`| версия Студии и сразу – в движок (одна операция) |
| `POST /api/funnels/{имя}/versions/{N}/engine` | отправить опубликованную vN в движок (повтор после отказа, другой запуск) |

`engine.make_entry` – сделать воронку стартовой у бота (`/start` ведёт сюда). `GET …/engine`
отдаёт и состояние «в боте»: какая версия там работает, стартовая ли воронка, ссылку
«Проверить в боте» (`t.me/<бот>?start=studio_test_<воронка>`, `runtime/studio_test.py`).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from runtime import studio_test

from ..model.funnel import Funnel
from ..versions.diff import diff_graphs
from ..versions.engine import MODES, EngineLink, EngineRecords, EngineUnavailable, clean_redirects
from ..versions.publish import PublishBlocked, check_publish, graph_of, normalize, publish
from ..versions.store import VersionError, VersionedStorage, keep_notes
from .storage import StorageError


class DiffReq(BaseModel):
    graph: dict[str, Any]
    against: str | int | None = "published"


class CheckReq(BaseModel):
    validation_errors: int = 0


class EngineReq(BaseModel):
    tenant: str
    mode: str = "new-only"         # new-only – «только новым», migrate – «перевести всех»
    redirects: dict[str, str] = {}  # {id исчезнувшего шага: id нового} – только для migrate
    make_entry: bool = False       # сделать воронку стартовой: /start бота ведёт сюда


class EngineCheckReq(EngineReq):
    version: int | None = None     # None – черновик, иначе версия Студии


class EngineSendReq(EngineReq):
    author: str = "я"
    comment: str = ""


class TargetReq(BaseModel):
    tenant: str


class PublishReq(BaseModel):
    comment: str = ""
    author: str = "я"
    validation_errors: int = 0
    number: int | None = None      # «Опубликовать v4» – если за это время появилась v4, откажем
    engine: EngineReq | None = None  # задан – версия сразу уходит и в движок


class RollbackReq(BaseModel):
    version: int


def versions_router(store: VersionedStorage, repo_root: Path, engine: EngineLink | None = None,
                    records: EngineRecords | None = None) -> APIRouter:
    r = APIRouter()
    engine = engine or EngineLink(None)
    records = records or EngineRecords(store.versions)

    def bad(status: int, message: str, **extra):
        raise HTTPException(status, {"message": message, **extra})

    def need(name: str) -> None:
        try:
            if not store.exists(name):
                bad(404, f"воронки «{name}» нет")
        except StorageError as e:
            bad(400, str(e))

    def get_graph(name: str, which: str | int | None) -> dict | None:
        try:
            return graph_of(store, name, which)
        except FileNotFoundError:
            bad(404, f"версии «{which}» у воронки «{name}» нет")
        except ValueError:
            bad(400, f"неверная версия «{which}»: draft, published или номер")

    @r.get("/api/funnels/{name}/versions")
    def list_versions(name: str):
        need(name)
        row = store.versions.draft_row(name)
        sent = records.by_number(name)
        return {
            "name": name,
            "published": store.versions.published_number(name),
            "next": store.versions.next_number(name),
            "draft": {"base_version": row["base_version"], "updated_at": row["updated_at"],
                      "saves": row["saves"], "migrated_from": row["migrated_from"]},
            "versions": [{**v, "engine": sent.get(v["number"], [])} for v in store.versions.versions(name)],
            "engine_target": records.target(name),
        }

    @r.get("/api/funnels/{name}/versions/{number}")
    def get_version(name: str, number: int):
        need(name)
        g = get_graph(name, number)
        return normalize(g) if g else g

    @r.get("/api/funnels/{name}/diff")
    def diff_saved(name: str, a: str = "published", b: str = "draft"):
        need(name)
        ga, gb = get_graph(name, a), get_graph(name, b)
        if gb is None:
            bad(404, "опубликованной версии ещё нет")
        res = diff_graphs(normalize(ga) if ga else None, normalize(gb))
        return {**res, "a": a, "b": b, "a_exists": ga is not None}

    @r.post("/api/funnels/{name}/diff")
    def diff_live(name: str, req: DiffReq):
        need(name)
        against = req.against if req.against not in (None, "") else "published"
        base = get_graph(name, against)
        res = diff_graphs(normalize(base) if base else None, normalize(req.graph))
        n = store.versions.published_number(name) if against == "published" else against
        return {**res, "against": n if base is not None else None}

    @r.post("/api/funnels/{name}/publish/check")
    def publish_check(name: str, req: CheckReq = Body(default=CheckReq())):
        need(name)
        return check_publish(store, name, repo_root, validation_errors=req.validation_errors,
                             repo_blocking=not engine.configured)

    # ── движок ──
    def engine_args(req: EngineReq) -> tuple[str, str, dict[str, str]]:
        if req.mode not in MODES:
            bad(400, f"неизвестный режим «{req.mode}»: new-only или migrate")
        if not req.tenant.strip():
            bad(400, "не выбран запуск движка")
        if not engine.configured:
            bad(409, "Движок не подключён: у сервера Студии не задан STUDIO_RUNTIME_DSN")
        # карта нужна только «перевести всех»: «только новым» людей не переносит
        return req.tenant.strip(), req.mode, clean_redirects(req.redirects) if req.mode == "migrate" else {}

    def studio_graphs(name: str) -> list[dict]:
        return [store.versions.version_graph(name, v["number"]) for v in store.versions.versions(name)]

    def engine_check(name: str, raw: dict, req: EngineReq) -> dict:
        tenant, mode, redirects = engine_args(req)
        try:
            f = Funnel.model_validate(raw)
        except Exception:  # noqa: BLE001 – блокер «модель» покажет проверка Студии
            bad(409, "Черновик не проходит проверку модели – движку его не отправить")
        try:
            return engine.check(tenant, f, mode=mode, redirects=redirects, studio_graphs=studio_graphs(name))
        except EngineUnavailable as e:
            bad(503, str(e))

    def send(name: str, number: int, req: EngineReq, *, author: str, comment: str) -> dict:
        """Версия Студии N → движок. Отказ записывается в журнал как «не ушла» – честно."""
        from runtime.publish import PublishError, StudioVersionSource
        tenant, mode, redirects = engine_args(req)
        src = StudioVersionSource(store)
        ref = f"{name}@{number}"
        f = src.load(ref)
        base = {"tenant": tenant, "funnel_id": f.id, "mode": mode, "author": author, "redirects": redirects}
        try:
            out = engine.publish(tenant, f, mode=mode, redirects=redirects, comment=comment, author=author,
                                 source=src.describe(ref))
        except PublishError as e:
            rep = e.report.to_json() if e.report else None
            err = "; ".join(e.report.errors) if e.report and e.report.errors else str(e)
            row = records.add(name, number, ok=False, engine_version=None, error=err, report=rep, **base)
            return {**row, "text": e.report.text() if e.report else err}
        except EngineUnavailable as e:
            row = records.add(name, number, ok=False, engine_version=None, error=str(e), **base)
            return {**row, "text": str(e)}
        records.set_target(name, tenant)
        row = records.add(name, number, ok=True, engine_version=out["version"], report=out["report"], **base)
        entry = None
        if req.make_entry:
            try:
                engine.set_entry(tenant, f.id)
                entry = {"ok": True, "funnel_id": f.id}
            except EngineUnavailable as e:
                entry = {"ok": False, "error": str(e)}
        return {**row, "text": out["text"], "entry": entry}

    @r.get("/api/engine")
    def engine_info():
        if not engine.configured:
            return {"configured": False, "where": "", "tenants": [],
                    "error": "Движок не подключён: у сервера Студии не задан STUDIO_RUNTIME_DSN"}
        try:
            return {"configured": True, "where": engine.where(), "error": None, "tenants": engine.tenants()}
        except EngineUnavailable as e:
            return {"configured": True, "where": engine.where(), "error": str(e), "tenants": []}

    @r.get("/api/funnels/{name}/engine")
    def funnel_engine(name: str):
        """Что с воронкой в боте: выбранный запуск, его бот, опубликованная в нём версия,
        стартовая ли воронка, ссылка «Проверить в боте», журнал отправок."""
        need(name)
        info = engine_info()
        tenant = records.target(name)
        fid = store.load_raw(name).get("id", "")
        if not tenant:
            # в бот воронку могли отправить не кнопкой Студии (командной строкой): запуск берём из
            # движка – где она опубликована (где она стартовая – первым)
            found = sorted((x for x in info["tenants"] if fid in x.get("funnels", [])),
                           key=lambda x: x.get("entry_funnel") != fid)
            tenant = found[0]["slug"] if found else None
        state = None
        if tenant and info["configured"] and not info["error"]:
            try:
                state = engine.state(tenant, fid)
            except EngineUnavailable as e:
                info = {**info, "error": str(e)}
        t = next((x for x in info["tenants"] if x["slug"] == tenant), None)
        tg = next((b for b in (t or {}).get("bots", []) if b["platform"] == "tg" and b["username"]), None)
        in_bot = None
        if state and state["versions"]:
            top = state["versions"][0]
            m = re.fullmatch(rf"studio:{re.escape(name)}@v(\d+)", top.get("source") or "")
            in_bot = {"engine_version": top["version"], "studio_number": int(m.group(1)) if m else None,
                      "published_at": top["published_at"], "published_by": top["published_by"]}
        return {**info, "tenant": tenant, "funnel_id": fid, "state": state, "history": records.history(name),
                "in_bot": in_bot,
                "is_entry": bool(t and t.get("entry_funnel") == fid),
                "test_link": studio_test.link(tg["username"], fid) if (tg and in_bot) else None}

    @r.put("/api/funnels/{name}/engine")
    def set_engine_target(name: str, req: TargetReq):
        need(name)
        if not req.tenant.strip():
            bad(400, "не выбран запуск движка")
        records.set_target(name, req.tenant.strip())
        return {"ok": True, "tenant": req.tenant.strip()}

    @r.post("/api/funnels/{name}/engine/check")
    def engine_dry_run(name: str, req: EngineCheckReq):
        need(name)
        raw = store.load_raw(name) if req.version is None else get_graph(name, req.version)
        return engine_check(name, raw, req)

    @r.post("/api/funnels/{name}/versions/{number}/engine")
    def engine_send(name: str, number: int, req: EngineSendReq):
        need(name)
        pub = store.versions.published_number(name)
        if pub != number:
            bad(409, f"В движок отправляется только опубликованная версия (сейчас {f'v{pub}' if pub else 'нет'}). "
                     "Откат – «Откатить сюда» и «Опубликовать»")
        with store.versions.lock:
            dry = engine_check(name, store.versions.version_graph(name, number), req)
            if not dry["ok"]:
                bad(409, "Движок отказал: " + (dry["error"] or ""), engine=dry)
            return send(name, number, req, author=req.author.strip() or "я", comment=req.comment.strip())

    @r.post("/api/funnels/{name}/publish")
    def do_publish(name: str, req: PublishReq):
        need(name)
        author = req.author.strip() or "я"
        try:
            with store.versions.lock:
                chk = check_publish(store, name, repo_root, validation_errors=req.validation_errors,
                                    repo_blocking=not engine.configured)
                if chk["blockers"]:
                    raise PublishBlocked(chk)
                if req.engine is not None:
                    # сначала – пробный прогон в движке: отказ движка = не публикуем вовсе
                    dry = engine_check(name, store.load_raw(name), req.engine)
                    if not dry["ok"]:
                        bad(409, "Движок отказал: " + (dry["error"] or ""), engine=dry)
                res = publish(store, name, repo_root, comment=req.comment.strip(), author=author,
                              validation_errors=req.validation_errors, number=req.number, chk=chk)
                if req.engine is not None:
                    res["engine"] = send(name, res["number"], req.engine, author=author,
                                         comment=req.comment.strip())
                return res
        except PublishBlocked as e:
            bl = e.check["blockers"]
            bad(409, "Опубликовать нельзя: " + "; ".join(b["text"] for b in bl[:2]) +
                (f" (и ещё {len(bl) - 2})" if len(bl) > 2 else ""), blockers=bl)
        except VersionError as e:
            bad(409, str(e))

    @r.post("/api/funnels/{name}/rollback")
    def rollback(name: str, req: RollbackReq):
        need(name)
        g = get_graph(name, req.version)
        merged = keep_notes(g, store.load_raw(name))
        f = Funnel.model_validate(merged)
        store.save(name, f, base_version=req.version)
        return {"ok": True, "version": req.version, "graph": f.model_dump(mode="json", by_alias=True)}

    return r
