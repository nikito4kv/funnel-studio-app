"""Фикстура env для тестов движка (помощники – в rt_helpers.py)."""
from __future__ import annotations

from datetime import datetime

import asyncpg
import pytest

from rt_helpers import TABLES, TEST_DSN, Env, FakeTelegram, _ensure_db
from runtime.clock import MSK, FakeClock
from runtime.db import _init_conn


@pytest.fixture
async def env(tmp_path, monkeypatch):
    if not await _ensure_db():
        pytest.skip("Postgres для тестов недоступен (STUDIO_TEST_DSN / контейнер fs-runtime-pg)")
    pool = await asyncpg.create_pool(TEST_DSN, min_size=1, max_size=12,
                                     init=_init_conn)
    async with pool.acquire() as c:
        await c.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
    clock = FakeClock(MSK.localize(datetime(2026, 9, 10, 12, 0)))
    tg = FakeTelegram(clock)
    e = Env(pool, clock, tg, tmp_path, monkeypatch)
    try:
        yield e
    finally:
        if e.rt is not None:
            await e.rt.close()
        await pool.close()
