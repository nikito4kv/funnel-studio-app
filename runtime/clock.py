"""Часы движка.

Всё время внутри – tz-aware UTC; в логах и отчётах – МСК. Движок берёт «сейчас»
только отсюда (а не из datetime.now() и не из now() базы), поэтому тесты
перематывают время управляемыми часами и проверяют расписание за минуты
вместо суток.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytz

MSK = pytz.timezone("Europe/Moscow")
UTC = timezone.utc


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock(Clock):
    """Управляемые часы для тестов: стоят, пока их не подвинут."""

    def __init__(self, start: datetime):
        if start.tzinfo is None:
            raise ValueError("FakeClock: нужно время с часовым поясом")
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def set(self, t: datetime) -> None:
        self._now = t.astimezone(UTC)

    def advance(self, seconds: float = 0, **kw) -> datetime:
        self._now = self._now + timedelta(seconds=seconds, **kw)
        return self._now


def msk(t: datetime | None) -> str:
    """Человеку – всегда МСК (CLAUDE.md bot-engine: «Время везде МСК»)."""
    if t is None:
        return "—"
    return t.astimezone(MSK).strftime("%d.%m.%Y %H:%M:%S МСК")
