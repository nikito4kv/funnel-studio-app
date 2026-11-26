"""Виды шагов сверх фундамента: опрос, звонок, SMS, закреп эфира, статья (rich),
переход в другую воронку.

Исполнитель (executor.py) зовёт отсюда ровно две точки:
  * `fire(...)` – после гейтов: если шаг наш, он выполнен целиком здесь
    (→ True), иначе исполнитель шлёт его обычным путём;
  * `after_send(...)` – после обычной отправки: закреп эфира из шага
    (`pin.air_pin`), переход в воронку (важнее on_done, как в движке) или
    on_done, затем also_fire.
Событие «нажал вариант ответа» и «ответил текстом» приходят из gateway.py
в `survey.on_callback` / `survey.on_free_text`. Смену состояний закрепа по
часам делает `periodic` – фоновая задача процесса (runner.py).

Главное правило (инцидент 15.05) и здесь: ошибка провайдера, закрепа,
перехода – запись в actions, цепочка идёт дальше.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from studio.model.funnel import EdgeKind, Step, StepType

from ..planner import plan_also, plan_edge
from . import air_pin, call, chain, rich, sms, survey
from .common import deliver, has_edit_steps, sent_row, take_edit_target, text_only

if TYPE_CHECKING:
    from ..executor import Executor, UserCtx
    from ..graph import CompiledFunnel
    from ..repo import Outcome

log = logging.getLogger("runtime.steps")

__all__ = ["fire", "after_send", "periodic", "survey", "air_pin", "chain", "call", "sms", "rich"]


async def fire(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step, job, ctx,
               out: "Outcome") -> bool:
    """Выполнить шаг, если он из этого пакета. → True – выполнен здесь."""
    if step.survey.qid:
        await survey.fire_question(ex, u, g, step, job, ctx, out)
        return True
    if step.type == StepType.call:
        await call.fire_call(ex, u, g, step, job, ctx, out)
        return True
    if step.type == StepType.sms:
        await sms.fire_sms(ex, u, g, step, job, ctx, out)
        return True
    if step.type == StepType.air_pin:
        await air_pin.fire_air_pin(ex, u, g, step, job, ctx, out)
        return True
    if step.type == StepType.rich:
        if u.platform == "max":
            # статей в MAX нет – как в bot-engine уходит текст шага (с его кнопками)
            if await ex._send(u, g, step, job, out):
                return True
            out.act("rich_fallback", step, payload="в MAX статей нет – ушёл текст шага")
        elif await rich.send_rich(ex, u, g, step, job, out):
            return True
        await after_send(ex, u, g, step, job, ctx, out)
        return True
    # survey.edit: текстовый шаг встаёт на место сообщения прошлого вопроса
    if has_edit_steps(g) and text_only(step) and await take_edit_target(ex, u, g, step):
        d = await deliver(ex, u, g, step, job, out)
        if not d.blocked:
            await after_send(ex, u, g, step, job, ctx, out)
        return True
    return False


async def after_send(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step, job, ctx,
                     out: "Outcome") -> None:
    """Хвост обычного шага после отправки (даже если она не удалась)."""
    if step.pin.air_pin and u.platform == "max":
        out.act("air_pin_skipped", step, payload="шапку закрепа в MAX не рисуем: закрепа в диалоге "
                                                  "с ботом нет – шаг ушёл как обычный")
    elif step.pin.air_pin:
        row = await sent_row(ex, job["id"])
        mids = list(row["message_ids"] or []) if row is not None and row["status"] == "ok" else []
        if mids or step.type == StepType.video_note:
            await air_pin.adopt(ex, u, g, step, job, out, mids[-1] if mids else None, len(mids))
    edge = g.edge(step.id, EdgeKind.chain_to_funnel)
    chained = False
    if edge is not None:
        try:
            chained = await chain.run_chain(ex, u, g, step, edge, job, out)
        except Exception as e:  # noqa: BLE001 – переход не удался: цепочка дальше по on_done
            log.exception("[%s] %s: переход из %s не удался", u.slug, u.uid, step.key)
            out.act("chain_failed", step, payload=f"{type(e).__name__}: {e}")
    if not chained:
        out.specs += plan_edge(g, step, EdgeKind.done, ctx)
    out.specs += plan_also(g, step, ctx)


async def periodic(rt, stop: asyncio.Event, every: float = air_pin.REFRESH_EVERY_S) -> None:
    """Фоновая задача процесса: смена состояний закрепа эфира по часам движка."""
    while not stop.is_set():
        try:
            await air_pin.refresh_due(rt)
        except Exception:  # noqa: BLE001
            log.exception("обход закрепов эфира не удался")
        try:
            await asyncio.wait_for(stop.wait(), timeout=every)
        except asyncio.TimeoutError:
            pass
