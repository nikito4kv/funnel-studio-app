"""API раздела «Боты» (этап 3 роадмапа 25.09).

| адрес | что |
|---|---|
| `GET /api/bots` | боты Студии (без токенов) + где работают; боты bot-engine и движка без токена; общие MAX-боты |
| `GET /api/bots/index` | лёгкий список «@username → id, имя» – для аватара в шапке и диалоге запуска |
| `POST /api/bots` `{token}` | добавить бота: getMe → карточка. Токен – в зашифрованное хранилище |
| `DELETE /api/bots/{id}` | забыть токен (в Telegram ничего не меняется) |
| `GET /api/bots/{id}` | профиль из Telegram: имя, описания, команды, флаги getMe, где работает |
| `GET /api/bots/{id}/avatar` | аватар (кэш 10 мин, `?fresh=1` – заново) |
| `PUT /api/bots/{id}/profile/{поле}` `{value, confirm}` | name / description / short_description / commands / photo |
| `GET /api/bots-tools/description-pic?media=` | картинка 640×360 для @BotFather /setdescriptionpic |

Токен из ответов не возвращается никогда – только `last4`. Правка профиля бота, который не
тестовый, без `confirm: true` не выполняется: 409 с «было → стало».
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse, Response

from ..bots import images
from ..bots.telegram import BotApi, BotApiError, ForbiddenMethod, TOKEN_RE, scrub
from ..bots.vault import BotVault, VaultError
from ..bots.where import max_groups, repo_by_username, repo_launches
from ..versions.engine import EngineUnavailable, is_test_bot

log = logging.getLogger("studio.bots")

LIMITS = {"name": 64, "description": 512, "short_description": 120}
COMMAND_RE = re.compile(r"^[a-z0-9_]{1,32}$")
AVATAR_TTL = 600


def _bad(status: int, message: str, **extra):
    raise HTTPException(status, {"message": message, **extra})


def _plain(v: Any) -> str:
    return "" if v is None else str(v)


def check_commands(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        _bad(400, "команды – список «команда – описание»")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, c in enumerate(raw, 1):
        cmd = _plain((c or {}).get("command")).strip().lstrip("/").strip()
        desc = _plain((c or {}).get("description")).strip()
        if not cmd and not desc:
            continue
        if not COMMAND_RE.match(cmd):
            _bad(400, f"команда {i} «/{cmd}»: только маленькие латинские буквы, цифры и «_», до 32 символов")
        if not desc or len(desc) > 256:
            _bad(400, f"команда /{cmd}: нужно описание до 256 символов – его видно в меню бота")
        if cmd in seen:
            _bad(400, f"команда /{cmd} повторяется")
        seen.add(cmd)
        out.append({"command": cmd, "description": desc})
    if len(out) > 100:
        _bad(400, "команд больше 100 – Telegram столько не примет")
    return out


def bots_router(store, repo_root: Path, engine, records) -> APIRouter:
    r = APIRouter()
    vault = BotVault(store.root)
    avatars = store.root / "bot_avatars"

    def client(req: Request, token: str) -> BotApi:
        return BotApi(token, transport=getattr(req.app.state, "bot_api_transport", None))

    def bot_client(req: Request, bot_id: int) -> BotApi:
        try:
            token = vault.token(bot_id)
        except KeyError:
            _bad(404, "такого бота в Студии нет – добавьте его токеном")
        except VaultError as e:
            _bad(409, str(e))
        return client(req, token)

    # ── где работает ──
    def engine_tenants() -> tuple[list[dict], str | None]:
        if not engine.configured:
            return [], None
        try:
            return engine.tenants(), None
        except EngineUnavailable as e:
            return [], str(e)
        except Exception as e:  # noqa: BLE001 – список ботов не должен падать из-за движка
            return [], f"{type(e).__name__}: {scrub(str(e))[:200]}"

    def studio_targets() -> dict[str, set[str]]:
        """запуск движка → воронки Студии, которые в него отправлялись или выбраны для него."""
        out: dict[str, set[str]] = {}
        try:
            with records.db() as c:
                for row in c.execute("SELECT name, tenant FROM engine_targets "
                                     "UNION SELECT name, tenant FROM engine_publishes WHERE status = 'ok'"):
                    out.setdefault(row["tenant"], set()).add(row["name"])
        except Exception:  # noqa: BLE001
            pass
        return out

    def context() -> dict[str, Any]:
        launches = repo_launches(repo_root)
        tenants, err = engine_tenants()
        funnels = store.list()
        return {"launches": launches, "repo": repo_by_username(launches), "tenants": tenants,
                "engine_error": err, "funnels": funnels, "targets": studio_targets()}

    def funnel_ref(f: dict) -> dict:
        # как в списке воронок шапки: «запуск / воронка», если у воронки нет названия
        launch, _, tail = f["name"].partition("__")
        return {"name": f["name"], "label": f.get("title") or f"{launch} / {tail}"}

    def where(username: str, ctx: dict) -> list[dict]:
        u = (username or "").lower()
        out: list[dict] = []
        if not u:
            return out
        for t in ctx["tenants"]:
            if any((b["username"] or "").lower() == u for b in t["bots"]):
                names = ctx["targets"].get(t["slug"], set())
                out.append({"kind": "studio", "slug": t["slug"], "title": t["title"] or t["slug"],
                            "funnels": [funnel_ref(f) for f in ctx["funnels"] if f["name"] in names],
                            "entry_funnel": t.get("entry_funnel")})
        for x in ctx["repo"].get(u, []):
            out.append({"kind": "repo", "slug": x["slug"], "title": x["title"],
                        "funnels": [funnel_ref(f) for f in ctx["funnels"] if f["launch"] == x["slug"]]})
        return out

    def careful(username: str, ctx: dict) -> bool:
        """Не тестовый бот – правка только после подтверждения «было → стало»."""
        combat = set(ctx["repo"].keys()) if ctx else engine.combat()
        return not is_test_bot(username, "", combat)

    def card(b: dict, ctx: dict) -> dict:
        return {"id": b["id"], "username": b.get("username") or "", "first_name": b.get("first_name") or "",
                "last4": b.get("last4") or "", "added_at": b.get("added_at"),
                "avatar": f"api/bots/{b['id']}/avatar", "careful": careful(b.get("username") or "", ctx),
                "where": where(b.get("username") or "", ctx)}

    # ── список ──
    @r.get("/api/bots")
    def list_bots():
        ctx = context()
        key = vault.key_state()
        try:
            mine = vault.list()
        except VaultError as e:
            mine, key = [], {"ok": False, "source": None, "error": str(e)}
        own = {(b.get("username") or "").lower() for b in mine}
        others: dict[str, dict] = {}
        for t in ctx["tenants"]:
            for b in t["bots"]:
                u = (b["username"] or "").lower()
                if b["platform"] == "tg" and u and u not in own:
                    others.setdefault(u, {"username": b["username"], "test": b["test"]})
        for u, xs in ctx["repo"].items():
            if u not in own:
                others.setdefault(u, {"username": xs[0]["tg"]["username"], "test": False})
        engine_max = [{"username": b["username"], "tenant": t["slug"], "title": t["title"]}
                      for t in ctx["tenants"] for b in t["bots"] if b["platform"] == "max"]
        return {
            "key": key,
            "engine": {"configured": engine.configured, "error": ctx["engine_error"]},
            "bots": [card(b, ctx) for b in mine],
            "others": sorted(({**o, "where": where(o["username"], ctx)} for o in others.values()),
                             key=lambda o: (not o["test"], o["username"].lower())),
            "max": max_groups(ctx["launches"], engine_max),
        }

    @r.get("/api/bots/index")
    def index():
        try:
            return {"bots": [{"id": b["id"], "username": b.get("username") or "",
                              "first_name": b.get("first_name") or ""} for b in vault.list()]}
        except VaultError:
            return {"bots": []}

    # ── добавить / забыть ──
    @r.post("/api/bots")
    def add(req: Request, body: dict[str, Any] = Body(...)):
        # тело разбираем сами: ошибка схемы FastAPI вернула бы токен обратно в ответе
        token = _plain(body.get("token") if isinstance(body, dict) else "").strip()
        token = re.sub(r"\s+", "", token)
        if not TOKEN_RE.match(token):
            _bad(400, "Это не похоже на токен бота. Он выглядит так: 123456789:AAH…, скопируйте его "
                      "целиком из @BotFather (API Token)")
        key = vault.key_state()
        if not key["ok"] and key["source"] is None:
            _bad(409, key["error"])
        try:
            with client(req, token) as api:
                me = api.me()
        except BotApiError as e:
            _bad(400, str(e))
        if not me or not me.get("is_bot"):
            _bad(400, "Это токен не бота – возьмите токен в @BotFather")
        rec = vault.put(me, token)
        log.info("бот @%s (id %s) добавлен в Студию, токен %s", rec["username"], rec["id"], "…" + rec["last4"])
        ctx = context()
        return {**card(rec, ctx), "fresh": rec["fresh"]}

    @r.delete("/api/bots/{bot_id}")
    def remove(bot_id: int):
        if not vault.remove(bot_id):
            _bad(404, "такого бота в Студии нет")
        for p in avatars.glob(f"{bot_id}.*"):
            p.unlink(missing_ok=True)
        return {"ok": True}

    # ── профиль ──
    @r.get("/api/bots/{bot_id}")
    def profile(req: Request, bot_id: int):
        b = vault.get(bot_id)
        if b is None:
            _bad(404, "такого бота в Студии нет – добавьте его токеном")
        try:
            with bot_client(req, bot_id) as api:
                p = api.profile()
        except BotApiError as e:
            _bad(502, str(e))
        me = p["me"]
        if me.get("username") != b.get("username") or me.get("first_name") != b.get("first_name"):
            vault.touch(bot_id, username=me.get("username") or "", first_name=me.get("first_name") or "")
            b = vault.get(bot_id) or b
        ctx = context()
        flags = {k: me.get(k) for k in ("can_join_groups", "can_read_all_group_messages",
                                         "supports_inline_queries", "has_main_web_app", "can_connect_to_business")}
        return {**card(b, ctx), "name": p["name"] or me.get("first_name") or "",
                "description": p["description"], "short_description": p["short_description"],
                "commands": p["commands"], "flags": flags, "limits": LIMITS}

    @r.get("/api/bots/{bot_id}/avatar")
    def avatar(req: Request, bot_id: int, fresh: int = 0):
        if vault.get(bot_id) is None:
            _bad(404, "такого бота в Студии нет")
        p = avatars / f"{bot_id}.jpg"
        none = avatars / f"{bot_id}.none"
        now = time.time()
        if not fresh:
            if p.is_file() and now - p.stat().st_mtime < AVATAR_TTL:
                return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=300"})
            if none.is_file() and now - none.stat().st_mtime < AVATAR_TTL:
                _bad(404, "у бота нет аватара")
        try:
            with bot_client(req, bot_id) as api:
                data = api.avatar(bot_id)
        except BotApiError as e:
            if p.is_file():            # Telegram недоступен – старая картинка лучше пустого круга
                return FileResponse(p, media_type="image/jpeg")
            _bad(502, str(e))
        avatars.mkdir(parents=True, exist_ok=True)
        if data is None:
            p.unlink(missing_ok=True)
            none.write_text("", encoding="utf-8")
            _bad(404, "у бота нет аватара")
        none.unlink(missing_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=300"})

    def current(api: BotApi, field: str, bot_id: int) -> Any:
        if field == "name":
            return (api.call("getMyName") or {}).get("name", "")
        if field == "description":
            return (api.call("getMyDescription") or {}).get("description", "")
        if field == "short_description":
            return (api.call("getMyShortDescription") or {}).get("short_description", "")
        if field == "commands":
            return [{"command": c.get("command", ""), "description": c.get("description", "")}
                    for c in (api.call("getMyCommands") or [])]
        if field == "photo":
            photos = api.call("getUserProfilePhotos", {"user_id": bot_id, "limit": 1}) or {}
            return bool(photos.get("total_count") or photos.get("photos"))
        return None

    @r.put("/api/bots/{bot_id}/profile/{field}")
    def put_field(req: Request, bot_id: int, field: str, body: dict[str, Any] = Body(...)):
        if field not in ("name", "description", "short_description", "commands", "photo"):
            _bad(404, f"поле «{field}» из Студии не меняется")
        b = vault.get(bot_id)
        if b is None:
            _bad(404, "такого бота в Студии нет – добавьте его токеном")
        value = body.get("value")
        jpeg: bytes | None = None
        if field in LIMITS:
            value = _plain(value).strip()
            if len(value) > LIMITS[field]:
                _bad(400, f"слишком длинно: {len(value)} из {LIMITS[field]} символов")
            if field == "name" and not value:
                _bad(400, "имя бота не может быть пустым")
        elif field == "commands":
            value = check_commands(value)
        else:
            try:
                jpeg = images.avatar_jpeg(images.media_file(store.media, _plain(value)))
            except images.ImageError as e:
                _bad(400, str(e))
        ctx = context()
        need = careful(b.get("username") or "", ctx)
        try:
            with bot_client(req, bot_id) as api:
                was = current(api, field, bot_id)
                now_v: Any = True if field == "photo" else value
                if need and body.get("confirm") is not True:
                    places = [w["slug"] for w in where(b.get("username") or "", ctx)]
                    _bad(409, "Это не тестовый бот – изменение увидят все, кто им пользуется. "
                              "Подтвердите «было → стало»", need_confirm=True, field=field,
                         was=was, now=now_v, where=places)
                if field == "name":
                    api.set_name(value)
                elif field == "description":
                    api.set_description(value)
                elif field == "short_description":
                    api.set_short_description(value)
                elif field == "commands":
                    api.set_commands(value)
                else:
                    api.set_photo(jpeg or b"")
                after = current(api, field, bot_id)
                me = api.me() if field == "name" else None
        except BotApiError as e:
            _bad(502, str(e))
        except ForbiddenMethod as e:        # страховка: сюда не должны попадать никогда
            _bad(500, str(e))
        if field == "photo":
            for p in avatars.glob(f"{bot_id}.*"):
                p.unlink(missing_ok=True)
        if me:
            vault.touch(bot_id, first_name=me.get("first_name") or "", username=me.get("username") or "")
        log.info("бот @%s: изменено поле %s", b.get("username"), field)
        return {"ok": True, "field": field, "was": was, "now": after}

    @r.get("/api/bots-tools/description-pic")
    def description_pic(media: str):
        try:
            data = images.description_pic(images.media_file(store.media, media))
        except images.ImageError as e:
            _bad(400, str(e))
        return Response(data, media_type="image/jpeg",
                        headers={"Content-Disposition": 'attachment; filename="description_640x360.jpg"'})

    return r
