"""Виды шагов (runtime/steps) у людей из MAX: опрос кнопками и текстом, закреп эфира,
статья, звонок и SMS, переход в другую воронку, прокси-ссылки кнопок и гейты по кликам.

Запуск с ДВУМЯ ботами – Telegram и MAX: проверяется, что человеку из MAX ничего не
уходит через Telegram (раньше виды шагов звали aiogram напрямую). Фальшивые MAX API,
Telegram и провайдеры, настоящий Postgres, управляемые часы.
"""
from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

from max_fake import FakeMax, bot_started, callback, message
from rt_helpers import mk_funnel, sid
from runtime.gates import links as glinks
from runtime.steps import providers
from studio.model.funnel import Edge, EdgeKind
from test_max import add_tenant, start_rt, upd
from test_steps import FakeProvider

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def fake(env):
    f = await FakeMax(env.clock).start()
    try:
        yield f
    finally:
        await f.stop()


async def sent(env, uid):
    async with env.pool.acquire() as c:
        return [(r["step_key"], r["status"]) for r in await c.fetch(
            "SELECT step_key, status FROM sent_messages WHERE uid=$1 AND platform='max' ORDER BY id", uid)]


async def actions(env, uid, *names):
    async with env.pool.acquire() as c:
        return [(r["action"], r["step_key"], r["payload"]) for r in await c.fetch(
            """SELECT action, step_key, payload FROM actions WHERE uid=$1 AND platform='max'
                 AND action = ANY($2) ORDER BY id""", uid, list(names))]


def no_telegram(env, uid):
    """Человеку MAX ничего не ушло через Telegram (ни сообщений, ни правок, ни «печатает…»)."""
    bad = [c for c in env.tg.calls if c.get("chat_id") == uid]
    assert not bad, [c["method"] for c in bad]


async def setup(env, fake, *funnels, settings=None, slug="m1"):
    await add_tenant(env, slug, settings=settings, default=True, tg=True)
    await start_rt(env, fake)
    for f in funnels:
        await env.publish(slug, f)


# ── опрос ────────────────────────────────────────────────────────────────────

async def test_max_survey_free_text_answer(env, fake):
    f = mk_funnel("f", [
        {"key": "a"},
        {"key": "q1", "text": "Чем занимаетесь?", "survey": {"qid": "q1", "free": True, "final": True}},
        {"key": "thanks"},
    ], [("a", "done", "q1", 5), ("q1", "survey_next", "thanks", None)])
    await setup(env, fake, f)
    uid = 71001
    await upd(env, bot_started(uid, "m1_reg_x"))
    await env.run_for(10)
    q = fake.of(uid)[-1]
    assert q["text"] == "Чем занимаетесь?" and fake.buttons(q) == []   # свободный ответ – без кнопок
    assert await upd(env, message(uid, "Делаю ботов")) == "text"
    await env.run_for(10)
    assert [k for k, _ in await sent(env, uid)] == ["a", "q1", "thanks"]
    async with env.pool.acquire() as c:
        r = await c.fetchrow("SELECT * FROM survey_responses WHERE uid=$1", uid)
    assert r["platform"] == "max" and r["answers"] == {"q1": "Делаю ботов"}
    assert r["submitted_at"] is not None
    no_telegram(env, uid)


async def test_max_survey_buttons_and_done_gate(env, fake):
    """Кнопки ответа – callback со слагом (§36); финал гасит клавиатуру правкой сообщения;
    гейт «не прошёл опрос» (survey_done) видит ответ человека из MAX."""
    f = mk_funnel("f", [
        {"key": "a"},
        {"key": "q1", "text": "Уровень?", "survey": {"qid": "q1", "final": True},
         "buttons": [{"text": "Beginner"}, {"text": "Профи"}]},
        {"key": "thanks"},
        {"key": "nudge", "conditions": {"survey_done": True}},
    ], [("a", "done", "q1", 5), ("q1", "survey_next", "thanks", None),
        ("a", "also_fire", "nudge", 3600)])
    await setup(env, fake, f)
    for uid in (71101, 71102):
        await upd(env, bot_started(uid, "m1_reg_x"))
    await env.run_for(10)
    q = fake.of(71101)[-1]
    kb = fake.buttons(q)
    assert [b["text"] for b in kb] == ["Beginner", "Профи"]
    assert all(b["type"] == "callback" and b["payload"].startswith("f:m1:sv:") for b in kb)
    assert await upd(env, callback(71101, kb[1]["payload"])) == "callback"
    assert fake.answers[-1]["notification"] == "✓ Профи"
    # финальный вопрос: клавиатура снята (вложения – пустой список), текст на месте
    last = fake.edits_of(71101)[-1]
    assert last["mid"] == q["mid"] and last["attachments"] == [] and last["text"] == "Уровень?"
    await env.run_for(7200)
    assert [k for k, _ in await sent(env, 71101)] == ["a", "q1", "thanks"]      # прошёл – дожима нет
    assert [k for k, _ in await sent(env, 71102)] == ["a", "q1", "nudge"]       # не прошёл – ушёл
    async with env.pool.acquire() as c:
        gated = await c.fetchval("SELECT payload FROM actions WHERE uid=71101 AND action='gated_out'")
    assert gated.startswith("survey_done")
    assert await actions(env, 71102, "gate_unchecked") == []                    # проверено по данным
    no_telegram(env, 71101)


# ── закреп, статья, звонок, SMS, переход ─────────────────────────────────────

SETTINGS = {
    "channel": {"link": "https://t.me/+chan", "max_link": "https://max.ru/join/chan"},
    "webinar": {"datetime_msk": "2026-09-12T11:00", "stream_url": "https://live.example/tg",
                "stream_url_max": "https://live.example/max"},
    "air_pin": {"days": [{"when": "2026-09-12T11:00", "url": "https://live.example/d1"}]},
    "telephony": {"provider": "fake"}, "sms": {"provider": "fake"},
    "service_chat_id": -100500,
    "entry_funnel": "fa",
}


def chain_edge(fid, a, to, delay=0, typing=False):
    return Edge(id=uuid.uuid5(uuid.NAMESPACE_URL, f"{fid}:{a}:chain:{to}"), from_step=sid(fid, a),
                kind=EdgeKind.chain_to_funnel, to_funnel=to, delay_seconds=delay, typing_before=typing)


@pytest.fixture
def fake_providers(monkeypatch):
    made = {}

    def reg(channel, prov):
        made[channel] = prov
        monkeypatch.setitem(providers.FACTORIES.setdefault(channel, {}), "fake", lambda cfg: prov)
        return prov
    return reg


async def test_max_steps_pin_rich_call_sms_chain(env, fake, fake_providers):
    tel = fake_providers("call", FakeProvider())
    sms = fake_providers("sms", FakeProvider())
    fa = mk_funnel("fa", [
        {"key": "a"},
        {"key": "pin", "type": "air_pin"},
        {"key": "art", "type": "rich", "media": {"rich": "./rich/art.json"}, "text": "статья текстом",
         "buttons": [{"text": "Канал", "url": "{channel.link}"}]},
        {"key": "head", "text": "шаг с шапкой", "pin": {"air_pin": True, "pin": True}},
        {"key": "call1", "type": "call", "call": {"audio_id": 777}},
        {"key": "sms1", "type": "sms", "sms": {"text": "{Имя}, эфир: {stream_url}"}},
        {"key": "go"},
        {"key": "never"},
    ], [("a", "done", "pin", 5), ("pin", "done", "art", 5), ("art", "done", "head", 5),
        ("head", "done", "call1", 5), ("call1", "done", "sms1", 5), ("sms1", "done", "go", 5),
        ("go", "done", "never", 60)])
    fa.edges.append(chain_edge("fa", "go", "fb", delay=3, typing=True))
    fb = mk_funnel("fb", [{"key": "b1"}], [])
    await setup(env, fake, fa, fb, settings=SETTINGS)
    uid = 71201
    await upd(env, bot_started(uid, "m1_reg_x"))
    async with env.pool.acquire() as c:
        await c.execute("UPDATE participants SET phone='79991112233' WHERE uid=$1", uid)
    await env.run_for(120)

    # закреп эфира в MAX пропущен (как в bot-engine), цепочка дальше; статья – текстом
    assert [k for k, _ in await sent(env, uid)] == ["a", "art", "head", "call1", "sms1", "go", "b1"]
    acts = await actions(env, uid, "air_pin_skipped", "rich_fallback", "pin_skipped", "chain")
    assert [a[:2] for a in acts] == [("air_pin_skipped", "pin"), ("rich_fallback", "art"),
                                     ("pin_skipped", "head"), ("air_pin_skipped", "head"),
                                     ("chain", "go")]
    texts = [c["text"] for c in fake.of(uid)]
    assert "статья текстом" in texts and "шаг с шапкой" in texts
    art = next(c for c in fake.of(uid) if c["text"] == "статья текстом")
    assert [b["url"] for b in fake.buttons(art)] == ["https://max.ru/join/chan"]   # ссылки MAX
    # звонок и SMS – на телефон MAX-участника, в SMS – MAX-зал эфира (§25)
    assert tel.calls == [{"phone": "79991112233", "audio_url": None, "audio_id": 777, "text": None}]
    assert sms.calls == [{"phone": "79991112233", "text": "Маша, эфир: https://live.example/max"}]
    # переход в другую воронку: позиция в fb, on_done «go» не пошёл
    async with env.pool.acquire() as c:
        pos = {r["funnel_id"]: r["status"] for r in await c.fetch(
            "SELECT funnel_id, status FROM positions WHERE uid=$1 AND platform='max'", uid)}
    assert set(pos) == {"fa", "fb"}
    no_telegram(env, uid)                        # ни закрепа, ни статьи, ни «печатает…» через TG
    assert not any(c["method"] in ("SendRichMessage", "PinChatMessage", "SendChatAction")
                   for c in env.tg.calls)


# ── прокси-ссылки кнопок и гейты по кликам в MAX ─────────────────────────────

BASE = "https://engine.test"
LINKS = {"enabled": True, "base_url": BASE}


async def test_max_button_links_wrapped_and_click_gates(env, fake):
    f = mk_funnel("f", [
        {"key": "a", "text": 'Смотрите <a href="https://land.test/t">лендинг</a>',
         "buttons": [{"text": "Лендинг", "url": "https://land.test/p"},
                     {"text": "ВИП", "url": "{vip_link}"},
                     {"text": "Канал", "url": "{channel.link}"}]},
        {"key": "push", "conditions": {"no_link_click": "funnel_a"}},
        {"key": "thanks", "conditions": {"link_click": "funnel_a"}},
    ], [("a", "done", "push", 3600), ("push", "done", "thanks", 60)])
    await setup(env, fake, f, settings={
        "links": LINKS, "channel": {"link": "https://t.me/+c", "max_link": "https://max.ru/join/c"},
        "webinar": {"vip_url": "https://pay.test/buy/VIP"}})
    for uid in (71301, 71302):
        await upd(env, bot_started(uid, "m1_reg_x"))
    await env.run_for(5)
    call = fake.of(71301)[0]
    land, vip, chan = fake.buttons(call)
    assert land["url"].startswith(BASE + "/go/") and "u=71301" in land["url"] and "p=max" in land["url"]
    assert vip["url"] == f"{BASE}/t/m1/govip?u=71301&pl=max&s=a"          # ВИП-прокси с платформой
    assert chan["url"] == "https://max.ru/join/c"                          # max.ru не оборачивается
    assert 'href="https://land.test/t"' in call["text"]                     # §26: текст – как есть

    code = urlsplit(land["url"]).path.rsplit("/", 1)[1]
    q = {k: v[0] for k, v in parse_qs(urlsplit(land["url"]).query).items()}
    status, loc = await glinks.go(env.rt, code, q, "Mozilla/5.0")
    assert status == 302 and loc == "https://land.test/p"
    status, loc = await glinks.govip(env.rt, "m1", {"u": "71301", "pl": "max", "s": "a"}, "Mozilla")
    assert status == 302 and parse_qs(urlsplit(loc).query)["max_id"] == ["71301"]

    await env.run_for(7200)
    # кликнул кнопку – дожим погас, «только кликнувшим» ушёл; не кликал – наоборот
    assert [k for k, _ in await sent(env, 71301)] == ["a", "thanks"]
    assert [k for k, _ in await sent(env, 71302)] == ["a", "push"]
    async with env.pool.acquire() as c:
        clicks = await c.fetch("SELECT platform, label, source FROM gate_clicks ORDER BY id")
    assert [tuple(r) for r in clicks] == [("max", "funnel_a", "button"), ("max", "vip_a", "vip")]
    assert await actions(env, 71302, "gate_unchecked", "gate_failsafe") == []
