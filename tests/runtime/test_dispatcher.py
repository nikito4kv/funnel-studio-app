"""Апдейты Telegram через настоящий aiogram Dispatcher: запуск – по id бота."""
from __future__ import annotations

from datetime import datetime

import pytest
from aiogram import Dispatcher
from aiogram.types import CallbackQuery, Chat, ChatMemberBanned, ChatMemberMember, ChatMemberUpdated, \
    Contact, Message, Update, User

from rt_helpers import mk_funnel
from runtime.clock import MSK
from runtime.runner import build_router

pytestmark = pytest.mark.asyncio


def _msg(uid, mid, **kw):
    return Message(message_id=mid, date=datetime.now(MSK), chat=Chat(id=uid, type="private"),
                   from_user=User(id=uid, is_bot=False, first_name="Оля"), **kw)


async def test_updates_route_to_right_tenant(env):
    await env.tenant("alpha")
    await env.tenant("beta")
    await env.start()
    fa = mk_funnel("f", [{"key": "ask", "type": "phone_request"}, {"key": "ok", "text": "спасибо альфа"}],
                   [("ask", "contact", "ok", 0)])
    fb = mk_funnel("f", [{"key": "a", "text": "бета", "claim": "Жми"}, {"key": "b", "text": "бета b"}],
                   [("a", "callback", "b", None)])
    await env.publish("alpha", fa)
    await env.publish("beta", fb)
    dp = Dispatcher()
    dp.include_router(build_router(env.rt))
    ba, bb = env.bot("alpha").bot, env.bot("beta").bot

    await dp.feed_update(ba, Update(update_id=1, message=_msg(7, 1, text="/start reg_tg_ads")))
    await dp.feed_update(bb, Update(update_id=2, message=_msg(7, 2, text="/start")))
    await env.run_for(1)
    await dp.feed_update(ba, Update(update_id=3, message=_msg(
        7, 3, contact=Contact(phone_number="89991112233", first_name="Оля", user_id=7))))
    cb_data = env.tg.sends(7)[-1]["reply_markup"].inline_keyboard[0][0].callback_data
    await dp.feed_update(bb, Update(update_id=4, callback_query=CallbackQuery(
        id="q1", from_user=User(id=7, is_bot=False, first_name="Оля"), chat_instance="ci",
        data=cb_data, message=_msg(7, 99, text="бета"))))
    await env.run_for(5)
    by_bot = {}
    for c in env.tg.sends(7):
        by_bot.setdefault(c["bot"], []).append(c["text"])
    assert by_bot[ba.id] == ["текст ask", "спасибо альфа"]
    assert by_bot[bb.id] == ["бета", "бета b"]
    async with env.pool.acquire() as c:
        rows = await c.fetch("""SELECT t.slug, p.phone, p.utm_source FROM participants p
                                JOIN tenants t ON t.id=p.tenant_id ORDER BY t.slug""")
        starts = await c.fetch("SELECT payload FROM actions WHERE action LIKE 'start%' ORDER BY id")
    assert [(r["slug"], r["phone"], r["utm_source"]) for r in rows] == \
        [("alpha", "79991112233", "tg"), ("beta", None, None)]
    assert [r["payload"] for r in starts] == ["reg_tg_ads", None]

    # заблокировал бота бета → его задачи отменены, альфа не тронута
    u = User(id=7, is_bot=False, first_name="Оля")
    await dp.feed_update(bb, Update(update_id=5, my_chat_member=ChatMemberUpdated(
        chat=Chat(id=7, type="private"), from_user=u, date=datetime.now(MSK),
        old_chat_member=ChatMemberMember(user=User(id=bb.id, is_bot=True, first_name="b")),
        new_chat_member=ChatMemberBanned(user=User(id=bb.id, is_bot=True, first_name="b"),
                                         until_date=datetime.now(MSK)))))
    async with env.pool.acquire() as c:
        rows = await c.fetch("""SELECT t.slug, p.blocked FROM participants p
                                JOIN tenants t ON t.id=p.tenant_id ORDER BY t.slug""")
    assert [(r["slug"], r["blocked"]) for r in rows] == [("alpha", False), ("beta", True)]
