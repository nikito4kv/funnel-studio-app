"""Опубликованная версия воронки в памяти воркера.

Движок читает граф Студии напрямую (studio.model.funnel.Funnel) – без
выгрузки в yaml. Здесь только индексы для быстрых переходов: шаг по id,
стрелки шага по видам.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from studio.model.funnel import Edge, EdgeKind, Funnel, Step, StepType

# Типы шагов, которые фундамент отправляет сам. call / sms / air_pin / rich и
# вопросы опроса исполняет runtime/steps (до этой проверки дело не доходит).
# Остальное – «не поддерживается»: цепочка идёт дальше, запись в actions.
SENDABLE = {StepType.text, StepType.photo, StepType.video, StepType.animation,
            StepType.video_note}
FLOW = {StepType.phone_request, StepType.email_request, StepType.jump}
STEPS_TYPES = {StepType.call, StepType.sms, StepType.air_pin, StepType.rich}
UNSUPPORTED_TYPES: set[StepType] = set()


@dataclass
class CompiledFunnel:
    tenant_id: int
    funnel_id: str
    version: int
    funnel: Funnel
    steps: dict[uuid.UUID, Step] = field(default_factory=dict)
    by_key: dict[str, Step] = field(default_factory=dict)
    out: dict[uuid.UUID, list[Edge]] = field(default_factory=dict)

    @classmethod
    def build(cls, tenant_id: int, version: int, funnel: Funnel) -> "CompiledFunnel":
        cf = cls(tenant_id=tenant_id, funnel_id=funnel.id, version=version, funnel=funnel)
        for s in funnel.steps:
            cf.steps[s.id] = s
            cf.by_key.setdefault(s.key, s)
        for e in funnel.edges:
            cf.out.setdefault(e.from_step, []).append(e)
        return cf

    # ── доступ ──
    def step(self, step_id: uuid.UUID | str | None) -> Step | None:
        if step_id is None:
            return None
        if not isinstance(step_id, uuid.UUID):
            try:
                step_id = uuid.UUID(str(step_id))
            except ValueError:
                return None
        return self.steps.get(step_id)

    def edges(self, step_id: uuid.UUID, kind: EdgeKind) -> list[Edge]:
        return [e for e in self.out.get(step_id, []) if e.kind == kind]

    def edge(self, step_id: uuid.UUID, kind: EdgeKind) -> Edge | None:
        es = self.edges(step_id, kind)
        return es[0] if es else None

    @property
    def first_step(self) -> Step | None:
        return self.funnel.steps[0] if self.funnel.steps else None

    @property
    def router(self):
        return self.funnel.branch_router

    def entry_step_ids(self) -> list[uuid.UUID]:
        """Входные шаги – их отправка и есть «регистрация» (reg_window.entry_step_keys движка):
        входы веток branch_router и когорт entry_variants, а если их нет – первый шаг."""
        ids: list[uuid.UUID] = []
        for b in (self.router.branches if self.router else []):
            if b.entry_step not in ids:
                ids.append(b.entry_step)
        for ev in self.funnel.entry_variants:
            if ev.entry_step not in ids:
                ids.append(ev.entry_step)
        if not ids and self.first_step is not None:
            ids.append(self.first_step.id)
        return ids

    def key_of(self, step_id) -> str:
        s = self.step(step_id)
        if s is not None:
            return s.key
        for u in self.funnel.unresolved:
            if u.id == step_id:
                return u.key
        return str(step_id)[:8]


def has_schedule(step: Step) -> bool:
    """Время шага задаётся часами или эфиром (перебивает задержку стрелки)."""
    sc = step.schedule
    return bool(sc.absolute_time_msk) or sc.webinar_offset_minutes is not None


def unsupported_reason(step: Step, funnel: CompiledFunnel) -> str | None:
    """Почему фундамент не исполняет шаг сам (None – исполняет)."""
    if step.type in UNSUPPORTED_TYPES:
        return f"тип «{step.type.value}»"
    if step.survey.qid:
        return "вопрос опроса"
    return None


def cohort_matches(rule, ctx: dict[str, str], platform: str) -> bool:
    """Условие когорты (CohortMatch Студии) – перенос variants.matches из bot-engine."""
    if rule.platforms and platform not in rule.platforms:
        return False
    for fld in ("utm_source", "utm_medium", "utm_campaign", "trigger", "lm_post_id"):
        want = getattr(rule, fld, None)
        if want is None:
            continue
        if (ctx.get(fld) or "").strip().lower() != want.strip().lower():
            return False
    if rule.lm_post_id_in:
        cur = (ctx.get("lm_post_id") or "").strip().lower()
        if cur not in {str(v).strip().lower() for v in rule.lm_post_id_in}:
            return False
    return True
