"""Общее для видов шагов: отправка через исполнителя, идемпотентность по задаче,
пометка «заблокировал бота», правка сообщения на месте (survey.edit)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from studio.model.funnel import Step, StepType

from .. import repo
from .config import StepsSettings, steps_settings

if TYPE_CHECKING:
    from ..executor import Executor, UserCtx
    from ..graph import CompiledFunnel
    from ..repo import Outcome

log = logging.getLogger("runtime.steps")

EDIT_TTL = timedelta(hours=1)   # «заменить сообщение вопроса» живёт недолго


@dataclass
class Delivery:
    ok: bool = False
    blocked: bool = False
    message_id: int | None = None     # последнее сообщение шага (на нём кнопки)
    message_ids: tuple[int, ...] = ()
    rerun: bool = False               # задача выполняется повторно, отправка уже была
    edited: bool = False
    ext_id: str | None = None         # MAX: id последнего сообщения шага (строка «mid.…»)


def _ext_last(row) -> str | None:
    ids = list(row["ext_message_ids"] or []) if row is not None else []
    return ids[-1] if ids else None


def settings_for(ex: "Executor", tenant_id: int) -> StepsSettings:
    try:
        return steps_settings(ex.rt.registry.tenant(tenant_id).settings)
    except Exception as e:  # noqa: BLE001 – кривые настройки не должны ронять шаг
        log.error("настройки видов шагов запуска %s не разобрались: %s – беру умолчания",
                  tenant_id, e)
        return StepsSettings()


def static_launch(ex: "Executor", tenant_id: int, g: "CompiledFunnel") -> str:
    t = ex.rt.registry.tenant(tenant_id)
    return t.settings.static_launch or (g.funnel.source.launch if g.funnel.source else "")


def mark_blocked(u: "UserCtx", step: Step, out: "Outcome", err: str) -> None:
    """Как executor._send: человек заблокировал бота – стоп ему во всём запуске."""
    log.info("[%s] %s заблокировал бота – задачи человека отменены", u.slug, u.uid)
    out.act("blocked", step, payload=err)
    out.participant.update(blocked=True, blocked_at=u.now)
    out.cancel_user_everywhere = "человек заблокировал бота"


async def sent_row(ex: "Executor", job_id: int):
    async with ex.rt.pool.acquire() as conn:
        return await repo.sent_for_job(conn, job_id)


async def begin(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step, job) -> int:
    async with ex.rt.pool.acquire() as conn:
        return await repo.sent_begin(conn, tenant_id=u.tenant_id, platform=u.platform, uid=u.uid,
                                     funnel_id=g.funnel_id, version=g.version, step=step,
                                     job_id=job["id"], now=u.now)


async def finish(ex: "Executor", sent_id: int, *, ok: bool, message_ids=(), error: str | None = None,
                 ext_ids=None):
    async with ex.rt.pool.acquire() as conn:
        await repo.sent_finish(conn, sent_id, ok=ok, message_ids=list(message_ids), error=error,
                               now=ex.rt.clock.now(),
                               ext_message_ids=list(ext_ids) if ext_ids else None)


def text_only(step: Step) -> bool:
    """Шаг без медиа – его можно поставить на место текстового сообщения."""
    m = step.media
    return step.type == StepType.text and not (m.image or m.images or m.video or m.animation
                                               or m.video_note or m.rich)


def has_edit_steps(g: "CompiledFunnel") -> bool:
    v = getattr(g, "_steps_has_edit", None)
    if v is None:
        v = any(s.survey.qid and s.survey.edit for s in g.funnel.steps)
        setattr(g, "_steps_has_edit", v)
    return v


async def take_edit_target(ex: "Executor", u: "UserCtx", g: "CompiledFunnel",
                           step: Step) -> int | str | None:
    """Сообщение, которое этот шаг должен заменить (survey.edit), или None.
    Telegram – число, MAX – строка «mid.…»."""
    if not (has_edit_steps(g) and text_only(step)):
        return None
    async with ex.rt.pool.acquire() as conn:
        r = await conn.fetchrow(
            """SELECT message_id, ext_message_id FROM survey_edits WHERE tenant_id=$1 AND platform=$2
                 AND uid=$3 AND funnel_id=$4 AND step_id=$5 AND created_at > $6""",
            u.tenant_id, u.platform, u.uid, g.funnel_id, step.id, u.now - EDIT_TTL)
    if r is None:
        return None
    return r["ext_message_id"] if u.platform == "max" else r["message_id"]


async def drop_edit_target(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step) -> None:
    async with ex.rt.pool.acquire() as conn:
        await conn.execute(
            """DELETE FROM survey_edits WHERE tenant_id=$1 AND platform=$2 AND uid=$3
                 AND funnel_id=$4 AND step_id=$5""", u.tenant_id, u.platform, u.uid, g.funnel_id, step.id)


async def deliver(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step, job,
                  out: "Outcome") -> Delivery:
    """Отправить шаг обычным путём исполнителя – или заменить им сообщение
    прошлого вопроса опроса (survey.edit). → что получилось."""
    prev = await sent_row(ex, job["id"])
    if prev is not None:
        log.warning("[%s] %s: задача %s повторно – %s уже был (%s), не дублируем",
                    u.slug, u.uid, job["id"], step.key, prev["status"])
        out.act("resend_skipped", step, payload=prev["status"])
        mids = tuple(prev["message_ids"] or ())
        return Delivery(ok=prev["status"] == "ok", message_id=mids[-1] if mids else None,
                        message_ids=mids, rerun=True, ext_id=_ext_last(prev))
    mid = await take_edit_target(ex, u, g, step)
    if mid:
        d = await _edit_in_place(ex, u, g, step, job, out, mid)
        await drop_edit_target(ex, u, g, step)
        if d is not None:
            return d
    blocked = await ex._send(u, g, step, job, out)
    if blocked:
        return Delivery(blocked=True)
    row = await sent_row(ex, job["id"])
    mids = tuple(row["message_ids"] or ()) if row else ()
    return Delivery(ok=bool(row and row["status"] == "ok"), message_id=mids[-1] if mids else None,
                    message_ids=mids, ext_id=_ext_last(row))


async def _edit_in_place_max(ex, u, g, step, job, out, mid: str) -> Delivery | None:
    """MAX: `PUT /messages` – текст и клавиатура шага на месте сообщения вопроса."""
    from ..platforms import max_steps
    text, rows = max_steps.render(ex, u, g, step)
    if not (text or "").strip():
        return None
    tenant = ex.rt.registry.tenant(u.tenant_id)
    from ..gates.links import wrap_max_rows
    rows = await wrap_max_rows(ex.rt, tenant, u.uid, step, rows)
    r = await max_steps.edit(ex, u, mid, text, rows)
    if r == "blocked":
        mark_blocked(u, step, out, "MAX: человек заблокировал бота")
        return Delivery(blocked=True)
    if r != "ok":
        return None
    sent_id = await begin(ex, u, g, step, job)
    await finish(ex, sent_id, ok=True, ext_ids=[mid])
    log.info("[%s] MAX %s: шаг %s встал на место сообщения %s (survey.edit)", u.slug, u.uid,
             step.key, mid)
    return Delivery(ok=True, ext_id=mid, edited=True)


async def _edit_in_place(ex, u, g, step, job, out, mid) -> Delivery | None:
    """→ Delivery, или None – не вышло, шлём обычным сообщением."""
    if u.platform == "max":
        return await _edit_in_place_max(ex, u, g, step, job, out, str(mid))
    entry = ex.rt.bot_for_tenant(u.tenant_id)
    if entry is None:
        return None
    text, kb = ex.render(u, g, step)
    if not (text or "").strip():
        return None
    try:
        # правка идемпотентна: упали после неё – повтор получит «not modified»
        await entry.bot.edit_message_text(chat_id=u.uid, message_id=mid, text=text,
                                          reply_markup=kb, disable_web_page_preview=True)
    except TelegramForbiddenError as e:
        mark_blocked(u, step, out, str(e))
        return Delivery(blocked=True)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            log.info("[%s] %s: заменить сообщение %s шагом %s не вышло (%s) – шлём новым",
                     u.slug, u.uid, mid, step.key, e)
            return None
    except Exception as e:  # noqa: BLE001
        log.info("[%s] %s: правка сообщения %s не удалась (%s) – шлём новым", u.slug, u.uid, mid, e)
        return None
    sent_id = await begin(ex, u, g, step, job)
    await finish(ex, sent_id, ok=True, message_ids=[mid])
    log.info("[%s] %s: шаг %s встал на место сообщения %s (survey.edit)", u.slug, u.uid, step.key, mid)
    return Delivery(ok=True, message_id=mid, message_ids=(mid,), edited=True)
