"""Источники данных для гейтов: касса, Bizon, клики – политика fail-safe / fail-open.

Настоящий Postgres, фальшивые касса (подписанные вебхуки) и Bizon (подменённый
клиент), управляемые часы. Боевые системы не трогаются.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from runtime.clock import MSK
from runtime.gates import links as glinks
from runtime.gates import payments as gpay
from runtime.gates.bizon import BizonNotReady
from runtime.gates.evaluate import gates_of
from runtime.gates.http import add_routes

from rt_helpers import mk_funnel, router_fixed

pytestmark = pytest.mark.asyncio

SECRET = "s3cr3t-кассы"
PAY = {"enabled": True, "secret": "env:FS_TEST_PAY_SECRET", "vip_tariffs": ["T-VIP"],
       "course_tariffs": ["T-COURSE"]}
BASE = "https://engine.test"


@pytest.fixture(autouse=True)
async def _clean_gate_tables(env):
    """Таблицы источников без ссылки на tenants – TRUNCATE tenants их не чистит."""
    async with env.pool.acquire() as c:
        await c.execute("TRUNCATE gate_payments, gate_bizon_sync, gate_bizon_reports, "
                        "gate_bizon_viewers RESTART IDENTITY CASCADE")
    env.mp.setenv("FS_TEST_PAY_SECRET", SECRET)
    yield


async def sent(env, uid) -> list[str]:
    async with env.pool.acquire() as c:
        return [r["step_key"] for r in await c.fetch(
            "SELECT step_key FROM sent_messages WHERE uid=$1 AND status='ok' ORDER BY id", uid)]


async def actions(env, uid, action) -> list:
    async with env.pool.acquire() as c:
        return await c.fetch("SELECT step_key, payload FROM actions WHERE uid=$1 AND action=$2 ORDER BY id",
                             uid, action)


async def setup(env, funnel, settings=None, slug="t1"):
    await env.tenant(slug, settings=settings)
    await env.start()
    return await env.publish(slug, funnel)


def webhook(ext: str, *, status="paid", tariff="T-VIP", uid=None, phone=None, when=None, meta=None):
    body = {"event": f"payment.{status}", "external_payment_id": ext, "tariff_id": tariff,
            "status": status, "amount": 490, "currency": "RUB",
            "customer": {"phone": phone, "email": "a@b.ru"},
            "meta": dict(meta or {}, **({"id": uid} if uid else {})),
            "utm": {"content": "vip_z_offer"}}
    if when:
        body["occurred_at"] = when
    raw = json.dumps(body, ensure_ascii=False).encode()
    return raw, gpay.sign(raw, SECRET)


async def http_client(env) -> TestClient:
    app = web.Application()
    add_routes(app, env.rt)
    cli = TestClient(TestServer(app))
    await cli.start_server()
    return cli


# ── политика без источников ──────────────────────────────────────────────────

async def test_policy_without_sources(env):
    """Касса / Bizon / ссылки не настроены: «только для …» не уходят, дожимы уходят."""
    f = mk_funnel("f", [
        {"key": "a"},
        {"key": "paid_only", "conditions": {"paid": True}},
        {"key": "course_only", "conditions": {"course_paid": True}},
        {"key": "was", "conditions": {"attendance": "was"}},
        {"key": "lm", "conditions": {"has_lm": True}},
        {"key": "click_only", "conditions": {"link_click": "funnel_a"}},
        {"key": "nudge_vip", "conditions": {"not_paid": True}},
        {"key": "nudge_air", "conditions": {"attendance": "was_not"}},
        {"key": "nudge_click", "conditions": {"no_link_click": "funnel_a"}},
        {"key": "end"},
    ], [("a", "done", "paid_only", 5), ("paid_only", "done", "course_only", 5),
        ("course_only", "done", "was", 5), ("was", "done", "lm", 5), ("lm", "done", "click_only", 5),
        ("click_only", "done", "nudge_vip", 5),
        ("nudge_vip", "done", "nudge_air", 5), ("nudge_air", "done", "nudge_click", 5),
        ("nudge_click", "done", "end", 5)])
    rep = await setup(env, f)
    await env.start_user("t1", 1)
    await env.run_for(600)
    assert await sent(env, 1) == ["a", "nudge_vip", "nudge_air", "nudge_click", "end"]
    assert [r["step_key"] for r in await actions(env, 1, "gate_failsafe")] == \
        ["paid_only", "course_only", "was", "lm", "click_only"]
    assert [r["step_key"] for r in await actions(env, 1, "gate_unchecked")] == \
        ["nudge_vip", "nudge_air", "nudge_click"]
    # публикация предупредила заранее – и про «не уйдут никому», и про «уйдут всем»
    text = "\n".join(rep.warnings)
    assert "«paid_only»" in text and "НЕ уйдут никому" in text
    assert "«nudge_vip»" in text and "уйдут ВСЕМ" in text
    assert "не настроен Bizon" in text
    assert "«click_only»" in text and "не включены ссылки движка" in text


# ── касса ────────────────────────────────────────────────────────────────────

async def test_webhook_signature_idempotency_and_refund(env):
    await setup(env, mk_funnel("f", [{"key": "a"}], []), settings={"payments": PAY})
    await env.start_user("t1", 7)
    cli = await http_client(env)
    try:
        raw, sig = webhook("P-1", uid=7)
        r = await cli.post("/payments/callback", data=raw, headers={"X-Payment-Signature": "00" + sig[2:]})
        assert r.status == 401
        async with env.pool.acquire() as c:
            assert await c.fetchval("SELECT count(*) FROM gate_payments") == 0
        r = await cli.post("/payments/callback", data=raw, headers={"X-Payment-Signature": sig})
        assert r.status == 200 and (await r.json())["matched"] == 1
        r = await cli.post("/payments/callback", data=raw, headers={"X-Payment-Signature": sig})
        assert (await r.json()).get("duplicate") is True
        # чужой запуск: адрес /t/<другой>/… той же подписью не принимается
        r = await cli.post("/t/nope/payments/callback", data=raw, headers={"X-Payment-Signature": sig})
        assert r.status == 401
        t = env.rt.registry.by_slug("t1")
        async with env.pool.acquire() as c:
            assert await c.fetchval("SELECT count(*) FROM gate_payments") == 1
            assert await gpay.is_paid(c, t, "tg", 7, None, "vip") is True
            assert await gpay.is_paid(c, t, "tg", 7, None, "course") is False
        assert len(await actions(env, 7, "paid")) == 1   # повтор вебхука не дублирует событие
        raw, sig = webhook("P-1", uid=7, status="refunded")
        r = await cli.post("/t/t1/payments/callback", data=raw, headers={"X-Payment-Signature": sig})
        assert r.status == 200
        async with env.pool.acquire() as c:
            assert await gpay.is_paid(c, t, "tg", 7, None, "vip") is False
        assert len(await actions(env, 7, "payment_refunded")) == 1
    finally:
        await cli.close()


async def test_paid_gates_by_id_and_on_paid_step(env):
    f = mk_funnel("f", [
        {"key": "a"},
        {"key": "rec", "conditions": {"paid": True}, "text": "запись для ВИП"},
        {"key": "nudge", "conditions": {"not_paid": True}, "text": "купи ВИП"},
        {"key": "thanks", "text": "оплата прошла"},
    ], [("a", "done", "rec", 3600), ("rec", "done", "nudge", 60)])
    rep = await setup(env, f, settings={"payments": {**PAY, "on_paid": {"vip": "thanks"}}})
    assert not any("оплат ВИПа" in w for w in rep.warnings)
    await env.start_user("t1", 1)
    await env.start_user("t1", 2)
    await env.run_for(60)
    raw, sig = webhook("P-10", uid=1)
    assert (await gpay.receive(env.rt, raw, sig))[0] == 200
    await env.run_for(7200)
    assert await sent(env, 1) == ["a", "thanks", "rec"]        # отбивка сразу, ВИП-шаг – да, дожим – нет
    assert await sent(env, 2) == ["a", "nudge"]                # не платил: ВИП-шаг пропущен ПО ДАННЫМ
    assert await actions(env, 2, "gate_failsafe") == []        # это не fail-safe, касса ответила «нет»
    assert [r["step_key"] for r in await actions(env, 2, "gated_out")] == ["rec"]
    paid = await actions(env, 1, "paid")
    assert len(paid) == 1 and "по id" in paid[0]["payload"]


@pytest.mark.parametrize("by_phone,expected", [(False, False), (True, True)])
async def test_match_by_phone_only_when_allowed(env, by_phone, expected):
    await setup(env, mk_funnel("f", [{"key": "a"}], []),
                settings={"payments": {**PAY, "match_by_phone": by_phone}})
    await env.start_user("t1", 5)
    async with env.pool.acquire() as c:
        await c.execute("UPDATE participants SET phone='79991112233' WHERE uid=5")
    raw, sig = webhook("P-20", phone="+7 (999) 111-22-33")      # id в платеже нет
    code, data = await gpay.receive(env.rt, raw, sig)
    assert code == 200 and data["matched"] == (1 if expected else 0)
    t = env.rt.registry.by_slug("t1")
    async with env.pool.acquire() as c:
        assert await gpay.is_paid(c, t, "tg", 5, "79991112233", "vip") is expected


async def test_paid_from_and_foreign_tariff(env):
    await setup(env, mk_funnel("f", [{"key": "a"}], []),
                settings={"payments": {**PAY, "paid_from": "2026-09-05"}})
    await env.start_user("t1", 3)
    t = env.rt.registry.by_slug("t1")
    raw, sig = webhook("P-old", uid=3, when="2026-09-01T12:00:00+03:00")
    assert (await gpay.receive(env.rt, raw, sig))[1]["matched"] == 0
    raw, sig = webhook("P-other", uid=3, tariff="T-CHUZHOY")
    assert (await gpay.receive(env.rt, raw, sig))[1]["matched"] == 0
    async with env.pool.acquire() as c:
        assert await gpay.is_paid(c, t, "tg", 3, None, "vip") is False
    raw, sig = webhook("P-new", uid=3, when="2026-09-06T12:00:00+03:00")
    assert (await gpay.receive(env.rt, raw, sig))[1]["matched"] == 1
    async with env.pool.acquire() as c:
        assert await gpay.is_paid(c, t, "tg", 3, None, "vip") is True


# ── Bizon ────────────────────────────────────────────────────────────────────

class FakeBizon:
    def __init__(self):
        self.reports: list[dict] = []
        self.viewers_by: dict[str, list[dict]] = {}
        self.not_ready: set[str] = set()
        self.calls: list[tuple] = []

    def add(self, wid: str, room: str, start_msk: str, viewers: list[dict]):
        self.reports.insert(0, {"webinarId": wid, "name": f"acc:{room}",
                                "created": MSK.localize(datetime.fromisoformat(start_msk)).isoformat()})
        self.viewers_by[wid] = viewers

    async def list_reports(self, skip, limit):
        self.calls.append(("list", skip))
        return self.reports[skip:skip + limit]

    async def viewers(self, wid, skip, limit):
        self.calls.append(("viewers", wid, skip))
        if wid in self.not_ready:
            raise BizonNotReady(wid)
        return self.viewers_by.get(wid, [])[skip:skip + limit]


BIZON = {"enabled": True, "token": "env:FS_TEST_BIZON", "room": "r1", "list_ttl_seconds": 0}


def air_funnel():
    """Эфир 11.09 11:00; шаги в 16:00 первого и второго дня (§32а: окно – на дне шага)."""
    return mk_funnel("f", [
        {"key": "a"},
        {"key": "d1_was", "conditions": {"attendance": "was"},
         "schedule": {"webinar_offset_minutes": 300}},
        {"key": "d2_was", "conditions": {"attendance": "was"},
         "schedule": {"webinar_offset_minutes": 300, "day_offset": 1}},
        {"key": "d2_was_not", "conditions": {"attendance": "was_not"},
         "schedule": {"webinar_offset_minutes": 301, "day_offset": 1}},
    ], [("a", "done", "d1_was", 0), ("d1_was", "done", "d2_was", 0), ("d2_was", "done", "d2_was_not", 0)],
        branch_router=router_fixed("f", "a", "2026-09-11", "11:00"))


async def test_bizon_window_on_step_day(env):
    fake = FakeBizon()
    fake.add("r1*2026-09-11T11:00:00", "r1", "2026-09-11T11:00",
             [{"url": "https://start.bizon365.ru/room/r1?id=1&autologin=1"}, {"phone": "+79990000002"}])
    fake.add("r1*2026-09-12T11:00:00", "r1", "2026-09-12T11:00", [{"phone": "89990000002"}])
    fake.add("x*2026-09-12T11:00:00", "other", "2026-09-12T11:00", [{"url": "https://x?id=1"}])
    await setup(env, air_funnel(), settings={"bizon": BIZON})
    gates_of(env.rt).bizon.client_factory = lambda s: fake
    await env.start_user("t1", 1)          # был только в первый день (по id из ссылки)
    await env.start_user("t1", 2)          # был оба дня (по телефону)
    async with env.pool.acquire() as c:
        await c.execute("UPDATE participants SET phone='79990000002' WHERE uid=2")
    await env.run_for(86400 * 3)
    # без окна на дне шага d2_was ушёл бы первому (он был в первый день)
    assert await sent(env, 1) == ["a", "d1_was", "d2_was_not"]
    assert await sent(env, 2) == ["a", "d1_was", "d2_was"]
    assert await actions(env, 1, "gate_failsafe") == []
    # зрители закрытого отчёта забираются один раз
    assert sum(1 for x in fake.calls if x[0] == "viewers" and x[1] == "r1*2026-09-11T11:00:00" and x[2] == 0) == 1


async def test_bizon_report_not_ready_is_unknown(env):
    """§27: отчёта ещё нет / 403 – «был» не шлём (fail-safe), «не был» шлём (fail-open)."""
    fake = FakeBizon()
    fake.add("r1*2026-09-11T11:00:00", "r1", "2026-09-11T11:00", [{"url": "https://b?id=1"}])
    fake.not_ready.add("r1*2026-09-11T11:00:00")
    await setup(env, air_funnel(), settings={"bizon": BIZON})
    gates_of(env.rt).bizon.client_factory = lambda s: fake
    await env.start_user("t1", 1)
    await env.run_for(86400 * 3)
    assert await sent(env, 1) == ["a", "d2_was_not"]
    fs = await actions(env, 1, "gate_failsafe")
    assert [r["step_key"] for r in fs] == ["d1_was", "d2_was"]
    assert "не готова" in fs[0]["payload"] and "ещё нет" in fs[1]["payload"]
    assert [r["step_key"] for r in await actions(env, 1, "gate_unchecked")] == ["d2_was_not"]


async def test_bizon_down_is_unknown(env):
    class Down(FakeBizon):
        async def list_reports(self, skip, limit):
            raise ConnectionError("нет сети")
    await setup(env, air_funnel(), settings={"bizon": BIZON})
    gates_of(env.rt).bizon.client_factory = lambda s: Down()
    await env.start_user("t1", 1)
    await env.run_for(86400 * 3)
    assert await sent(env, 1) == ["a", "d2_was_not"]


# ── клики ────────────────────────────────────────────────────────────────────

LINKS = {"enabled": True, "base_url": BASE + "/"}


async def test_click_via_go_and_gate(env):
    f = mk_funnel("f", [
        {"key": "a", "text": 'Смотрите <a href="https://land.test/p?x=1">лендинг</a> и https://land.test/b.',
         "buttons": [{"text": "Лендинг", "url": "https://land.test/p"},
                     {"text": "Канал", "url": "https://t.me/+chan"}]},
        {"key": "push", "conditions": {"no_link_click": "funnel_a"}},
    ], [("a", "done", "push", 3600)])
    await setup(env, f, settings={"links": LINKS})
    await env.start_user("t1", 1)
    await env.start_user("t1", 2)
    await env.run_for(5)
    call = env.tg.sends(1)[0]
    btn, chan = [r[0] for r in call["reply_markup"].inline_keyboard]
    assert btn.url.startswith(BASE + "/go/") and "u=1" in btn.url and chan.url == "https://t.me/+chan"
    assert call["text"].count(BASE + "/go/") == 2 and ">лендинг</a>" in call["text"]
    cli = await http_client(env)
    try:
        path = urlsplit(btn.url)
        # превью-робот телеграма не считается кликом
        r = await cli.get(f"{path.path}?{path.query}", allow_redirects=False,
                          headers={"User-Agent": "TelegramBot (like TwitterBot)"})
        assert r.status == 302
        t = env.rt.registry.by_slug("t1")
        async with env.pool.acquire() as c:
            assert await glinks.clicked(c, t, "tg", 1, "funnel_a") is False
        r = await cli.get(f"{path.path}?{path.query}", allow_redirects=False,
                          headers={"User-Agent": "Mozilla/5.0"})
        assert r.status == 302 and r.headers["Location"] == "https://land.test/p"
        assert (await cli.get("/go/nope", allow_redirects=False)).status == 404
    finally:
        await cli.close()
    await env.run_for(7200)
    assert await sent(env, 1) == ["a"]            # кликнул кнопку – дожим погас
    assert await sent(env, 2) == ["a", "push"]    # не кликал – дожим ушёл
    assert await actions(env, 2, "gate_unchecked") == []   # проверено по данным


async def test_max_text_links_are_not_clicks(env):
    """§26: в MAX переход по ссылке из текста – не человек; считаются только кнопки."""
    await setup(env, mk_funnel("f", [{"key": "a"}], []), settings={"links": LINKS})
    t = env.rt.registry.by_slug("t1")
    now = env.clock.now()
    async with env.pool.acquire() as c:
        tcode = await glinks.link_code(c, t, "https://land.test/", "text_a", "text", now)
        bcode = await glinks.link_code(c, t, "https://land.test/", "funnel_a", "button", now)
    await glinks.go(env.rt, tcode, {"u": "9", "p": "max"}, "Mozilla")
    async with env.pool.acquire() as c:
        assert await glinks.clicked(c, t, "max", 9, "text_a") is False
        assert await glinks.clicked(c, t, "max", 9, "") is False
    await glinks.go(env.rt, tcode, {"u": "9", "p": "tg"}, "Mozilla")
    await glinks.go(env.rt, bcode, {"u": "9", "p": "max"}, "Mozilla")
    async with env.pool.acquire() as c:
        assert await glinks.clicked(c, t, "tg", 9, "text_a") is True
        assert await glinks.clicked(c, t, "max", 9, "funnel_a") is True


async def test_stream_click_counts_as_attendance_and_bizon_autologin(env):
    """Переход по ссылке эфира через /go = «был» (даже без отчёта Bizon); в ссылку – id человека."""
    f = mk_funnel("f", [
        {"key": "live", "buttons": [{"text": "Эфир", "url": "https://start.bizon365.ru/room/acc/r1"}],
         "schedule": {"webinar_offset_minutes": 0}},
        {"key": "rec", "conditions": {"attendance": "was"}, "schedule": {"webinar_offset_minutes": 240}},
    ], [("live", "done", "rec", 0)], branch_router=router_fixed("f", "live", "2026-09-11", "11:00"))
    await setup(env, f, settings={"links": LINKS, "bizon": {**BIZON, "enabled": False, "room": "r1"}})
    await env.start_user("t1", 1)
    await env.start_user("t1", 2)
    await env.run_until(MSK.localize(datetime(2026, 9, 11, 11, 1)))
    url = env.tg.sends(1)[-1]["reply_markup"].inline_keyboard[0][0].url
    async with env.pool.acquire() as c:
        await c.execute("UPDATE participants SET phone='79995550011' WHERE uid=1")
    code, loc = await glinks.go(env.rt, urlsplit(url).path.rsplit("/", 1)[1],
                                {"u": "1", "p": "tg"}, "Mozilla")
    q = parse_qs(urlsplit(loc).query)
    assert code == 302 and q["id"] == ["1"] and q["phone"] == ["79995550011"] and q["autologin"] == ["1"]
    await env.run_for(86400)
    assert await sent(env, 1) == ["live", "rec"]
    assert await sent(env, 2) == ["live"]          # Bizon выключен, клика нет – «не знаем», fail-safe


async def test_vip_link_goes_through_govip(env):
    f = mk_funnel("f", [{"key": "offer", "buttons": [{"text": "ВИП", "url": "{vip_link}"}]}], [])
    await setup(env, f, settings={"links": LINKS,
                                  "webinar": {"vip_url": "https://pay.test/buy/T-VIP?src=bot"}})
    await env.start_user("t1", 4, payload="reg_vk_cpc")
    await env.run_for(5)
    url = env.tg.sends(4)[0]["reply_markup"].inline_keyboard[0][0].url
    assert url == f"{BASE}/t/t1/govip?u=4&pl=tg&s=offer"
    code, loc = await glinks.govip(env.rt, "t1", {"u": "4", "pl": "tg", "s": "offer"}, "Mozilla")
    q = parse_qs(urlsplit(loc).query)
    assert code == 302 and loc.startswith("https://pay.test/buy/T-VIP?")
    assert q["id"] == ["4"] and q["tg_id"] == ["4"] and q["src"] == ["bot"]
    assert q["utm_content"] == ["vip_offer"] and q["utm_source"] == ["vk"]
    t = env.rt.registry.by_slug("t1")
    async with env.pool.acquire() as c:
        assert await glinks.clicked(c, t, "tg", 4, "vip_") is True
    # касса вернула id из ссылки → оплата привязалась к человеку
    await env.rt.registry.load_tenants()
    env.rt.registry.tenants[t.id].settings.payments = env.rt.registry.tenants[t.id].settings.payments \
        .model_copy(update=PAY)
    raw, sig = webhook("P-vip", meta={"id": q["id"][0], "pl": "tg"})
    assert (await gpay.receive(env.rt, raw, sig))[1]["matched"] == 1
