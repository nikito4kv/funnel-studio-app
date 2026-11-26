"""Шаг «звонок» (type: call) – перенос _handle_call движка bot-engine.

Как в движке: позвонили (или не смогли) – дальше on_done и also_fire.
Ошибка провайдера, нет телефона, провайдер не настроен – шаг пропускается с
записью в actions, цепочка идёт дальше (главное правило, инцидент 15.05).

Идемпотентность: попытка пишется в sent_messages по задаче ДО вызова
провайдера. Процесс упал посреди звонка – повтор задачи второй раз не
позвонит (лучше недозвон, чем два звонка человеку).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from studio.model.funnel import EdgeKind, Step

from ..planner import plan_also, plan_edge
from . import providers
from .common import begin, finish, sent_row, settings_for

if TYPE_CHECKING:
    from ..executor import Executor, UserCtx
    from ..graph import CompiledFunnel
    from ..repo import Outcome

log = logging.getLogger("runtime.steps.call")

_WHAT = {"call": "звонок", "sms": "SMS"}


async def provider_step(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step, job,
                        out: "Outcome", channel: str,
                        do: Callable[[object, str], Awaitable[providers.ProviderResult]],
                        *, what: str = "", data: dict | None = None) -> None:
    """Общий ход звонка и SMS: провайдер → телефон → вызов → запись итога."""
    name = _WHAT[channel]
    if await sent_row(ex, job["id"]) is not None:
        log.warning("[%s] %s: %s по шагу %s уже был (повтор задачи) – не повторяем",
                    u.slug, u.uid, name, step.key)
        out.act("resend_skipped", step, payload=channel)
        return
    st = settings_for(ex, u.tenant_id)
    cfg = st.telephony if channel == "call" else st.sms
    prov, why = providers.build(channel, cfg)
    if prov is None:
        log.info("[%s] %s: %s по шагу %s не отправлен – %s", u.slug, u.uid, name, step.key, why)
        out.act(f"{channel}_skipped", step, payload=why)
        return
    phone = providers.norm_phone(u.participant.get("phone"))
    if not phone:
        log.info("[%s] %s: %s по шагу %s – нет телефона", u.slug, u.uid, name, step.key)
        out.act(f"{channel}_skipped", step, payload="нет телефона")
        return
    if not what:
        out.act(f"{channel}_skipped", step, payload="пустой шаг: нечего отправлять")
        return
    sent_id = await begin(ex, u, g, step, job)
    try:
        res = await do(prov, phone)
    except Exception as e:  # noqa: BLE001 – провайдер упал: цепочку не рвём
        res = providers.ProviderResult(False, f"{type(e).__name__}: {e}")
    await finish(ex, sent_id, ok=res.ok, error=None if res.ok else res.detail)
    payload = f"{getattr(prov, 'name', '?')} {providers.mask_phone(phone)}: {res.detail}"
    if res.ok:
        log.info("[%s] %s: %s по шагу %s – ок (%s)", u.slug, u.uid, name, step.key, payload)
        out.act(channel, step, payload=payload, **(data or {}))
    else:
        log.warning("[%s] %s: %s по шагу %s не удался (%s) – цепочка дальше",
                    u.slug, u.uid, name, step.key, payload)
        out.act(f"{channel}_error", step, payload=payload, **(data or {}))


async def fire_call(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step, job, ctx,
                    out: "Outcome") -> None:
    cs = step.call
    what = cs.audio_url or (str(cs.audio_id) if cs.audio_id else "") or (cs.text or "")

    async def do(prov, phone):
        return await prov.dial(phone, audio_url=cs.audio_url, audio_id=cs.audio_id, text=cs.text)

    await provider_step(ex, u, g, step, job, out, "call", do, what=what)
    out.specs += plan_edge(g, step, EdgeKind.done, ctx)
    out.specs += plan_also(g, step, ctx)
