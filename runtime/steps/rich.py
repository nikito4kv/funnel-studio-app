"""Шаг-статья (type: rich) – перенос _send_rich / _rich_blocks tg-сендера bot-engine.

Статья – одно сообщение из блоков (заголовки, абзацы, списки, картинки с
подписями), метод Bot API `sendRichMessage` (07.2026). Блоки лежат в JSON
(`media.rich`: `./rich/<файл>.json` или `./static/<файл>.json`) – как в bot-engine:
`{"blocks": [...]}`. Кнопки – обычные inline-кнопки шага.

Картинки внутри блоков (`./static/…`) метод принимает ТОЛЬКО file_id: файл
сначала заливается служебным сообщением в `service_chat_id` запуска (в
bot-engine – первому админу), сообщение удаляется, file_id кэшируется в
media_cache по (sha256, бот). Служебного чата нет – блок с такой картинкой
выкидывается (как в движке без админов).

aiogram 3.13 метода не знает – он описан здесь (`SendRichMessage`).

Отличие от bot-engine: статья не ушла (блоков нет, метод отклонён) – уходит
текст шага (он и так лежит в шаге запасом для MAX/VK); движок в этом случае
не слал ничего. Дальше – как у обычного шага: on_done (или переход в
воронку), also_fire.
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods.base import TelegramMethod
from aiogram.types import FSInputFile, InlineKeyboardMarkup, Message

from studio.model.funnel import Step

from .. import repo
from .common import begin, finish, mark_blocked, sent_row, settings_for, static_launch

if TYPE_CHECKING:
    from ..executor import Executor, UserCtx
    from ..graph import CompiledFunnel
    from ..repo import Outcome

log = logging.getLogger("runtime.steps.rich")


class SendRichMessage(TelegramMethod[Message]):
    """sendRichMessage (Bot API 07.2026) – в aiogram 3.13 его нет."""
    __returning__ = Message
    __api_method__ = "sendRichMessage"

    chat_id: Union[int, str]
    rich_message: dict[str, Any]
    reply_markup: Optional[InlineKeyboardMarkup] = None


_cache: dict[tuple[int, str, float], list[dict]] = {}


def _find(ex: "Executor", ref: str, launch: str) -> Path | None:
    ref = (ref or "").strip()
    if ref.startswith(("./static/", "static/")):
        m = ex.rt.media.resolve(ref, launch)
        return Path(m.value) if m is not None and m.kind == "file" else None
    rel = ref[2:] if ref.startswith("./") else ref
    if not rel or ".." in Path(rel).parts or rel.startswith(("/", "\\")):
        return None
    media = ex.rt.media
    cands = [media.data_dir / "media" / rel, media.data_dir / "media" / Path(rel).name]
    if media.repo_root and launch:
        cands.append(media.repo_root / "launches" / launch / rel)
    return next((p for p in cands if p.is_file()), None)


async def _file_id(ex: "Executor", entry, service_chat: int, path: str, launch: str, field: str) -> str | None:
    """Картинка/видео блока → file_id этого бота (кэш media_cache)."""
    if not path.startswith(("./", "static/")) and "/" not in path and len(path) > 20:
        return path    # уже file_id
    ref = ex.rt.media.resolve(path, launch)
    if ref is None or ref.kind != "file":
        log.warning("rich: файла %s нет", path)
        return None
    kind = {"photo": "photo", "video": "video", "animation": "animation"}.get(field, "photo")
    async with ex.rt.pool.acquire() as conn:
        fid = await repo.media_get(conn, ref.sha256, entry.tg_bot_id, kind)
    if fid:
        return fid
    if not service_chat:
        log.warning("rich: некуда залить %s (service_chat_id не задан) – блок без медиа выкидывается",
                    path)
        return None
    send = {"photo": entry.bot.send_photo, "video": entry.bot.send_video,
            "animation": entry.bot.send_animation}[kind]
    try:
        probe = await send(service_chat, FSInputFile(ref.value), disable_notification=True)
    except Exception as e:  # noqa: BLE001
        log.warning("rich: заливка %s не удалась: %s", path, e)
        return None
    obj = getattr(probe, kind, None)
    fid = (obj[-1].file_id if isinstance(obj, list) else getattr(obj, "file_id", None)) if obj else None
    try:
        await entry.bot.delete_message(service_chat, probe.message_id)
    except Exception:  # noqa: BLE001
        pass
    if fid:
        async with ex.rt.pool.acquire() as conn:
            await repo.media_put(conn, ref.sha256, entry.tg_bot_id, kind, fid)
    return fid


async def load_blocks(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step, entry) -> list[dict] | None:
    launch = static_launch(ex, u.tenant_id, g)
    path = _find(ex, step.media.rich or "", launch)
    if path is None:
        log.warning("[%s] rich %s: файл статьи %s не найден", u.slug, step.key, step.media.rich)
        return None
    ck = (entry.tg_bot_id, str(path), path.stat().st_mtime)
    if ck in _cache:
        return _cache[ck]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] rich %s: файл не читается: %s", u.slug, step.key, e)
        return None
    blocks = copy.deepcopy(data.get("blocks") if isinstance(data, dict) else data)
    if not isinstance(blocks, list) or not blocks:
        return None
    service = settings_for(ex, u.tenant_id).service_chat_id
    keep = []
    for b in blocks:
        drop = False
        for field in ("photo", "video", "animation"):
            m = (b or {}).get(field) if isinstance(b, dict) else None
            if not isinstance(m, dict):
                continue
            src = m.get("media") or ""
            if not src or src.startswith(("http://", "https://")):
                continue
            fid = await _file_id(ex, entry, service, src, launch, field)
            if fid:
                m["media"] = fid
            else:
                drop = True
        if not drop:
            keep.append(b)
    if keep:
        _cache[ck] = keep
    return keep or None


async def send_rich(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step, job,
                    out: "Outcome") -> bool:
    """Отправка статьи с идемпотентностью по задаче. → True, если заблокировал бота."""
    prev = await sent_row(ex, job["id"])
    if prev is not None:
        out.act("resend_skipped", step, payload=prev["status"])
        return False
    entry = ex.rt.bot_for_tenant(u.tenant_id)
    if entry is None:
        out.act("send_error", step, payload="у запуска нет бота")
        return False
    text, kb = ex.render(u, g, step)
    blocks = await load_blocks(ex, u, g, step, entry)
    sent_id = await begin(ex, u, g, step, job)
    mids: list[int] = []
    err = None
    note = None
    try:
        if blocks:
            try:
                msg = await entry.bot(SendRichMessage(chat_id=u.uid, rich_message={"blocks": blocks},
                                                      reply_markup=kb))
                if getattr(msg, "message_id", None):
                    mids.append(msg.message_id)
            except TelegramForbiddenError:
                raise
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
        else:
            err = "блоки статьи не собрались"
        if not mids and (text or "").strip():
            m = await entry.bot.send_message(u.uid, text, reply_markup=kb)
            mids.append(m.message_id)
            note = f"статья → текст ({err})"
    except TelegramForbiddenError as e:
        await finish(ex, sent_id, ok=False, error=str(e))
        mark_blocked(u, step, out, str(e))
        return True
    except Exception as e:  # noqa: BLE001
        err = f"{err + '; ' if err else ''}{type(e).__name__}: {e}"
    ok = bool(mids)
    await finish(ex, sent_id, ok=ok, message_ids=mids, error=None if ok and not note else (note or err))
    if not ok:
        log.warning("[%s] %s: статья %s не ушла (%s) – цепочка продолжается", u.slug, u.uid, step.key, err)
        out.act("send_error", step, payload=err)
    else:
        log.info("[%s] %s: ушла статья %s %s", u.slug, u.uid, step.key, note or "")
        if note:
            out.act("rich_fallback", step, payload=note)
    return False
