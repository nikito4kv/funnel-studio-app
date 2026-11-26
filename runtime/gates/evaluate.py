"""Проверка условий шага по источникам данных – одна точка вызова из executor.

    skip = await gates.evaluate(rt, u, step, g, out)   # → причина пропуска или None

Порядок и политика – см. base.py: каждое условие → Fact (True/False/None),
положительные без данных пропускают шаг (fail-safe), отрицательные без данных
шаг отправляют (fail-open). В actions:
  * `gate_failsafe`  – шаг пропущен, потому что данных нет;
  * `gate_unchecked` – шаг ушёл, хотя условие проверить не смогли (fail-open);
  * причина пропуска по данным пишется executor'ом как `gated_out`.
Канал (getChatMember) проверяет сам executor – здесь его нет.

Час регистрации (`conditions.registered_windows`, `skip_if_registered_msk` bot-engine):
регистрация – последняя отправка входного шага воронки (sent_messages); попала в
окна МСК – шаг пропускается. Не нашли – шаг уходит (fail-open), как в движке.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from studio.engine_ref.reg_window import in_windows

from .. import repo
from ..clock import MSK
from ..engine_lib.branch import branch_by_id
from ..steps import survey
from . import links, payments
from .base import Fact
from .bizon import BizonSource, step_window

if TYPE_CHECKING:
    from ..service import Runtime

log = logging.getLogger("runtime.gates")


class Gates:
    """Состояние источников в процессе движка (кэши, замки, фабрики клиентов)."""

    def __init__(self, rt: "Runtime"):
        self.rt = rt
        self.bizon = BizonSource(rt)


def gates_of(rt: "Runtime") -> Gates:
    g = rt.__dict__.get("_gates")
    if g is None:
        g = rt.__dict__["_gates"] = Gates(rt)
    return g


def attendance_room(step, funnel, settings, platform: str) -> str:
    """Зал для гейта: шага (MAX-зал шага для MAX) → воронки → запуска."""
    c = step.conditions
    if platform == "max" and c.bizon_room_max:
        return c.bizon_room_max
    if c.bizon_room:
        return c.bizon_room
    if funnel.webinar_room is not None and funnel.webinar_room.bizon_room:
        return funnel.webinar_room.bizon_room
    if platform == "max" and settings.bizon.room_max:
        return settings.bizon.room_max
    return settings.bizon.room


async def facts(rt: "Runtime", u, step, g) -> list[Fact]:
    """Все условия шага (кроме канала) → факты."""
    c = step.conditions
    t = rt.registry.tenant(u.tenant_id)
    phone = u.participant.get("phone")
    out: list[Fact] = []

    async def safe(fn, *a, **kw):
        try:
            return await fn(*a, **kw)
        except Exception as e:  # noqa: BLE001 – источник упал = данных нет, решает политика
            log.warning("[%s] %s: источник гейта упал: %s", t.slug, u.uid, e)
            return None

    # ── час регистрации ── (в движке bot-engine – первым из гейтов шага)
    if c.registered_windows:
        async with rt.pool.acquire() as conn:
            reg = await safe(repo.registered_at, conn, tenant_id=u.tenant_id, platform=u.platform,
                             uid=u.uid, funnel_id=g.funnel_id, step_ids=g.entry_step_ids())
        windows = ", ".join(c.registered_windows)
        if reg is None:
            st, note = None, "время регистрации не найдено"
        else:
            hm = reg.astimezone(MSK).strftime("%H:%M")
            st = in_windows(reg.astimezone(MSK), c.registered_windows)
            note = (f"зарегистрировался в {hm} МСК – в окне {windows}" if st
                    else f"зарегистрировался в {hm} МСК – вне окон {windows}")
        out.append(Fact("registered_msk", False, st, "sent_messages", note))

    # ── оплаты ──
    for name, positive, product in (("paid", True, "vip"), ("not_paid", False, "vip"),
                                    ("course_paid", True, "course"),
                                    ("not_course_paid", False, "course")):
        if not getattr(c, name):
            continue
        async with rt.pool.acquire() as conn:
            st = await safe(payments.is_paid, conn, t, u.platform, u.uid, phone, product)
        note = ("касса у запуска не настроена" if not t.settings.payments.configured()
                or not t.settings.payments.tariffs(product) else
                "оплата найдена" if st else "оплаты нет" if st is False else "касса: ошибка")
        out.append(Fact(name, positive, st, "payments", note))

    # ── эфир ──
    if c.attendance:
        room = attendance_room(step, g.funnel, t.settings, u.platform)
        branch = branch_by_id(g.router, u.position.get("branch"))
        window = step_window(u.position.get("webinar_at"), step, branch, hours=c.gate_window_hours,
                             lead_minutes=t.settings.bizon.window_lead_minutes)
        st, note = None, ""
        if t.settings.bizon.count_link_clicks:
            async with rt.pool.acquire() as conn:
                if await safe(links.clicked_room, conn, t, u.platform, u.uid, room, window):
                    st, note = True, "переходил по ссылке эфира"
        if st is None:
            res = await safe(gates_of(rt).bizon.attended, t, room, window, u.uid, phone)
            st, note = res if res is not None else (None, "Bizon: ошибка")
        out.append(Fact(f"attendance={c.attendance}", c.attendance == "was", st, "bizon", note))

    # ── клики ──
    # «кликнул» (положительный) – поле Студии `conditions.link_click` (в bot-engine такого нет)
    for name, prefix, positive in (("link_click", c.link_click, True),
                                   ("no_link_click", c.no_link_click, False)):
        if not prefix:
            continue
        async with rt.pool.acquire() as conn:
            st = await safe(links.clicked, conn, t, u.platform, u.uid, prefix)
        note = ("ссылки движка у запуска не включены" if not t.settings.links.configured()
                else "кликнул" if st else "не кликал" if st is False else "клики: ошибка")
        out.append(Fact(f"{name}={prefix}", positive, st, "clicks", note))

    # ── условия без источника в движке (пока) ──
    if c.has_lm:
        out.append(Fact("has_lm", True, None, "none", "источника лидмагнитов в движке нет"))
    # ── опрос: не слать прошедшим (runtime/steps/survey – ответы в survey_responses) ──
    if c.survey_done:
        async with rt.pool.acquire() as conn:
            st = await safe(survey.is_done, conn, u.tenant_id, u.platform, u.uid)
        note = "опрос пройден" if st else "опрос не пройден" if st is False else "опрос: ошибка"
        out.append(Fact("survey_done", False, st, "survey", note))
    if c.no_manager_dm:
        out.append(Fact("no_manager_dm", False, None, "none", "источника «написал менеджеру» нет"))
    return out


async def evaluate(rt: "Runtime", u, step, g, out) -> str | None:
    """→ причина пропуска шага или None (шлём). Пишет fail-safe / fail-open в actions."""
    fs = await facts(rt, u, step, g)
    t = rt.registry.tenant(u.tenant_id)
    opened = [f for f in fs if f.policy == "fail-open"]
    if opened:
        payload = "; ".join(f"{f.gate} ({f.note})" for f in opened)
        out.act("gate_unchecked", step, payload=payload)
        log.info("[%s] %s: шаг %s – не проверено, fail-open (дожим уходит): %s",
                 t.slug, u.uid, step.key, payload)
    for f in fs:
        if f.send:
            continue
        if f.policy == "fail-safe":
            out.act("gate_failsafe", step, payload=f"{f.gate} ({f.note})")
            log.info("[%s] %s: шаг %s – нет данных для «%s» (%s), fail-safe: не шлём",
                     t.slug, u.uid, step.key, f.gate, f.note)
        return f.reason()
    return None
