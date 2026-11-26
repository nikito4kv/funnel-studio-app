"""Поля bot-engine 23.09.2026 в движке Студии – та же семантика, что в runner.py / reg_window.py.

  * `schedule.min_delay_seconds` (min_delay_seconds) – шаг со временем не раньше, чем через
    N с от постановки; для прошедшего часа при «всё равно отправить» – вместо 120 с;
  * `conditions.registered_windows` (skip_if_registered_msk) – регистрация (последняя
    отправка входного шага) в окнах МСК: шаг пропускается, цепочка идёт дальше;
  * `Funnel.resend_entry_on_repeat` – повторный /start в окне анти-дубля шлёт первое
    сообщение ещё раз, таймеры не трогает.
"""
from __future__ import annotations

import pytest

from rt_helpers import mk_funnel

pytestmark = pytest.mark.asyncio


async def sent(env, uid):
    async with env.pool.acquire() as c:
        return [(r["step_key"], r["created_at"]) for r in await c.fetch(
            "SELECT step_key, created_at FROM sent_messages WHERE uid=$1 AND status='ok' ORDER BY id", uid)]


async def setup(env, funnel, settings=None, slug="t1"):
    await env.tenant(slug, settings=settings)
    await env.start()
    await env.publish(slug, funnel)


async def test_min_delay_seconds(env):
    # часы теста – 12:00 МСК
    f = mk_funnel("f", [
        {"key": "a"},
        # 11:00 прошло, «всё равно отправить»: не через 120 с, а через 360
        {"key": "past", "schedule": {"absolute_time_msk": "11:00", "skip_if_past": False,
                                     "min_delay_seconds": 360}},
        # 12:02 впереди, но раньше 360 с от постановки нельзя
        {"key": "soon", "schedule": {"absolute_time_msk": "12:02", "min_delay_seconds": 360}},
        # 15:00 далеко – как было
        {"key": "later", "schedule": {"absolute_time_msk": "15:00", "min_delay_seconds": 360}},
        # 11:30 прошло и skip_if_past – выпадает, min_delay этого не меняет
        {"key": "gone", "schedule": {"absolute_time_msk": "11:30", "min_delay_seconds": 360}},
    ], [("a", "done", "past", 0), ("a", "also_fire", "soon", 0), ("a", "also_fire", "later", 0),
        ("a", "also_fire", "gone", 0)])
    await setup(env, f)
    t0 = env.clock.now()
    await env.start_user("t1", 1)
    await env.run_for(4 * 3600)
    got = {k: round((t - t0).total_seconds()) for k, t in await sent(env, 1)}
    assert got == {"a": 0, "past": 360, "soon": 360, "later": 3 * 3600}


async def test_registered_windows_gate(env):
    f = mk_funnel("f", [
        {"key": "a"},
        {"key": "b", "text": "не тем, кто пришёл 11–13", "conditions": {"registered_windows": ["11:00-13:00"]}},
        {"key": "c", "text": "не тем, кто пришёл 13–14", "conditions": {"registered_windows": ["13:00-14:00"]}},
    ], [("a", "done", "b", 60), ("b", "done", "c", 60)])
    await setup(env, f)
    await env.start_user("t1", 1)          # 12:00 – в окне b
    await env.run_for(600)
    assert [k for k, _ in await sent(env, 1)] == ["a", "c"]
    async with env.pool.acquire() as c:
        why = await c.fetchval("SELECT payload FROM actions WHERE action='gated_out' AND step_key='b'")
    assert why.startswith("registered_msk:") and "12:00" in why

    # повторный /start в 13:30 – отсчёт от него (последняя отправка входного шага)
    await env.run_for(5400 - 600)
    assert await env.start_user("t1", 1) == "started"
    await env.run_for(600)
    assert [k for k, _ in await sent(env, 1)] == ["a", "c", "a", "b"]


async def test_resend_entry_on_repeat(env):
    edges = [("a", "done", "b", 25), ("b", "done", "c", 600)]
    f = mk_funnel("f", [{"key": "a"}, {"key": "b"}, {"key": "c"}], edges, resend_entry_on_repeat=True)
    await setup(env, f)
    t0 = env.clock.now()
    await env.start_user("t1", 1)
    await env.run_for(10)
    # нетерпеливый /start через 10 с: первое сообщение ещё раз, таймеры живы
    assert await env.start_user("t1", 1) == "resent"
    await env.run_for(3600)
    got = [(k, round((t - t0).total_seconds())) for k, t in await sent(env, 1)]
    assert got == [("a", 0), ("a", 10), ("b", 25), ("c", 625)]
    async with env.pool.acquire() as c:
        acts = [r["action"] for r in await c.fetch("SELECT action FROM actions WHERE uid=1 ORDER BY id")]
    assert "start_ignored" in acts and "entry_resent" in acts
    assert acts.count("step") == 3, "повтор – не шаг: анти-дубль и позиция не тронуты"
