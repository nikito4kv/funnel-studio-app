"""Часы симуляции.

Скопированные хелперы движка (engine/dnd.py, engine/branch.py) спрашивают
«который час» через `datetime.now(tz)`. Здесь тот же `datetime`, но `now()`
отдаёт время, выставленное симулятором для текущего потока (`use(...)`), –
так логика движка остаётся дословной, а время – модельным.

Вне симуляции (часы не выставлены) ведёт себя как обычный datetime.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime as _dt
from typing import Iterator

_local = threading.local()


class datetime(_dt):  # noqa: N801 – подменяет стандартное имя
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        cur: _dt | None = getattr(_local, "now", None)
        if cur is None:
            return _dt.now(tz)
        if tz is None:
            return cur.replace(tzinfo=None)
        return cur.astimezone(tz)


def set_now(now: _dt | None) -> None:
    """Выставить время симуляции (aware datetime) для текущего потока."""
    _local.now = now


@contextmanager
def use(now: _dt) -> Iterator[None]:
    prev = getattr(_local, "now", None)
    _local.now = now
    try:
        yield
    finally:
        _local.now = prev
