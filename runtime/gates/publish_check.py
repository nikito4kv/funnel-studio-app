"""Проверка перед публикацией: условия шагов против источников данных запуска.

Публикацию это не блокирует – движок и без источника ведёт себя безопасно
(положительный гейт пропускает шаг, дожим уходит), – но человек, нажимающий
«Опубликовать», должен увидеть это заранее, а не узнать от клиентов.
"""
from __future__ import annotations

from collections import defaultdict

from studio.model.funnel import Funnel, StepType

from ..settings import TenantSettings


def _names(keys: list[str]) -> str:
    keys = sorted(set(keys))
    head = ", ".join(f"«{k}»" for k in keys[:6])
    return head + (f" и ещё {len(keys) - 6}" if len(keys) > 6 else "")


def gate_warnings(funnel: Funnel, s: TenantSettings) -> list[str]:
    never: dict[str, list[str]] = defaultdict(list)    # причина → шаги, которые не уйдут никому
    always: dict[str, list[str]] = defaultdict(list)   # причина → дожимы, которые уйдут всем
    pay_off = {p: not (s.payments.configured() and s.payments.tariffs(p)) for p in ("vip", "course")}
    pay_why = {"vip": "не настроен источник оплат ВИПа (payments: enabled, secret, vip_tariffs)",
               "course": "не настроен источник оплат курса (payments: enabled, secret, course_tariffs)"}
    for st in funnel.steps:
        if st.type == StepType.jump:
            continue
        c, k = st.conditions, st.key
        if c.paid and pay_off["vip"]:
            never[pay_why["vip"]].append(k)
        if c.course_paid and pay_off["course"]:
            never[pay_why["course"]].append(k)
        if c.not_paid and pay_off["vip"]:
            always[pay_why["vip"]].append(k)
        if c.not_course_paid and pay_off["course"]:
            always[pay_why["course"]].append(k)
        if c.attendance:
            room = (c.bizon_room or c.bizon_room_max
                    or (funnel.webinar_room.bizon_room if funnel.webinar_room else "")
                    or s.bizon.room or s.bizon.room_max)
            why = ("не настроен Bizon (bizon: enabled, token)" if not s.bizon.configured()
                   else "не задан зал Bizon (у шага, воронки или bizon.room)" if not room else "")
            if why:
                (never if c.attendance == "was" else always)[why].append(k)
        link_off = "не включены ссылки движка (links: enabled, base_url) – кликов не видно"
        if c.no_link_click and not s.links.configured():
            always[link_off].append(k)
        if c.link_click and not s.links.configured():
            never[link_off].append(k)
        if c.has_lm:
            never["источника лидмагнитов в движке нет"].append(k)
        if c.no_manager_dm:
            always["источника «написал менеджеру» в движке нет"].append(k)
    out = []
    for why, keys in never.items():
        out.append(f"шаги {_names(keys)} требуют данных, а их нет: {why}. "
                   f"Они НЕ уйдут никому (fail-safe)")
    for why, keys in always.items():
        out.append(f"дожимы {_names(keys)} не смогут проверить условие: {why}. "
                   f"Они уйдут ВСЕМ (fail-open)")
    return out
