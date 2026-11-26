"""Семантика шагов – как у движка bot-engine (runner.py), на маленьких графах."""
from __future__ import annotations

from datetime import timedelta

import pytest
from aiogram.exceptions import TelegramBadRequest

from runtime.executor import claim_callback_data
from runtime.worker import Worker

from rt_helpers import SimulatedCrash, forbidden_for, mk_funnel, router_fixed, sid

pytestmark = pytest.mark.asyncio


async def sent_keys(env, uid):
    async with env.pool.acquire() as c:
        return [(r["step_key"], r["status"], r["created_at"]) for r in await c.fetch(
            "SELECT step_key, status, created_at FROM sent_messages WHERE uid=$1 ORDER BY id", uid)]


def rel(rows, t0):
    return [(k, round((t - t0).total_seconds())) for k, _s, t in rows]


async def setup(env, funnel, settings=None, slug="t1"):
    await env.tenant(slug, settings=settings)
    await env.start()
    await env.publish(slug, funnel)


# ── время ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("skip_if_past,expected", [
    (True, [("a", 0), ("c", 60)]),                  # b в прошлом – не шлём, сразу его on_done
    (False, [("a", 0), ("b", 120), ("c", 180)]),    # b в прошлом – шлём через 120 с (анти-каскад)
])
async def test_skip_if_past(env, skip_if_past, expected):
    f = mk_funnel("f", [
        {"key": "a"},
        {"key": "b", "schedule": {"absolute_time_msk": "11:00", "skip_if_past": skip_if_past}},
        {"key": "c"},
    ], [("a", "done", "b", 30), ("b", "done", "c", 60)])
    await setup(env, f)
    t0 = env.clock.now()   # 12:00 МСК – 11:00 уже прошло
    await env.start_user("t1", 1)
    await env.run_for(3600)
    assert rel(await sent_keys(env, 1), t0) == expected
    if skip_if_past:
        async with env.pool.acquire() as c:
            assert await c.fetchval("SELECT count(*) FROM sent_messages WHERE step_key='b'") == 0


async def test_absolute_future_and_day_offset(env):
    f = mk_funnel("f", [
        {"key": "a"},
        {"key": "b", "schedule": {"absolute_time_msk": "15:30"}},
        {"key": "c", "schedule": {"absolute_time_msk": "09:00", "day_offset": 1}},
    ], [("a", "done", "b", 0), ("b", "done", "c", 0)])
    await setup(env, f)
    t0 = env.clock.now()
    await env.start_user("t1", 1)
    await env.run_for(86400 * 2)
    assert rel(await sent_keys(env, 1), t0) == [("a", 0), ("b", 3.5 * 3600), ("c", 21 * 3600)]


async def test_webinar_offset_from_branch_router(env):
    """Шаг «за час до эфира» – от эфира человека из branch_router (фикс-дата)."""
    f = mk_funnel("f", [
        {"key": "a"},
        {"key": "b", "schedule": {"webinar_offset_minutes": -60}},
        {"key": "c", "schedule": {"webinar_offset_minutes": 17}},
    ], [("a", "done", "b", 0), ("b", "done", "c", 0)],
        branch_router=router_fixed("f", "a", "2026-09-11", "11:00"))
    await setup(env, f)
    t0 = env.clock.now()   # 10.09 12:00; эфир 11.09 11:00
    await env.start_user("t1", 1)
    await env.run_for(86400 * 2)
    assert rel(await sent_keys(env, 1), t0) == [("a", 0), ("b", 22 * 3600), ("c", 23 * 3600 + 17 * 60)]
    async with env.pool.acquire() as c:
        pos = await c.fetchrow("SELECT branch, webinar_at FROM positions WHERE uid=1")
    assert pos["branch"] == "M" and pos["webinar_at"] == t0 + timedelta(hours=23)


async def test_dnd_moves_to_morning_and_exempt(env):
    f = mk_funnel("f", [
        {"key": "a"}, {"key": "b"}, {"key": "c", "schedule": {"dnd_exempt": True}},
    ], [("a", "done", "b", 10 * 3600), ("a", "also_fire", "c", 10 * 3600)])
    await setup(env, f, settings={"dnd": {"enabled": True, "from": "21:00", "to": "09:00",
                                          "reschedule_to": "09:01"}})
    t0 = env.clock.now()   # 12:00 + 10 ч = 22:00 – ночь
    await env.start_user("t1", 1)
    await env.run_for(86400)
    got = dict(rel(await sent_keys(env, 1), t0))
    assert got["b"] == 21 * 3600 + 60          # перенесён на 09:01 следующего дня
    assert got["c"] == 10 * 3600               # also_fire без часов – без DND (как в движке)


# ── главное правило: ошибка внутри шага не рвёт цепочку ──────────────────────

async def test_send_error_chain_continues(env):
    f = mk_funnel("f", [{"key": "a"}, {"key": "b"}, {"key": "c"}],
                  [("a", "done", "b", 5), ("b", "done", "c", 5)])
    await setup(env, f)
    env.tg.fail_rules.append(lambda c: TelegramBadRequest(method=None, message="Bad Request: boom")
                             if (c["text"] or "").endswith(" b") else None)
    await env.start_user("t1", 1)
    await env.run_for(60)
    rows = await sent_keys(env, 1)
    assert [(k, s) for k, s, _ in rows] == [("a", "ok"), ("b", "error"), ("c", "ok")]


async def test_exception_inside_step_still_plans_on_done(env, monkeypatch):
    f = mk_funnel("f", [{"key": "a"}, {"key": "b"}], [("a", "done", "b", 5)])
    await setup(env, f)
    orig = env.rt.executor.render

    def boom(u, g, step, **kw):
        if step.key == "a":
            raise RuntimeError("пул соединений кончился")
        return orig(u, g, step, **kw)

    monkeypatch.setattr(env.rt.executor, "render", boom)
    await env.start_user("t1", 1)
    await env.run_for(60)
    assert [k for k, s, _ in await sent_keys(env, 1) if s == "ok"] == ["b"]
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM actions WHERE action='step_error'") == 1


async def test_call_sms_without_provider_do_not_break_chain(env):
    """Звонок и SMS без настроенного провайдера – пропуск с записью, цепочка дальше."""
    f = mk_funnel("f", [{"key": "a"}, {"key": "call1", "type": "call"}, {"key": "sms1", "type": "sms"},
                        {"key": "c"}],
                  [("a", "done", "call1", 5), ("call1", "done", "sms1", 5), ("sms1", "done", "c", 5)])
    await setup(env, f)
    await env.start_user("t1", 1)
    await env.run_for(60)
    assert [k for k, _s, _ in await sent_keys(env, 1)] == ["a", "c"]
    async with env.pool.acquire() as c:
        assert [r["step_key"] for r in await c.fetch(
            "SELECT step_key FROM actions WHERE action IN ('call_skipped', 'sms_skipped') "
            "ORDER BY id")] == ["call1", "sms1"]


async def test_blocked_user_jobs_cancelled(env):
    f = mk_funnel("f", [{"key": "a"}, {"key": "b"}, {"key": "c"}],
                  [("a", "done", "b", 5), ("a", "also_fire", "c", 50)])
    await setup(env, f)
    env.tg.fail_rules.append(lambda c: forbidden_for(1)(c) if (c["text"] or "").endswith(" b") else None)
    await env.start_user("t1", 1)
    await env.run_for(120)
    async with env.pool.acquire() as c:
        part = await c.fetchrow("SELECT blocked FROM participants WHERE uid=1")
        pos = await c.fetchrow("SELECT status FROM positions WHERE uid=1")
        cancelled = await c.fetchval("SELECT count(*) FROM jobs WHERE status='cancelled' AND step_key='c'")
    assert part["blocked"] and pos["status"] == "stopped" and cancelled == 1
    assert [k for k, _s, _ in await sent_keys(env, 1)] == ["a", "b"]


async def test_worker_crash_no_duplicate_after_lease(env):
    """Процесс упал сразу после отправки – задача вернётся после аренды, но
    сообщение второй раз не уйдёт, а цепочка продолжится ровно один раз."""
    f = mk_funnel("f", [{"key": "a"}, {"key": "b"}], [("a", "done", "b", 5)])
    await setup(env, f)
    env.tg.crash_rules.append(lambda c: (c["text"] or "").endswith(" a"))
    await env.start_user("t1", 1)
    with pytest.raises(SimulatedCrash):
        await env.worker.tick()
    env.tg.crash_rules.clear()
    async with env.pool.acquire() as c:
        j = await c.fetchrow("SELECT status, attempts FROM jobs WHERE step_key='a'")
    assert j["status"] == "running"                 # «висит» на упавшем процессе
    w2 = Worker(env.rt, name="w2")
    assert await w2.tick() == 0                     # аренда ещё не истекла – не трогаем
    env.clock.advance(env.rt.cfg.lease_seconds + 1)
    await env.run_for(60, worker=w2)
    a_sends = [c for c in env.tg.sends(1) if (c["text"] or "").endswith(" a")]
    b_sends = [c for c in env.tg.sends(1) if (c["text"] or "").endswith(" b")]
    assert len(a_sends) == 1 and len(b_sends) == 1
    async with env.pool.acquire() as c:
        j = await c.fetchrow("SELECT status, attempts FROM jobs WHERE step_key='a'")
        assert await c.fetchval("SELECT count(*) FROM actions WHERE action='resend_skipped'") == 1
    assert j["status"] == "done" and j["attempts"] == 2


# ── кнопка-клейм ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("keeps", [False, True])
async def test_claim_cancels_pending_done(env, keeps):
    f = mk_funnel("f", [
        {"key": "a", "claim": "Хочу урок"},
        {"key": "lesson"}, {"key": "nudge"},
    ], [("a", "done", "nudge", 3600), ("a", "callback", "lesson", None)])
    f.steps[0].claim = type(f.steps[0].claim)(keeps_chain=keeps)
    await setup(env, f)
    await env.start_user("t1", 1)
    await env.run_for(0)
    btn = env.tg.sends(1)[0]["reply_markup"].inline_keyboard[0][0]
    assert btn.callback_data == claim_callback_data(f.steps[0])
    env.clock.advance(60)
    await env.click("t1", 1, btn.callback_data)
    await env.run_for(7200)
    keys = [k for k, _s, _ in await sent_keys(env, 1)]
    assert keys == (["a", "lesson", "nudge"] if keeps else ["a", "lesson"])


async def test_claim_without_target_answers_ack(env):
    f = mk_funnel("f", [{"key": "a", "claim": "Буду на эфире"}, {"key": "b"}],
                  [("a", "done", "b", 100)])
    f.steps[0].claim = type(f.steps[0].claim)(ack_text="Записали!")
    await setup(env, f)
    await env.start_user("t1", 1)
    await env.run_for(0)
    # без стрелки callback кнопку не показываем (как движок: claim_button_text И on_callback)
    assert env.tg.sends(1)[0]["reply_markup"] is None
    assert await env.click("t1", 1, claim_callback_data(f.steps[0])) == "Записали!"
    await env.run_for(200)
    assert [k for k, _s, _ in await sent_keys(env, 1)] == ["a", "b"]   # таймер не тронут


# ── повторный /start ─────────────────────────────────────────────────────────

async def test_repeat_start_does_not_kill_chain(env):
    f = mk_funnel("f", [{"key": "a"}, {"key": "b"}, {"key": "c"}],
                  [("a", "done", "b", 25), ("b", "done", "c", 600)])
    await setup(env, f)
    t0 = env.clock.now()
    await env.start_user("t1", 1)
    await env.run_for(10)
    # нетерпеливый /start через 10 с: вход только что был, таймер живой – ничего не трогаем
    assert await env.start_user("t1", 1) == "ignored"
    await env.run_for(3600)
    assert rel(await sent_keys(env, 1), t0) == [("a", 0), ("b", 25), ("c", 625)]
    # через час – воронка доехала, /start запускает её заново
    assert await env.start_user("t1", 1) == "started"
    await env.run_for(30)
    assert [k for k, _s, _ in await sent_keys(env, 1)][-2:] == ["a", "b"]


async def test_repeat_start_after_window_restarts_but_chain_survives(env):
    f = mk_funnel("f", [{"key": "a"}, {"key": "b"}, {"key": "c"}],
                  [("a", "done", "b", 25), ("b", "done", "c", 600)])
    await setup(env, f)
    await env.start_user("t1", 1)
    await env.run_for(120)            # a, b ушли; c ждёт
    assert await env.start_user("t1", 1) == "started"    # политика движка: с начала
    await env.run_for(3600)
    keys = [k for k, _s, _ in await sent_keys(env, 1)]
    assert keys == ["a", "b", "a", "b", "c"]
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM jobs WHERE status='pending'") == 0


async def test_repeat_start_policy_keep(env):
    f = mk_funnel("f", [{"key": "a"}, {"key": "b"}], [("a", "done", "b", 3600)])
    await setup(env, f, settings={"restart_policy": "keep"})
    await env.start_user("t1", 1)
    await env.run_for(600)
    assert await env.start_user("t1", 1) == "ignored"
    await env.run_for(3600)
    assert [k for k, _s, _ in await sent_keys(env, 1)] == ["a", "b"]


# ── гейты ────────────────────────────────────────────────────────────────────

async def test_channel_gate(env):
    f = mk_funnel("f", [{"key": "a"}, {"key": "nudge", "conditions": {"channel": "not_joined"}},
                        {"key": "after"}],
                  [("a", "done", "nudge", 10), ("nudge", "done", "after", 10)])
    await setup(env, f, settings={"channel": {"chat_id": -100500, "link": "https://t.me/+x"}})
    env.tg.members[(-100500, 1)] = "member"     # 1 – в канале
    env.tg.members[(-100500, 2)] = "left"       # 2 – точно не в канале
    for uid in (1, 2, 3):                        # 3 – бот не может проверить → fail-open
        await env.start_user("t1", uid)
    await env.run_for(60)
    assert [k for k, _s, _ in await sent_keys(env, 1)] == ["a", "after"]
    assert [k for k, _s, _ in await sent_keys(env, 2)] == ["a", "nudge", "after"]
    assert [k for k, _s, _ in await sent_keys(env, 3)] == ["a", "nudge", "after"]


async def test_jump_tombstone_and_missing_media(env):
    f = mk_funnel("f", [
        {"key": "a", "type": "photo", "media": {"image": "./static/nope.jpg"}},
        {"key": "old", "type": "jump"}, {"key": "new"},
    ], [("a", "done", "old", 5), ("old", "jump", "new", None)])
    await setup(env, f)
    await env.start_user("t1", 1)
    await env.run_for(60)
    calls = env.tg.sends(1)
    assert [c["method"] for c in calls] == ["SendMessage", "SendMessage"]   # нет файла → текстом
    assert [k for k, _s, _ in await sent_keys(env, 1)] == ["a", "new"]


async def test_email_request(env):
    f = mk_funnel("f", [{"key": "ask", "type": "email_request"}, {"key": "thanks"}, {"key": "silent"}],
                  [("ask", "email", "thanks", 0), ("ask", "skip", "silent", 600)])
    await setup(env, f)
    await env.start_user("t1", 1)
    await env.start_user("t1", 2)
    await env.run_for(5)
    bot = env.bot("t1")
    assert "ошибка" in (await env.rt.gateway.on_text(bot, 1, "не почта", "x1"))
    assert await env.rt.gateway.on_text(bot, 1, "Anna@Mail.ru", "x2") is None
    await env.run_for(1200)
    assert [k for k, _s, _ in await sent_keys(env, 1)] == ["ask", "thanks"]
    assert [k for k, _s, _ in await sent_keys(env, 2)] == ["ask", "silent"]
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT email FROM participants WHERE uid=1") == "anna@mail.ru"


async def test_phone_as_text(env):
    f = mk_funnel("f", [{"key": "ask", "type": "phone_request"}, {"key": "ok"}],
                  [("ask", "contact", "ok", 0)])
    await setup(env, f)
    await env.start_user("t1", 1)
    await env.run_for(1)
    await env.rt.gateway.on_text(env.bot("t1"), 1, "+7 (999) 111-22-33", "t1")
    await env.run_for(5)
    assert [k for k, _s, _ in await sent_keys(env, 1)] == ["ask", "ok"]


async def test_entry_variant_and_step_variant(env):
    f = mk_funnel("f", [{"key": "a"}, {"key": "posev"}, {"key": "b"}],
                  [("a", "done", "b", 5), ("posev", "done", "b", 5)])
    raw = f.model_dump(mode="json", by_alias=True)
    raw["entry_variants"] = [{"when": {"trigger": "poreg"}, "entry_step": str(sid("f", "posev"))}]
    raw["steps"][2]["content"]["variants"] = [{"when": {"utm_source": "tgads"},
                                               "text": "текст для рекламы"}]
    f = type(f).model_validate(raw)
    await setup(env, f)
    await env.start_user("t1", 1, "poreg_seed1")
    await env.start_user("t1", 2, "reg_tgads_x")
    await env.run_for(60)
    assert [k for k, _s, _ in await sent_keys(env, 1)] == ["posev", "b"]
    assert [c["text"] for c in env.tg.sends(2)] == ["текст a", "текст для рекламы"]
