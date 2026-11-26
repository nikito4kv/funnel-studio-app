"""Общий протокол гейтов: факт, источник, политика при отсутствии данных.

Каждое условие шага сводится к ФАКТУ о человеке («оплатил ВИП», «был на эфире
дня N», «кликнул ссылку с меткой X», …) и к направлению:

  * «положительный» гейт – шаг только для тех, у кого факт ЕСТЬ (`paid`,
    `course_paid`, `attendance: was`, «кликнул», `has_lm`). Нет данных
    (источник не настроен, упал, отчёта ещё нет) → **fail-safe**: шаг
    пропускается. Иначе «только для оплативших» уйдёт всем.
  * «отрицательный» гейт – дожим тем, у кого факта НЕТ (`not_paid`,
    `not_course_paid`, «не был», «не кликнул», «не прошёл опрос», «не написал
    менеджеру»). Нет данных → **fail-open**: шаг уходит (§24: лишний дожим
    дешевле молчания).

Факт – тройное состояние: True / False / None (не знаем). Источник обязан
отличать «точно нет» от «не знаю»: «оплат нет» при ненастроенной кассе – это
None, а не False.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Fact:
    """Факт о человеке для одного условия шага."""
    gate: str            # имя условия: paid / not_paid / attendance=was / no_link_click=… / …
    positive: bool       # True – шаг только для тех, у кого факт есть
    state: bool | None   # есть ли факт; None – данных нет
    source: str          # payments / bizon / clicks / none
    note: str = ""       # почему нет данных или чем подтверждён факт

    @property
    def send(self) -> bool:
        if self.positive:
            return self.state is True
        return self.state is not True

    @property
    def policy(self) -> str:
        """Как решили: checked – по данным; fail-safe / fail-open – без данных."""
        if self.state is not None:
            return "checked"
        return "fail-safe" if self.positive else "fail-open"

    def reason(self) -> str:
        """Причина пропуска – в actions и лог (только когда шаг не уходит)."""
        if self.state is None:
            return f"{self.gate}: нет данных ({self.note or self.source}) – шаг только для «{self.gate}», fail-safe"
        return f"{self.gate}: {self.note or ('да' if self.state else 'нет')}"


# ── мелочи, общие для источников ─────────────────────────────────────────────

def phone10(raw) -> str | None:
    """Последние 10 цифр телефона – устойчиво к 7 / 8 / +7 (как _n10 в vip_sync bot-engine)."""
    d = re.sub(r"\D", "", str(raw or ""))
    return d[-10:] if len(d) >= 10 else None


def as_int(v) -> int | None:
    try:
        s = str(v).strip()
        return int(s) if s.lstrip("-").isdigit() else None
    except Exception:  # noqa: BLE001
        return None
