"""Полный проход воронки webinar (импорт из bot-engine через studio.convert)
типовым человеком: порядок и время сообщений."""
from __future__ import annotations

from datetime import timedelta

import pytest
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

from studio.convert.from_engine import import_file
from studio.paths import bot_repo_path

WEBINAR = bot_repo_path() / "launches" / "webinar" / "funnel.yaml"
pytestmark = pytest.mark.skipif(not WEBINAR.is_file(), reason="нет bot-engine рядом")

SETTINGS = {
    "display_name": "Spreadsheet Automation Workshop",
    "static_launch": "webinar",
    "channel": {"link": "https://t.me/+DemoChannelInvite", "title": "Spreadsheet Automation Workshop", "chat_id": 0},
    "webinar": {"datetime_msk": "2026-09-17T11:00",
                "gift_url": "https://example.com/workshop-ticket"},
}


async def _sent(env, uid):
    async with env.pool.acquire() as c:
        return await c.fetch(
            """SELECT step_key, status, created_at, array_length(message_ids, 1) n
               FROM sent_messages WHERE uid=$1 ORDER BY id""", uid)


async def test_webinar_full_pass(env):
    await env.tenant("webinar", settings=SETTINGS)
    await env.start()
    funnel = import_file(WEBINAR, repo_root=bot_repo_path())
    rep = await env.publish("webinar", funnel, comment="первая")
    assert rep.to_version == 1
    t0 = env.clock.now()
    uid = 5001

    assert await env.start_user("webinar", uid, "reg_vk_post_sep") == "started"
    await env.run_for(0)
    # s_confirm: картинка + просьба о контакте с кнопкой «отправить контакт»
    first = env.tg.sends(uid)[0]
    assert first["method"] == "SendPhoto" and first["media"] == "file:contact.jpg"
    assert isinstance(first["reply_markup"], ReplyKeyboardMarkup)
    assert first["reply_markup"].keyboard[0][0].request_contact
    assert "Аня, please confirm" in first["text"]

    env.clock.advance(5)
    assert await env.contact("webinar", uid) == "ok"
    # люди, дошедшие до конца дня: прогоняем 6 часов
    await env.run_until(t0 + timedelta(hours=6))
    rows = await _sent(env, uid)
    got = [(r["step_key"], round((r["created_at"] - t0).total_seconds())) for r in rows]
    assert got == [
        ("s_confirm", 0), ("s_greet", 5), ("s_welcome", 10), ("s_remind1", 190), ("s_vipintro", 1810),
        ("s_offer", 1815), ("s_remind2", 7390), ("s_offer_nudge", 16215), ("s_survey", 19815),
    ], got
    assert all(r["status"] == "ok" for r in rows)

    calls = env.tg.sends(uid)
    kinds = [c["method"] for c in calls]
    # s_welcome длиннее 1000 символов → картинка отдельно, текст с кнопками следом
    assert kinds[2:4] == ["SendPhoto", "SendMessage"]
    assert isinstance(calls[3]["reply_markup"], InlineKeyboardMarkup)
    assert calls[3]["reply_markup"].inline_keyboard[0][0].url == SETTINGS["channel"]["link"]
    # кружок – с диаметром из файла (§35), без текста
    vn = calls[1]
    assert vn["method"] == "SendVideoNote" and vn["media"] == "file:circle1.mp4"
    assert vn.get("length") == 640 and vn.get("duration")
    # ВИП-кнопка ведёт на страницу ВИПа, а не на /govip без домена
    vip = next(c for c in calls if c["text"] and "ticket" in (c["text"] or "").lower() and c["reply_markup"])
    assert vip["reply_markup"].inline_keyboard[0][0].url == SETTINGS["webinar"]["gift_url"]

    # гейты: канал не проверить (chat_id=0) и ВИП без источника – fail-open, записано
    async with env.pool.acquire() as c:
        unchecked = await c.fetch("SELECT step_key, payload FROM actions WHERE uid=$1 "
                                  "AND action='gate_unchecked' ORDER BY id", uid)
    assert [r["step_key"] for r in unchecked] == ["s_remind1", "s_vipintro", "s_offer", "s_remind2", "s_offer_nudge"]

    # s_survey: кнопка-клейм → опрос из 3 вопросов (edit-in-place) → подарок после опроса
    survey = calls[-1]
    btn = survey["reply_markup"].inline_keyboard[0][0]
    assert btn.text == "📝 TAKE THE SURVEY" and btn.callback_data.startswith("c:")
    env.clock.advance(60)
    await env.click("webinar", uid, btn.callback_data)
    await env.run_for(10)
    rows = await _sent(env, uid)
    assert rows[-1]["step_key"] == "s_q1"
    q1 = env.tg.sends(uid)[-1]
    kb = q1["reply_markup"].inline_keyboard
    assert [r[0].text for r in kb][:2] == ["Employee", "Business owner"]
    assert all(r[0].callback_data.startswith("f:webinar:sv:") for r in kb)
    await env.click("webinar", uid, kb[1][0].callback_data)        # q1: single
    await env.run_for(5)
    edits = [c for c in env.tg.calls if c["method"] == "EditMessageText"]
    assert edits and edits[-1]["message_id"] == q1_mid(env, uid)      # q2 встал на место q1
    kb2 = edits[-1]["reply_markup"].inline_keyboard
    assert kb2[-1][0].text == "✅ Done, next"
    await env.click("webinar", uid, kb2[0][0].callback_data)       # q2: multi – два варианта
    await env.click("webinar", uid, kb2[3][0].callback_data)
    await env.click("webinar", uid, kb2[-1][0].callback_data)      # «готово»
    await env.run_for(5)
    kb3 = [c for c in env.tg.calls if c["method"] == "EditMessageText"][-1]["reply_markup"].inline_keyboard
    await env.click("webinar", uid, kb3[0][0].callback_data)       # q3: финал
    await env.run_for(5)
    rows = await _sent(env, uid)
    assert rows[-1]["step_key"] == "s_thanks"
    async with env.pool.acquire() as c:
        ans = await c.fetchrow("SELECT * FROM survey_responses WHERE uid=$1", uid)
        pos = await c.fetchrow("SELECT * FROM positions WHERE uid=$1", uid)
        part = await c.fetchrow("SELECT * FROM participants WHERE uid=$1", uid)
    assert ans["answers"] == {"q1": "Business owner",
                              "q2": ["Build reports faster", "Raise my income"], "q3": "Beginner"}
    assert ans["submitted_at"] is not None
    assert pos["status"] == "done" and pos["step_key"] == "s_thanks"
    assert part["phone"] == "79991234567" and part["utm_source"] == "vk" and part["trigger"] == "reg"

    # второй человек: те же файлы уходят уже по file_id из кэша (заливка один раз на бота)
    before = len(env.tg.calls)
    await env.start_user("webinar", 5002)
    await env.run_for(0)
    again = env.tg.sends(5002)[0]
    assert again["media"].startswith("ph"), again["media"]
    assert len(env.tg.calls) == before + 1


async def test_phone_known_and_skip_timeout(env):
    """Телефон уже известен → запрос пропускается (через 2 с); молчит 10 с → «не дал»."""
    await env.tenant("webinar", settings=SETTINGS)
    await env.start()
    await env.publish("webinar", import_file(WEBINAR, repo_root=bot_repo_path()))
    t0 = env.clock.now()
    # молчун: 10 с тишины → s_greet по ветке «не дал»
    await env.start_user("webinar", 7001)
    await env.run_for(30)
    rows = await _sent(env, 7001)
    assert [(r["step_key"], round((r["created_at"] - t0).total_seconds())) for r in rows] == \
        [("s_confirm", 0), ("s_greet", 10), ("s_welcome", 15)]
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT phone_auto_skipped FROM participants WHERE uid=7001")
        await c.execute("UPDATE participants SET phone='79990000000' WHERE uid=7001")
        await c.execute("UPDATE jobs SET status='cancelled' WHERE uid=7001 AND status='pending'")
    # повторный вход того, чей телефон известен: s_confirm молча пропускается
    env.clock.advance(3600)
    t1 = env.clock.now()
    await env.start_user("webinar", 7001)
    await env.run_for(20)
    rows = (await _sent(env, 7001))[3:]
    assert [(r["step_key"], round((r["created_at"] - t1).total_seconds())) for r in rows] == \
        [("s_greet", 2), ("s_welcome", 7)]


def q1_mid(env, uid):
    """id сообщения первого вопроса опроса (на его место встают следующие)."""
    return next(c["_mid"] for c in env.tg.calls if c.get("_mid") and c["chat_id"] == uid
                and c["method"] == "SendMessage" and c["reply_markup"] is not None
                and getattr(c["reply_markup"], "inline_keyboard", None)
                and c["reply_markup"].inline_keyboard[0][0].text == "Employee")
