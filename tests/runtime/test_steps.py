"""Виды шагов из runtime/steps: опрос, звонок, SMS, закреп эфира, статья, переход
в другую воронку. Настоящий Postgres, фальшивый Telegram и фальшивые провайдеры,
управляемые часы."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from aiogram.exceptions import TelegramBadRequest

from runtime.clock import MSK
from runtime.steps import air_pin, providers, survey
from runtime.worker import Worker
from studio.model.funnel import Edge, EdgeKind

from rt_helpers import SimulatedCrash, mk_funnel, router_fixed, sid

pytestmark = pytest.mark.asyncio


async def sent(env, uid):
    async with env.pool.acquire() as c:
        return [(r["step_key"], r["status"]) for r in await c.fetch(
            "SELECT step_key, status FROM sent_messages WHERE uid=$1 ORDER BY id", uid)]


async def actions(env, uid, *names):
    async with env.pool.acquire() as c:
        return [(r["action"], r["step_key"], r["payload"]) for r in await c.fetch(
            "SELECT action, step_key, payload FROM actions WHERE uid=$1 AND action = ANY($2) ORDER BY id",
            uid, list(names))]


async def setup(env, *funnels, settings=None, slug="t1"):
    await env.tenant(slug, settings=settings)
    await env.start()
    for f in funnels:
        await env.publish(slug, f)


def kb_of(call):
    return call["reply_markup"].inline_keyboard if call.get("reply_markup") else []


def chain_edge(fid, a, to, delay=0, typing=False):
    return Edge(id=uuid.uuid5(uuid.NAMESPACE_URL, f"{fid}:{a}:chain:{to}"), from_step=sid(fid, a),
                kind=EdgeKind.chain_to_funnel, to_funnel=to, delay_seconds=delay, typing_before=typing)


# ── опрос ────────────────────────────────────────────────────────────────────

def survey_funnel(**q1):
    return mk_funnel("f", [
        {"key": "a"},
        {"key": "q1", "text": "Чем занимаетесь?", "survey": {"qid": "q1", **q1},
         "buttons": [{"text": "Найм"}, {"text": "Бизнес"}, {"text": "Пенсия"}]},
        {"key": "thanks"},
        {"key": "z_survey_followup"},
    ], [("a", "done", "q1", 5), ("q1", "survey_next", "thanks", None),
        ("a", "also_fire", "z_survey_followup", 3600)])


async def test_survey_single_answer_and_repeat_tap_ignored(env):
    await setup(env, survey_funnel(final=True))
    await env.start_user("t1", 1)
    await env.run_for(10)
    q = env.tg.sends(1)[-1]
    rows = kb_of(q)
    assert [r[0].text for r in rows] == ["Найм", "Бизнес", "Пенсия"]
    assert all(r[0].callback_data.startswith("f:t1:sv:") for r in rows)
    async with env.pool.acquire() as c:
        pos = await c.fetchrow("SELECT status, awaiting FROM positions WHERE uid=1")
    assert pos["status"] == "active" and pos["awaiting"]["kind"] == "survey"   # ждёт ответа, не «дошёл»

    assert await env.click("t1", 1, rows[1][0].callback_data) == "✓ Бизнес"
    # повторное нажатие (та же или другая кнопка) – ответ уже принят, второго перехода нет
    assert await env.click("t1", 1, rows[1][0].callback_data) == survey.REPEAT_ACK
    assert await env.click("t1", 1, rows[0][0].callback_data) == survey.REPEAT_ACK
    await env.run_for(7200)
    assert [k for k, _ in await sent(env, 1)] == ["a", "q1", "thanks"]
    async with env.pool.acquire() as c:
        r = await c.fetchrow("SELECT * FROM survey_responses WHERE uid=1")
        fu = await c.fetchval("SELECT status FROM jobs WHERE step_key='z_survey_followup'")
        pos = await c.fetchrow("SELECT status, awaiting FROM positions WHERE uid=1")
    assert r["answers"] == {"q1": "Бизнес"} and r["submitted_at"] is not None
    assert fu == "cancelled"                        # final отменил дожим опроса
    assert pos["status"] == "done" and pos["awaiting"] is None
    # у финального вопроса гасятся кнопки
    assert any(c["method"] == "EditMessageReplyMarkup" and c["reply_markup"] is None for c in env.tg.calls)
    assert len(await actions(env, 1, "survey_repeat")) == 2


async def test_survey_restart_reopens_question(env):
    await setup(env, survey_funnel())
    await env.start_user("t1", 1)
    await env.run_for(10)
    await env.click("t1", 1, kb_of(env.tg.sends(1)[-1])[0][0].callback_data)
    await env.run_for(10)
    env.clock.advance(3600)
    await env.start_user("t1", 1)                    # воронка с начала – вопрос снова
    await env.run_for(10)
    assert await env.click("t1", 1, kb_of(env.tg.sends(1)[-1])[2][0].callback_data) == "✓ Пенсия"
    await env.run_for(10)
    assert [k for k, _ in await sent(env, 1)].count("thanks") == 2
    async with env.pool.acquire() as c:
        assert (await c.fetchval("SELECT answers FROM survey_responses WHERE uid=1")) == {"q1": "Пенсия"}


async def test_survey_multi_toggle_and_done(env):
    await setup(env, survey_funnel(multi=True, done_button="Готово"))
    await env.start_user("t1", 1)
    await env.run_for(10)
    rows = kb_of(env.tg.sends(1)[-1])
    assert rows[-1][0].text == "Готово"
    assert await env.click("t1", 1, rows[0][0].callback_data) == "✓ Найм"
    assert await env.click("t1", 1, rows[2][0].callback_data) == "✓ Пенсия"
    assert await env.click("t1", 1, rows[0][0].callback_data) == "– Найм"
    edits = [c for c in env.tg.calls if c["method"] == "EditMessageReplyMarkup"]
    assert [r[0].text for r in kb_of(edits[-1])][:3] == ["Найм", "Бизнес", "✅ Пенсия"]
    await env.run_for(60)
    assert [k for k, _ in await sent(env, 1)] == ["a", "q1"]          # без «готово» дальше не идём
    assert await env.click("t1", 1, rows[-1][0].callback_data) == "Готово"
    await env.run_for(10)
    assert [k for k, _ in await sent(env, 1)] == ["a", "q1", "thanks"]
    async with env.pool.acquire() as c:
        assert (await c.fetchval("SELECT answers FROM survey_responses WHERE uid=1")) == {"q1": ["Пенсия"]}


async def test_survey_free_text_answer(env):
    await setup(env, survey_funnel(free=True))
    await env.start_user("t1", 1)
    await env.run_for(10)
    assert kb_of(env.tg.sends(1)[-1]) == []           # свободный ответ – без кнопок-вариантов
    assert await env.rt.gateway.on_text(env.bot("t1"), 1, "Делаю ботов", "m1") is None
    await env.run_for(10)
    assert [k for k, _ in await sent(env, 1)] == ["a", "q1", "thanks"]
    # следующее сообщение – уже не ответ
    await env.rt.gateway.on_text(env.bot("t1"), 1, "спасибо", "m2")
    async with env.pool.acquire() as c:
        assert (await c.fetchval("SELECT answers FROM survey_responses WHERE uid=1")) == {"q1": "Делаю ботов"}
    assert [a[2] for a in await actions(env, 1, "message")] == ["спасибо"]


async def test_survey_question_not_sent_goes_next(env):
    await setup(env, survey_funnel())
    env.tg.fail_rules.append(lambda c: TelegramBadRequest(method=None, message="Bad Request: boom")
                             if c["text"] == "Чем занимаетесь?" else None)
    await env.start_user("t1", 1)
    await env.run_for(30)
    assert [k for k, _ in await sent(env, 1)] == ["a", "q1", "thanks"]
    assert (await sent(env, 1))[1][1] == "error"


async def test_survey_foreign_slug_ignored(env):
    await setup(env, survey_funnel())
    await env.start_user("t1", 1)
    await env.run_for(10)
    data = kb_of(env.tg.sends(1)[-1])[0][0].callback_data.replace("f:t1:", "f:other:")
    assert await env.click("t1", 1, data) == ""
    await env.run_for(10)
    assert [k for k, _ in await sent(env, 1)] == ["a", "q1"]


# ── звонок и SMS ─────────────────────────────────────────────────────────────

class FakeProvider:
    def __init__(self, name="fake", fail: Exception | None = None, ok=True):
        self.name, self.fail, self.ok = name, fail, ok
        self.calls: list[dict] = []

    async def dial(self, phone, *, audio_url=None, audio_id=None, text=None):
        self.calls.append({"phone": phone, "audio_url": audio_url, "audio_id": audio_id, "text": text})
        if self.fail:
            raise self.fail
        return providers.ProviderResult(self.ok, "принят" if self.ok else "HTTP 500: нет денег")

    async def send(self, phone, text):
        self.calls.append({"phone": phone, "text": text})
        if self.fail:
            raise self.fail
        return providers.ProviderResult(self.ok, "принят" if self.ok else "HTTP 500: нет денег")


@pytest.fixture
def fake_providers(monkeypatch):
    made = {}

    def reg(channel, prov):
        made[channel] = prov
        monkeypatch.setitem(providers.FACTORIES.setdefault(channel, {}), "fake", lambda cfg: prov)
        return prov
    return reg


PROV_SETTINGS = {"telephony": {"provider": "fake"}, "sms": {"provider": "fake"},
                 "webinar": {"stream_url": "https://live.example/room?x=1", "datetime_msk": "2026-09-11T11:00"}}


def call_sms_funnel():
    return mk_funnel("f", [
        {"key": "a"},
        {"key": "call1", "type": "call", "call": {"audio_id": 777}},
        {"key": "sms1", "type": "sms",
         "sms": {"text": "{Имя}, эфир в {webinar.time}: {stream_url}"}},
        {"key": "c"},
    ], [("a", "done", "call1", 5), ("call1", "done", "sms1", 5), ("sms1", "done", "c", 5)])


async def test_call_and_sms_through_provider(env, fake_providers):
    tel = fake_providers("call", FakeProvider())
    sms = fake_providers("sms", FakeProvider())
    await setup(env, call_sms_funnel(), settings=PROV_SETTINGS)
    await env.start_user("t1", 1)
    await env.contact("t1", 1, "8 (999) 123-45-67")
    await env.run_for(60)
    assert tel.calls == [{"phone": "79991234567", "audio_url": None, "audio_id": 777, "text": None}]
    assert sms.calls == [{"phone": "79991234567",
                          "text": "Аня, эфир в 11:00: https://live.example/room?x=1"}]
    assert [k for k, _ in await sent(env, 1)] == ["a", "call1", "sms1", "c"]
    acts = await actions(env, 1, "call", "sms")
    assert [a[:2] for a in acts] == [("call", "call1"), ("sms", "sms1")]
    assert "…4567" in acts[0][2] and "79991234567" not in acts[0][2]   # номер в логе замаскирован
    async with env.pool.acquire() as c:
        data = await c.fetchval("SELECT data FROM actions WHERE action='sms'")
    assert "без сокращения" in data["links"]


@pytest.mark.parametrize("mode", ["raise", "http_error"])
async def test_provider_error_chain_continues(env, fake_providers, mode):
    kw = {"fail": RuntimeError("таймаут")} if mode == "raise" else {"ok": False}
    fake_providers("call", FakeProvider(**kw))
    fake_providers("sms", FakeProvider(**kw))
    await setup(env, call_sms_funnel(), settings=PROV_SETTINGS)
    await env.start_user("t1", 1)
    await env.contact("t1", 1)
    await env.run_for(60)
    assert [k for k, s in await sent(env, 1)] == ["a", "call1", "sms1", "c"]
    assert [s for k, s in await sent(env, 1)] == ["ok", "error", "error", "ok"]
    assert [a[0] for a in await actions(env, 1, "call_error", "sms_error")] == ["call_error", "sms_error"]


async def test_call_no_phone_and_raw_key_skipped(env, monkeypatch):
    monkeypatch.setenv("FS_ZVON_KEY", "k-123")
    await setup(env, call_sms_funnel(), settings={
        "telephony": {"provider": "zvonobot", "api_key": "env:FS_ZVON_KEY"},
        "sms": {"provider": "p1sms", "api_key": "открытый-ключ"}})
    await env.start_user("t1", 1)
    await env.run_for(60)
    acts = await actions(env, 1, "call_skipped", "sms_skipped")
    assert acts[0][:2] == ("call_skipped", "call1") and acts[0][2] == "нет телефона"
    assert acts[1][:2] == ("sms_skipped", "sms1") and "открытым текстом" in acts[1][2]
    assert [k for k, _ in await sent(env, 1)] == ["a", "c"]


async def test_call_not_repeated_after_crash(env, fake_providers):
    """Процесс упал сразу после звонка – повтор задачи второй раз не звонит."""
    tel = fake_providers("call", FakeProvider(fail=SimulatedCrash("упали после звонка")))
    fake_providers("sms", FakeProvider())
    await setup(env, call_sms_funnel(), settings=PROV_SETTINGS)
    await env.start_user("t1", 1)
    await env.contact("t1", 1)
    await env.run_for(0)
    env.clock.advance(5)
    with pytest.raises(SimulatedCrash):
        await env.worker.tick()
    tel.fail = None
    env.clock.advance(env.rt.cfg.lease_seconds + 1)
    await env.run_for(60, worker=Worker(env.rt, name="w2"))
    assert len(tel.calls) == 1
    assert [k for k, _ in await sent(env, 1)] == ["a", "call1", "sms1", "c"]
    assert await actions(env, 1, "resend_skipped")


# ── закреп эфира ─────────────────────────────────────────────────────────────

PIN_SETTINGS = {
    "channel": {"link": "https://t.me/+chan"},
    "air_pin": {"days": [{"when": "2026-09-12T11:00", "url": "https://live.example/d1"},
                         {"when": "2026-09-13T11:00", "url": "https://live.example/d2"}],
                "live_minutes": 120, "soon_minutes": 30, "after_text": ""},
}


def edits_of(env, uid):
    return [c for c in env.tg.calls if c["method"] in ("EditMessageText", "EditMessageCaption")
            and c["chat_id"] == uid]


async def test_air_pin_step_states_follow_engine_clock(env):
    f = mk_funnel("f", [{"key": "a"}, {"key": "pin", "type": "air_pin"}, {"key": "b"}],
                  [("a", "done", "pin", 0), ("pin", "done", "b", 10)])
    await setup(env, f, settings=PIN_SETTINGS)
    await env.start_user("t1", 1)          # 10.09 12:00 МСК, эфир 12.09 11:00
    await env.run_for(30)
    assert [k for k, _ in await sent(env, 1)] == ["a", "pin", "b"]   # закреп не рвёт цепочку
    pin_msg = next(c for c in env.tg.sends(1) if "до эфира" in (c["text"] or ""))
    assert pin_msg["text"].startswith("⏳ <b>1 день до эфира.</b>")
    assert kb_of(pin_msg)[0][0].url == "https://t.me/+chan"          # до эфира – в канал
    assert any(c["method"] == "PinChatMessage" for c in env.tg.calls)
    async with env.pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM air_pins WHERE uid=1")
    assert row["state"] == "wait:0:d0" or row["state"].startswith("wait:0:d")
    assert row["next_at"] is not None

    async def at(msk_iso):
        from datetime import datetime
        env.clock.set(MSK.localize(datetime.fromisoformat(msk_iso)))
        return await air_pin.refresh_due(env.rt)

    await at("2026-09-11T12:30:00")                  # меньше суток – часы
    assert edits_of(env, 1)[-1]["text"].startswith("⏳ <b>22 ч. до эфира.</b>")
    n = len(edits_of(env, 1))
    await at("2026-09-11T12:45:00")                  # гранула не сменилась – не трогаем
    assert len(edits_of(env, 1)) == n
    await at("2026-09-12T10:31:00")                  # за 30 минут – «скоро», кнопка в зал
    e = edits_of(env, 1)[-1]
    assert e["text"].startswith("⏰ <b>Первый эфир →</b>") and kb_of(e)[0][0].url == "https://live.example/d1"
    await at("2026-09-12T11:00:02")                  # эфир идёт
    assert edits_of(env, 1)[-1]["text"].startswith("🔴 <b>Первый эфир идёт, вход →</b>")
    await at("2026-09-12T13:00:02")                  # эфир кончился – ждём второй
    e = edits_of(env, 1)[-1]
    assert "до эфира" in e["text"] and kb_of(e)[0][0].url == "https://t.me/+chan"
    await at("2026-09-13T11:00:02")
    assert edits_of(env, 1)[-1]["text"].startswith("🔴 <b>Второй эфир идёт")
    await at("2026-09-13T13:00:02")                  # все прошли, after_text пуст – закреп снят
    assert any(c["method"] == "UnpinChatMessage" for c in env.tg.calls)
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM air_pins") == 0


async def test_air_pin_adopts_step_message(env):
    """Обычный шаг с pin.air_pin: шапка с отсчётом сверху его текста, закрепляется он сам."""
    f = mk_funnel("f", [{"key": "a", "text": "Вступай в канал", "pin": {"air_pin": True},
                         "buttons": [{"text": "Канал", "url": "https://t.me/+chan"}]}, {"key": "b"}],
                  [("a", "done", "b", 5)])
    await setup(env, f, settings=PIN_SETTINGS)
    await env.start_user("t1", 1)
    await env.run_for(30)
    mid = env.tg.sends(1)[0]["_mid"]
    e = edits_of(env, 1)[-1]
    assert e["message_id"] == mid and e["text"].endswith("\n\nВступай в канал")
    assert e["text"].startswith("⏳ <b>")
    pins = [c for c in env.tg.calls if c["method"] == "PinChatMessage"]
    assert pins and pins[0]["message_id"] == mid
    assert [k for k, _ in await sent(env, 1)] == ["a", "b"]
    # перерисовка сохраняет текст шага
    from datetime import datetime
    env.clock.set(MSK.localize(datetime(2026, 9, 12, 11, 0, 2)))
    await air_pin.refresh_due(env.rt)
    assert edits_of(env, 1)[-1]["text"].endswith("\n\nВступай в канал")
    assert "идёт" in edits_of(env, 1)[-1]["text"]


async def test_air_pin_evergreen_uses_person_webinar(env):
    """Дней в настройках нет – закреп считает до эфира самого человека (branch_router)."""
    f = mk_funnel("f", [{"key": "a"}, {"key": "pin", "type": "air_pin"}], [("a", "done", "pin", 0)],
                  branch_router=router_fixed("f", "a", "2026-09-11", "18:00"))
    await setup(env, f, settings={"channel": {"link": "https://t.me/+c"},
                                  "webinar": {"stream_url": "https://live.example/x"}})
    await env.start_user("t1", 1)          # 10.09 12:00, эфир 11.09 18:00 – через 30 ч
    await env.run_for(10)
    msg = next(c for c in env.tg.sends(1) if "до эфира" in (c["text"] or ""))
    assert msg["text"].startswith("⏳ <b>1 день до эфира.</b>")
    from datetime import datetime
    env.clock.set(MSK.localize(datetime(2026, 9, 11, 18, 0, 2)))
    await air_pin.refresh_due(env.rt)
    e = edits_of(env, 1)[-1]
    assert e["text"].startswith("🔴 <b>Прямой эфир идёт") and kb_of(e)[0][0].url == "https://live.example/x"


async def test_air_pin_nothing_to_show_chain_continues(env):
    f = mk_funnel("f", [{"key": "pin", "type": "air_pin"}, {"key": "b"}], [("pin", "done", "b", 5)])
    await setup(env, f, settings={})
    await env.start_user("t1", 1)
    await env.run_for(30)
    assert [k for k, _ in await sent(env, 1)] == ["b"]
    assert [a[0] for a in await actions(env, 1, "air_pin_skipped")] == ["air_pin_skipped"]


# ── статья (rich) ────────────────────────────────────────────────────────────

def _rich_files(env):
    media = env.tmp / "data" / "media"
    (media / "rich").mkdir(parents=True, exist_ok=True)
    (media / "banner.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 100)
    (media / "rich" / "art.json").write_text(json.dumps({"blocks": [
        {"type": "heading", "text": "Заголовок", "size": 1},
        {"type": "photo", "photo": {"type": "photo", "media": "./static/banner.jpg"}},
        {"type": "paragraph", "text": ["Абзац"]},
    ]}, ensure_ascii=False), encoding="utf-8")


async def test_rich_article_with_image_and_buttons(env):
    f = mk_funnel("f", [{"key": "a", "type": "rich", "media": {"rich": "./rich/art.json"},
                         "text": "запасной текст",
                         "buttons": [{"text": "Грант", "url": "https://t.me/x"}]}, {"key": "b"}],
                  [("a", "done", "b", 5)])
    await setup(env, f, settings={"service_chat_id": -100500})
    _rich_files(env)
    await env.start_user("t1", 1)
    await env.run_for(30)
    art = next(c for c in env.tg.calls if c["method"] == "SendRichMessage")
    blocks = art["rich_message"]["blocks"]
    assert [b["type"] for b in blocks] == ["heading", "photo", "paragraph"]
    assert blocks[1]["photo"]["media"].startswith("ph")        # file_id, а не путь
    assert kb_of(art)[0][0].url == "https://t.me/x"
    upload = next(c for c in env.tg.calls if c["method"] == "SendPhoto")
    assert upload["chat_id"] == -100500                         # залито в служебный чат
    assert any(c["method"] == "DeleteMessage" and c["chat_id"] == -100500 for c in env.tg.calls)
    assert [k for k, _ in await sent(env, 1)] == ["a", "b"]
    # второму человеку картинка не перезаливается – file_id из кэша
    await env.start_user("t1", 2)
    await env.run_for(0)
    assert len([c for c in env.tg.calls if c["method"] == "SendPhoto"]) == 1


async def test_rich_rejected_falls_back_to_text(env):
    f = mk_funnel("f", [{"key": "a", "type": "rich", "media": {"rich": "./rich/art.json"},
                         "text": "запасной текст"}, {"key": "b"}], [("a", "done", "b", 5)])
    await setup(env, f, settings={})
    _rich_files(env)
    env.tg.fail_rules.append(lambda c: TelegramBadRequest(method=None, message="Bad Request: unknown method")
                             if c["method"] == "SendRichMessage" else None)
    await env.start_user("t1", 1)
    await env.run_for(30)
    assert [c["text"] for c in env.tg.sends(1) if c["method"] == "SendMessage"][0] == "запасной текст"
    assert [k for k, _ in await sent(env, 1)] == ["a", "b"]
    assert await actions(env, 1, "rich_fallback")


# ── переход в другую воронку ─────────────────────────────────────────────────

async def test_chain_to_other_funnel_and_back(env):
    fa = mk_funnel("fa", [{"key": "a1"}, {"key": "a2"}, {"key": "a_done"}],
                   [("a1", "done", "a2", 5), ("a2", "done", "a_done", 60)])
    fa.edges.append(chain_edge("fa", "a2", "fb", delay=3, typing=True))
    fb = mk_funnel("fb", [{"key": "b1"}, {"key": "b2"}], [("b1", "done", "b2", 5)])
    fb.edges.append(chain_edge("fb", "b2", "back", delay=120))
    await setup(env, fa, fb, settings={"entry_funnel": "fa", "funnel_aliases": {"back": "fa"}})
    t0 = env.clock.now()
    await env.start_user("t1", 1)
    await env.run_for(20)
    assert any(c["method"] == "SendChatAction" and c["action"] == "typing" for c in env.tg.calls)
    await env.run_for(200)
    keys = [k for k, _ in await sent(env, 1)]
    # переход важнее on_done: a_done не пришёл; через 120 с – обратно в fa с начала
    assert keys[:4] == ["a1", "a2", "b1", "b2"] and "a_done" not in keys[:5]
    assert keys[4] == "a1"
    async with env.pool.acquire() as c:
        ins = await c.fetch("SELECT payload, created_at FROM actions WHERE uid=1 AND action='chain_in' "
                            "ORDER BY id")
        pos = {r["funnel_id"]: r for r in await c.fetch("SELECT * FROM positions WHERE uid=1")}
    assert [r["payload"] for r in ins][:3] == ["fa → fb", "fb → fa", "fa → fb"]
    assert round((ins[0]["created_at"] - t0).total_seconds()) == 5
    assert set(pos) == {"fa", "fb"}
    b1_at = next(r for r in await _sent_at(env, 1) if r[0] == "b1")[1]
    assert round((b1_at - t0).total_seconds()) == 8          # a2 в +5, переход с задержкой 3 с


async def _sent_at(env, uid):
    async with env.pool.acquire() as c:
        return [(r["step_key"], r["created_at"]) for r in await c.fetch(
            "SELECT step_key, created_at FROM sent_messages WHERE uid=$1 ORDER BY id", uid)]


async def test_chain_loop_guard(env):
    """A ↔ B по кругу: не больше CHAIN_MAX_PER_HOUR переходов за час."""
    fa = mk_funnel("fa", [{"key": "a1"}], [])
    fa.edges.append(chain_edge("fa", "a1", "fb", delay=100))
    fb = mk_funnel("fb", [{"key": "b1"}], [])
    fb.edges.append(chain_edge("fb", "b1", "fa", delay=100))
    await setup(env, fa, fb, settings={"entry_funnel": "fa"})
    await env.start_user("t1", 1)
    await env.run_for(3600)
    keys = [k for k, _ in await sent(env, 1)]
    assert len(keys) == 11 and keys[:3] == ["a1", "b1", "a1"]
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM actions WHERE action='chain_in'") == 10
    loop = await actions(env, 1, "chain_loop")
    assert len(loop) == 1 and "переходов за час" in loop[0][2]


async def test_chain_same_transition_twice_in_minute(env):
    fa = mk_funnel("fa", [{"key": "a1"}, {"key": "a2"}, {"key": "a3"}],
                   [("a1", "also_fire", "a2", 10), ("a2", "done", "a3", 5)])
    fa.edges.append(chain_edge("fa", "a1", "fb"))
    fa.edges.append(chain_edge("fa", "a2", "fb"))
    fb = mk_funnel("fb", [{"key": "b1"}], [])
    await setup(env, fa, fb, settings={"entry_funnel": "fa"})
    await env.start_user("t1", 1)
    await env.run_for(60)
    assert [k for k, _ in await sent(env, 1)] == ["a1", "b1", "a2", "a3"]   # второй переход → on_done
    assert "меньше 60 с" in (await actions(env, 1, "chain_loop"))[0][2]


async def test_chain_missing_target_goes_on_done(env):
    fa = mk_funnel("fa", [{"key": "a1"}, {"key": "a2"}], [("a1", "done", "a2", 5)])
    fa.edges.append(chain_edge("fa", "a1", "нет_такой"))
    await setup(env, fa)
    await env.start_user("t1", 1)
    await env.run_for(30)
    assert [k for k, _ in await sent(env, 1)] == ["a1", "a2"]
    assert (await actions(env, 1, "chain_failed"))[0][1] == "a1"


async def test_chained_funnel_gets_contact_and_claim(env):
    """После перехода телефон и кнопка-клейм относятся к воронке, где человек сейчас."""
    fa = mk_funnel("fa", [{"key": "a1"}], [])
    fa.edges.append(chain_edge("fa", "a1", "fb"))
    fb = mk_funnel("fb", [{"key": "ph", "type": "phone_request"}, {"key": "got", "claim": "Хочу"},
                          {"key": "gift"}],
                   [("ph", "contact", "got", 0), ("got", "callback", "gift", None)])
    await setup(env, fa, fb, settings={"entry_funnel": "fa"})
    await env.start_user("t1", 1)
    await env.run_for(10)
    await env.contact("t1", 1)
    await env.run_for(10)
    btn = kb_of(env.tg.sends(1)[-1])[0][0]
    await env.click("t1", 1, btn.callback_data)
    await env.run_for(10)
    assert [k for k, _ in await sent(env, 1)] == ["a1", "ph", "got", "gift"]


async def test_chain_not_repeated_after_crash(env):
    """Упали после перехода, до записи итога шага – повтор задачи не перезапустит fb."""
    fa = mk_funnel("fa", [{"key": "a1"}], [])
    fa.edges.append(chain_edge("fa", "a1", "fb"))
    fb = mk_funnel("fb", [{"key": "b1"}], [])
    await setup(env, fa, fb, settings={"entry_funnel": "fa"})
    import runtime.steps.chain as chain_mod
    orig = chain_mod.run_chain
    state = {"n": 0}

    async def crashing(*a, **kw):
        r = await orig(*a, **kw)
        state["n"] += 1
        if state["n"] == 1:
            raise SimulatedCrash("упали после перехода")
        return r

    env.mp.setattr(chain_mod, "run_chain", crashing)
    await env.start_user("t1", 1)
    with pytest.raises(SimulatedCrash):
        await env.worker.tick()
    env.clock.advance(env.rt.cfg.lease_seconds + 1)
    await env.run_for(30, worker=Worker(env.rt, name="w2"))
    assert [k for k, _ in await sent(env, 1)] == ["a1", "b1"]
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM actions WHERE action='chain_in'") == 1


async def test_callback_data_fits_telegram_limit():
    d = survey.cb_data("a" * 30, uuid.uuid4(), 12)
    assert len(d.encode()) <= 64 and d.startswith("f:" + "a" * 30 + ":sv:")
    p = survey.parse(d)
    assert p.slug == "a" * 30 and p.choice == "12"
    assert survey.cb_data("x" * 60, uuid.uuid4(), 1).startswith("sv:")


async def test_air_pin_next_change_granularity():
    from datetime import datetime
    ap = air_pin.AirPinSettings(days=[{"when": "2026-09-12T11:00"}])
    days = air_pin.days_for(ap)
    now = MSK.localize(datetime(2026, 9, 10, 12, 0))
    assert air_pin.state_key(days, now, 30) == "wait:0:d1"
    nxt = air_pin.next_change(days, now, 30)
    assert nxt.astimezone(MSK) == MSK.localize(datetime(2026, 9, 11, 11, 0, 1))
    assert air_pin.state_key(days, nxt, 30) == "wait:0:h23"
    assert air_pin.counter_text(days[0].start, nxt) == "23 ч."
    assert timedelta(0) < nxt - now
