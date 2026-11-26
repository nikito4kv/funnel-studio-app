"""Когда уйдёт шаг: перенос `_schedule_or_fire` + `_compute_absolute_delay_tg`.

Правила – как в движке bot-engine (runner.py):
  * шаг с часами (`absolute_time_msk`) или от эфира (`webinar_offset_minutes`)
    перебивает задержку входящей стрелки: задержка = цель − сейчас;
  * цель в прошлом: `skip_if_past` – шаг НЕ шлётся, сразу его on_done;
    иначе – шлётся через 120 с (анти-каскад: догоняющие шаги не пачкой);
  * `min_delay_seconds` (bot-engine 23.09.2026): шаг со временем уходит не раньше,
    чем через N с от постановки – и для будущего часа, и вместо 120 с для
    прошедшего; пропуск по `skip_if_past` не меняется;
  * якорь времени – эфир человека (из branch_router), а если его нет –
    полночь МСК дня входа (иначе цепочка абсолютных шагов валилась каскадом,
    инцидент 07.07);
  * ночной режим (DND) переносит отложенный шаг из окна на утро, кроме
    `dnd_exempt`; немедленные шаги не трогает;
  * `also_fire` к шагу без часов ставится с голой задержкой, без DND (как
    `_schedule_at_delay` в движке).

Функции чистые: вход – граф, «сейчас», якорь; выход – список задач.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from studio.model.funnel import EdgeKind

from .clock import MSK
from .engine_lib.branch import branch_by_id, resolve_step_target
from .engine_lib.dnd import apply_dnd_to_delay
from .graph import CompiledFunnel, has_schedule
from .repo import JobSpec

log = logging.getLogger("runtime.planner")

PAST_STAGGER_S = 120          # _PAST_STAGGER_S движка
_MAX_SKIP_DEPTH = 64          # страховка от цикла из пропущенных шагов


@dataclass
class PlanCtx:
    now: datetime                       # UTC, aware
    webinar_at: datetime | None         # эфир человека (aware) или None
    entered_at: datetime                # вход человека (aware) – якорь, если эфира нет
    branch_id: str | None
    dnd: object                         # DndSettings
    notes: list[tuple[str, uuid.UUID, str]] = field(default_factory=list)  # (что, шаг, ключ)

    def anchor_msk(self) -> datetime:
        if self.webinar_at is not None:
            return self.webinar_at.astimezone(MSK)
        return self.entered_at.astimezone(MSK).replace(hour=0, minute=0, second=0, microsecond=0)


def step_target(g: CompiledFunnel, step, ctx: PlanCtx) -> datetime | None:
    """Момент шага по часам/эфиру (aware) или None, если шаг идёт по стрелке."""
    if not has_schedule(step):
        return None
    branch = branch_by_id(g.router, ctx.branch_id)
    return resolve_step_target(step.schedule, ctx.anchor_msk(), branch)


def plan(g: CompiledFunnel, step_id: uuid.UUID | None, delay_s: int, ctx: PlanCtx, *,
         kind: str = "fire", reason: str = "", _depth: int = 0) -> list[JobSpec]:
    """Задачи, чтобы шаг `step_id` ушёл через `delay_s` (с правилами времени).

    Шаг с часами в прошлом и `skip_if_past` не ставится – вместо него
    планируется его `on_done` (рекурсивно, как в движке).
    """
    if step_id is None:
        return []
    step = g.step(step_id)
    if step is None:
        ctx.notes.append(("unknown_step", step_id, g.key_of(step_id)))
        log.warning("стрелка ведёт в шаг, которого нет в версии v%s: %s", g.version, step_id)
        return []
    delay = max(0, int(delay_s or 0))
    target = step_target(g, step, ctx)
    absolute = target is not None
    if absolute:
        d = int((target - ctx.now).total_seconds())
        if d <= 0:
            if step.schedule.skip_if_past:
                ctx.notes.append(("skipped_past", step.id, step.key))
                if _depth >= _MAX_SKIP_DEPTH:
                    return []
                done = g.edge(step.id, EdgeKind.done)
                if done is None:
                    return []
                return plan(g, done.to_step, done.delay_seconds or 0, ctx,
                            kind="fire", reason=f"после пропуска {step.key}", _depth=_depth + 1)
            d = max(PAST_STAGGER_S, step.schedule.min_delay_seconds or 0)
        else:
            d = max(d, step.schedule.min_delay_seconds or 0)
        delay = d
    if kind != "also" or absolute:
        delay = apply_dnd_to_delay(delay, ctx.dnd, step.schedule.dnd_exempt, ctx.now)
    return [JobSpec(step_id=step.id, step_key=step.key, kind=kind,
                    run_at=ctx.now + timedelta(seconds=delay), reason=reason)]


def plan_edge(g: CompiledFunnel, step, kind: EdgeKind, ctx: PlanCtx, *, delay_override: int | None = None,
              reason: str | None = None) -> list[JobSpec]:
    e = g.edge(step.id, kind)
    if e is None or e.to_step is None:
        return []
    d = e.delay_seconds if delay_override is None else delay_override
    return plan(g, e.to_step, d or 0, ctx, reason=reason or f"{kind.value} из {step.key}")


def plan_also(g: CompiledFunnel, step, ctx: PlanCtx) -> list[JobSpec]:
    out: list[JobSpec] = []
    for e in g.edges(step.id, EdgeKind.also_fire):
        out += plan(g, e.to_step, e.delay_seconds or 0, ctx, kind="also",
                    reason=f"параллельно из {step.key}")
    return out
