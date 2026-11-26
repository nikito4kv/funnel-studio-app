"""MAX в движке: фальшивый MAX API (настоящий HTTP) + настоящий Postgres +
управляемые часы. Боевые боты bot-engine не трогаются.

Проверяется: полный проход webinar в MAX (порядок и секунды), общий бот на
два запуска (роутинг по метке, запуск по умолчанию), повторный вход текстом
«/start <метка>», чужая кнопка не попадает в свой запуск, content.max
(None / пустое / свои кнопки), кружок без подписи, ссылки MAX, блокировка,
long-poll с маркером, кэш токенов вложений.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from max_fake import FakeMax, bot_started, callback, contact, message, stopped
from rt_helpers import TEST_DSN, mk_funnel, sid
from runtime.config import RuntimeConfig
from runtime.platforms.max_hub import MaxBot, MaxLink, route_start, split_callback
from runtime.service import Runtime
from runtime.worker import Worker
from studio.model.funnel import Button, MaxContent, StepVariant, CohortMatch

MAX_ENV = "FS_TEST_MAX_TOKEN"


@pytest.fixture
async def fake(env):
    f = await FakeMax(env.clock).start()
    try:
        yield f
    finally:
        await f.stop()


async def add_tenant(env, slug: str, *, settings: dict | None = None, prefix: str | None = None,
                     default: bool = False, tg: bool = False, max_env: str = MAX_ENV) -> int:
    """Запуск + (по желанию) TG-бот + привязка к MAX-боту (общему, если env тот же)."""
    if tg:
        tid = await env.tenant(slug, settings=settings)
    else:
        async with env.pool.acquire() as c:
            tid = await c.fetchval(
                "INSERT INTO tenants (slug, title, settings) VALUES ($1,$1,$2) RETURNING id",
                slug, settings or {})
    env.mp.setenv(max_env, f"max-token-{max_env}")
    async with env.pool.acquire() as c:
        bot_id = await c.fetchval(
            """INSERT INTO bots (tenant_id, platform, secret_ref) VALUES ($1,'max',$2)
               ON CONFLICT (platform, secret_ref) DO UPDATE SET enabled=true RETURNING id""",
            tid, f"env:{max_env}")
        await c.execute("INSERT INTO bot_tenants (bot_id, tenant_id, prefix, is_default) "
                        "VALUES ($1,$2,$3,$4)", bot_id, tid, prefix or f"{slug}_", default)
    return tid


async def start_rt(env, fake) -> Runtime:
    cfg = RuntimeConfig(dsn=TEST_DSN, data_dir=env.tmp / "data")
    env.rt = Runtime(cfg, clock=env.clock, session_factory=env.tg.session, pool=env.pool,
                     max_api_factory=fake.api)
    await env.rt.open()
    env.worker = Worker(env.rt, name="w1")
    return env.rt


def the_bot(env) -> MaxBot:
    return next(iter(env.rt.max_hub.bots.values()))


async def upd(env, u: dict) -> str:
    return await env.rt.max_hub.handle_update(the_bot(env), u)


async def sent_rows(env, uid: int, platform: str = "max"):
    async with env.pool.acquire() as c:
        return await c.fetch(
            """SELECT s.step_key, s.status, s.created_at, s.ext_message_ids, t.slug
               FROM sent_messages s JOIN tenants t ON t.id=s.tenant_id
               WHERE s.uid=$1 AND s.platform=$2 ORDER BY s.id""", uid, platform)


# ── роутинг: чистые функции ──────────────────────────────────────────────────

def test_route_start_and_callback_split():
    bot = MaxBot(row_id=1, api=None, links=[MaxLink(1, "ai_", True), MaxLink(2, "ai_agents_", False),
                                             MaxLink(3, "promo16_", False), MaxLink(4, "promo_", False)])
    assert route_start(bot, "ai_agents_reg_vk_x") == (2, "reg_vk_x")   # самая длинная метка
    assert route_start(bot, "ai_reg_vk") == (1, "reg_vk")
    assert route_start(bot, "promo16_reg_a") == (3, "reg_a")
    assert route_start(bot, "promo_reg_a") == (4, "reg_a")
    assert route_start(bot, "ai_agents") == (2, "")
    assert route_start(bot, "") == (1, "")                               # без метки – по умолчанию
    assert route_start(bot, "zzz_reg_x") == (1, "zzz_reg_x")
    assert route_start(MaxBot(row_id=2, api=None, links=[MaxLink(5, "a_", False)]), "b_x") is None
    assert split_callback("f:webinar:c:abc") == ("webinar", "c:abc")
    assert split_callback("c:abc") is None and split_callback("f::x") is None


# ── полный проход webinar в MAX ─────────────────────────────────────────────

WEBINAR_SETTINGS = {
    "display_name": "Spreadsheet Automation Workshop",
    "static_launch": "webinar",
    "channel": {"link": "https://t.me/+DemoChannelInvite", "max_link": "https://max.ru/join/webinar",
                "chat_id": 0},
    "webinar": {"datetime_msk": "2026-09-17T11:00",
                "gift_url": "https://example.com/workshop-ticket"},
}


def _webinar():
    from studio.convert.from_engine import import_file
    from studio.paths import bot_repo_path
    p = bot_repo_path() / "launches" / "webinar" / "funnel.yaml"
    if not p.is_file():
        pytest.skip("нет bot-engine рядом")
    return import_file(p, repo_root=bot_repo_path())


async def test_max_webinar_full_pass(env, fake):
    funnel = _webinar()
    await add_tenant(env, "webinar", settings=WEBINAR_SETTINGS, default=True)
    await start_rt(env, fake)
    await env.publish("webinar", funnel, comment="первая")
    t0 = env.clock.now()
    uid = 88001

    assert await upd(env, bot_started(uid, "webinar_reg_vk_post_sep")) == "started"
    await env.run_for(0)
    first = fake.of(uid)[0]
    # s_confirm (manual_phone_max): картинка + MAX-текст просьбы + кнопка «поделиться контактом»
    assert fake.media(first) == ["image"]
    assert "Маша, please confirm" in first["text"] and "Send your phone number" in first["text"]
    assert [b["type"] for b in fake.buttons(first)] == ["request_contact"]
    assert first["format"] == "html" and "<tg-time" not in first["text"]

    env.clock.advance(5)
    assert await upd(env, contact(uid)) == "ok"
    await env.run_until(t0 + timedelta(hours=6))
    rows = await sent_rows(env, uid)
    got = [(r["step_key"], round((r["created_at"] - t0).total_seconds())) for r in rows]
    assert got == [
        ("s_confirm", 0), ("s_greet", 5), ("s_welcome", 10), ("s_remind1", 190), ("s_vipintro", 1810),
        ("s_offer", 1815), ("s_remind2", 7390), ("s_offer_nudge", 16215), ("s_survey", 19815),
    ], got
    assert all(r["status"] == "ok" and r["ext_message_ids"] for r in rows)

    calls = fake.of(uid)
    # кружок в MAX – обычное видео; у s_greet текста нет, подписи нет
    greet = calls[1]
    assert fake.media(greet) == ["video"] and not greet["text"]
    # s_welcome: длинный текст и картинка ОДНИМ сообщением (лимита подписи нет), кнопки – канал MAX
    z01 = calls[2]
    assert fake.media(z01) == ["image"] and len(z01["text"]) > 900   # в TG тут было бы два сообщения
    assert {b["url"] for b in fake.buttons(z01)} == {WEBINAR_SETTINGS["channel"]["max_link"]}
    assert "<tg-time" not in z01["text"] and "17 September" in z01["text"]

    # s_survey: claim-кнопка со слагом запуска (§36) → опрос из 3 вопросов в MAX
    # (edit-in-place через PUT /messages) → подарок после опроса
    btn = fake.buttons(calls[-1])[0]
    assert btn["type"] == "callback" and btn["payload"].startswith("f:webinar:c:")
    env.clock.advance(60)
    assert await upd(env, callback(uid, btn["payload"])) == "callback"
    assert fake.answers[-1]["notification"] == "✓"
    await env.run_for(10)
    rows = await sent_rows(env, uid)
    assert rows[-1]["step_key"] == "s_q1"
    q1 = fake.of(uid)[-1]
    kb = fake.buttons(q1)
    assert [b["text"] for b in kb][:2] == ["Employee", "Business owner"]
    # варианты – callback со слагом запуска: общий MAX-бот разложит нажатие по запуску
    assert all(b["type"] == "callback" and b["payload"].startswith("f:webinar:sv:") for b in kb)
    assert "Маша, what do you do?" in q1["text"]
    async with env.pool.acquire() as c:
        pos = await c.fetchrow("SELECT status, awaiting FROM positions WHERE uid=$1", uid)
    assert pos["status"] == "active" and pos["awaiting"]["kind"] == "survey"

    assert await upd(env, callback(uid, kb[1]["payload"])) == "callback"     # q1: single
    assert fake.answers[-1]["notification"] == "✓ Business owner"
    await env.run_for(5)
    edits = fake.edits_of(uid)
    assert edits and edits[-1]["mid"] == q1["mid"]              # q2 встал на место q1
    assert "Which results matter" in edits[-1]["text"]
    kb2 = fake.buttons(edits[-1])
    assert kb2[-1]["text"] == "✅ Done, next"
    await upd(env, callback(uid, kb2[0]["payload"]))            # q2: multi – два варианта
    await upd(env, callback(uid, kb2[3]["payload"]))
    ticked = fake.buttons(fake.edits_of(uid)[-1])                # отметки ✅ – правкой сообщения
    assert [b["text"] for b in ticked][:4] == ["✅ Build reports faster", "Free up my time",
                                               "Find new clients", "✅ Raise my income"]
    await upd(env, callback(uid, kb2[-1]["payload"]))           # «готово»
    await env.run_for(5)
    kb3 = fake.buttons(fake.edits_of(uid)[-1])
    assert kb3[0]["text"] == "Beginner"
    await upd(env, callback(uid, kb3[0]["payload"]))            # q3: финал
    # повторное нажатие на отвеченный вопрос – «ответ уже принят», второго перехода нет
    await upd(env, callback(uid, kb3[1]["payload"]))
    assert fake.answers[-1]["notification"] == "Ответ уже принят"
    await env.run_for(5)
    rows = await sent_rows(env, uid)
    assert [r["step_key"] for r in rows[-4:]] == ["s_q1", "s_q2", "s_q3", "s_thanks"]
    # одно сообщение на весь опрос: и подарок (s_q3 с survey_edit) встал на его место
    assert all(r["ext_message_ids"] == [q1["mid"]] for r in rows[-4:])
    post = fake.edits_of(uid)[-1]
    assert "thank you for answering" in post["text"]
    assert [b["url"] for b in fake.buttons(post)] == ["https://example.com/starter-set"]
    async with env.pool.acquire() as c:
        ans = await c.fetchrow("SELECT * FROM survey_responses WHERE uid=$1", uid)
        part = await c.fetchrow("SELECT * FROM participants WHERE uid=$1", uid)
        pos = await c.fetchrow("SELECT * FROM positions WHERE uid=$1", uid)
    assert ans["platform"] == "max" and ans["submitted_at"] is not None
    assert ans["answers"] == {"q1": "Business owner",
                              "q2": ["Build reports faster", "Raise my income"], "q3": "Beginner"}
    assert part["platform"] == "max" and part["phone"] == "79991234567"
    assert part["utm_source"] == "vk" and part["trigger"] == "reg" and part["first_name"] == "Маша"
    assert pos["status"] == "done" and pos["step_key"] == "s_thanks" and pos["awaiting"] is None

    # файлы залиты в MAX один раз на бота: второй человек получает те же токены
    uploads = len(fake.uploads)
    await upd(env, bot_started(88002, "webinar_reg_tg_x"))
    await env.run_for(0)
    assert len(fake.uploads) == uploads
    assert fake.of(88002)[0]["attachments"][0] == first["attachments"][0]


async def test_max_phone_silent_and_known(env, fake):
    """Молчит 10 с → «не дал»; телефон известен → запрос пропускается (2 с)."""
    await add_tenant(env, "webinar", settings=WEBINAR_SETTINGS, default=True)
    await start_rt(env, fake)
    await env.publish("webinar", _webinar())
    t0 = env.clock.now()
    await upd(env, bot_started(88101, ""))            # без метки – запуск по умолчанию
    await env.run_for(30)
    rows = await sent_rows(env, 88101)
    assert [(r["step_key"], round((r["created_at"] - t0).total_seconds())) for r in rows] == \
        [("s_confirm", 0), ("s_greet", 10), ("s_welcome", 15)]
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT phone_auto_skipped FROM participants WHERE uid=88101")
        await c.execute("UPDATE participants SET phone='79990000000' WHERE uid=88101")
    env.clock.advance(3600)
    t1 = env.clock.now()
    # повторный вход – текстом «/start …» (bot_started MAX шлёт только первый раз)
    assert await upd(env, message(88101, "/start webinar_reg_x")) == "started"
    await env.run_for(20)
    rows = (await sent_rows(env, 88101))[3:]
    assert [(r["step_key"], round((r["created_at"] - t1).total_seconds())) for r in rows] == \
        [("s_greet", 2), ("s_welcome", 7)]
    # номер текстом, пока ждём телефон, тоже принимается
    async with env.pool.acquire() as c:
        await c.execute("UPDATE participants SET phone=NULL WHERE uid=88101")
    env.clock.advance(3600)
    await upd(env, message(88101, "/start"))
    await env.run_for(0)
    env.clock.advance(3)
    assert await upd(env, message(88101, "8 (999) 111-22-33")) == "text"
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT phone FROM participants WHERE uid=88101") == "79991112233"
        acts = [r["action"] for r in await c.fetch(
            "SELECT action FROM actions WHERE uid=88101 ORDER BY id")]
    assert acts.count("start_re_entry") == 2 and "contact" in acts


# ── общий бот на два запуска ─────────────────────────────────────────────────

def _two_step(fid: str, claim: bool = False):
    steps = [{"key": "a1", "text": f"{fid}: привет, {{Имя}}", **({"claim": "Жми"} if claim else {})},
             {"key": "a2", "text": f"{fid}: второй"}, {"key": "a3", "text": f"{fid}: по кнопке"}]
    edges = [("a1", "done", "a2", 600)] + ([("a1", "callback", "a3", None)] if claim else [])
    return mk_funnel(fid, steps, edges)


async def test_umbrella_routing_two_tenants(env, fake):
    ta = await add_tenant(env, "alpha", default=True)
    tb = await add_tenant(env, "beta")
    await start_rt(env, fake)
    assert len(env.rt.max_hub.bots) == 1 and len(the_bot(env).links) == 2
    await env.publish("alpha", _two_step("fa", claim=True))
    await env.publish("beta", _two_step("fb", claim=True))

    assert await upd(env, bot_started(501, "beta_reg_vk_post")) == "started"
    assert await upd(env, bot_started(502, "")) == "started"           # по умолчанию → alpha
    assert await upd(env, bot_started(503, "zzz_reg_tg_x")) == "started"  # чужая метка → alpha как есть
    await env.run_for(0)
    assert fake.of(501)[0]["text"].startswith("fb:") and fake.of(502)[0]["text"].startswith("fa:")
    async with env.pool.acquire() as c:
        parts = {r["uid"]: r for r in await c.fetch(
            "SELECT tenant_id, uid, utm_source, trigger, first_payload FROM participants WHERE platform='max'")}
    assert parts[501]["tenant_id"] == tb and parts[501]["utm_source"] == "vk"
    assert parts[501]["first_payload"] == "reg_vk_post"        # метка запуска снята
    assert parts[502]["tenant_id"] == ta and parts[503]["tenant_id"] == ta
    assert parts[503]["first_payload"] == "zzz_reg_tg_x"   # незнакомая метка не срезается

    # тот же человек заходит во второй запуск – это ДРУГОЙ участник (свой запуск)
    await upd(env, bot_started(502, "beta_reg_x"))
    await env.run_for(0)
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM participants WHERE uid=502") == 2
    # повторный вход текстом «/start beta_…» – re-entry в beta, а не в alpha
    env.clock.advance(3600)
    assert await upd(env, message(501, "/start beta_reg_tg_other")) == "started"
    async with env.pool.acquire() as c:
        act = await c.fetchrow("SELECT tenant_id, action, payload FROM actions WHERE uid=501 "
                               "AND action LIKE 'start%' ORDER BY id DESC LIMIT 1")
        utm = await c.fetchval("SELECT utm_source FROM participants WHERE uid=501")
    assert act["tenant_id"] == tb and act["action"] == "start_re_entry" and act["payload"] == "reg_tg_other"
    assert utm == "vk"                       # UTM – первого входа


async def test_foreign_callback_does_not_leak(env, fake):
    await add_tenant(env, "alpha", default=True)
    await add_tenant(env, "beta")
    await add_tenant(env, "gamma", max_env="FS_TEST_MAX_OTHER")   # другой бот
    await start_rt(env, fake)
    await env.publish("alpha", _two_step("fa", claim=True))
    await env.publish("beta", _two_step("fb", claim=True))
    uid = 601
    await upd(env, bot_started(uid, "alpha_x"))
    await env.run_for(0)
    btn = fake.buttons(fake.of(uid)[0])[0]
    assert btn["payload"] == f"f:alpha:c:{sid('fa', 'a1').hex}"
    hexid = sid("fa", "a1").hex
    bot = env.rt.max_hub.bots[min(env.rt.max_hub.bots)]
    handle = env.rt.max_hub.handle_update
    # кнопка со слагом beta: уходит в beta, в alpha ничего не трогает
    assert await handle(bot, callback(uid, f"f:beta:c:{hexid}")) == "callback"
    # кнопка запуска, которого у этого бота нет, и кнопка без слага – мимо воронок
    assert await handle(bot, callback(uid, f"f:gamma:c:{hexid}")) == "foreign"
    assert fake.answers[-1]["notification"] == "Эта кнопка устарела"
    assert await handle(bot, callback(uid, f"c:{hexid}")) == "ignored"
    pend = await env.pending(uid=uid)
    assert [r["step_key"] for r in pend] == ["a2"]            # цепочка alpha цела
    await env.run_for(1)
    assert len(fake.of(uid)) == 1                             # a3 не ушёл никому
    # своя кнопка – работает: a3 сразу, a2 отменён
    assert await handle(bot, callback(uid, btn["payload"])) == "callback"
    await env.run_for(700)
    assert [c["text"] for c in fake.of(uid)] == ["fa: привет, Маша", "fa: по кнопке"]


# ── контент MAX ──────────────────────────────────────────────────────────────

async def test_max_content_semantics(env, fake, tmp_path):
    media = tmp_path / "data" / "media"
    media.mkdir(parents=True)
    (media / "pic.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 64)
    (media / "vn.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64)
    settings = {"channel": {"link": "https://t.me/+tgchan", "max_link": "https://max.ru/join/mx"},
                "webinar": {"stream_url": "https://live.example/tg", "stream_url_max": "https://live.example/max"}}
    await add_tenant(env, "cnt", settings=settings, default=True, tg=True)
    await start_rt(env, fake)
    f = mk_funnel("fc", [
        {"key": "s1", "text": "<b>Канал</b>: <a href=\"{channel.link}\">тут</a> "
                              "<tg-time unix=\"1\">завтра</tg-time><blockquote>цитата</blockquote>",
         "buttons": [{"text": "Эфир", "url": "{stream_url}"}, {"text": "TG-кнопка", "callback_data": "gm:x"}]},
        {"key": "s2", "type": "photo", "text": "подпись только для TG", "media": {"image": "./static/pic.jpg"},
         "buttons": [{"text": "Канал", "url": "{channel.link}"}]},
        {"key": "s3", "type": "video_note", "text": "то, что говорю в кружке",
         "media": {"video_note": "./static/vn.mp4"}},
        {"key": "s4", "text": "общий текст", "claim": "Забрать"},
        {"key": "s5", "text": "только TG"},
        {"key": "s6", "type": "video", "text": "ролик", "media": {"video": "AgADfileidTelegram1234567890"}},
    ], [("s1", "done", "s2", 1), ("s2", "done", "s3", 1), ("s3", "done", "s4", 1),
        ("s4", "callback", "s5", None), ("s4", "done", "s5", 1), ("s5", "done", "s6", 1)],
        max_video_note_caption=False)
    st = {s.key: s for s in f.steps}
    st["s2"].content.max = MaxContent(text="", buttons=[])            # в MAX ни подписи, ни кнопок
    st["s4"].content.max = MaxContent(claim_button_text="Забрать в MAX",
                                      buttons=[Button(text="Своя", callback_data="f:cnt:gm:1")])
    st["s5"].content.variants = [StepVariant(when=CohortMatch(utm_source="vk"), text_max="MAX для vk")]
    await env.publish("cnt", f)

    await upd(env, bot_started(701, "cnt_reg_vk_a"))
    await env.start_user("cnt", 702, "reg_vk_a")                       # тот же запуск в Telegram
    await env.run_for(30)
    m = fake.of(701)
    s1, s2, s3, s4, s5 = m[0], m[1], m[2], m[3], m[4]
    assert 'href="https://max.ru/join/mx"' in s1["text"]
    assert "<tg-time" not in s1["text"] and "<blockquote" not in s1["text"] and "цитата" in s1["text"]
    assert [(b["type"], b.get("url")) for b in fake.buttons(s1)] == [("link", "https://live.example/max")]
    assert fake.media(s2) == ["image"] and s2["text"] == "" and fake.buttons(s2) == []
    assert fake.media(s3) == ["video"] and s3["text"] == ""            # кружок без подписи
    assert [(b["type"], b["text"], b.get("payload")) for b in fake.buttons(s4)] == [
        ("callback", "Забрать в MAX", f"f:cnt:c:{sid('fc', 's4').hex}"),
        ("callback", "Своя", "f:cnt:gm:1")]
    assert s4["text"] == "общий текст"
    assert s5["text"] == "MAX для vk"
    # s6: file_id Telegram в MAX не годится – шаг пропущен, цепочка дошла до конца
    rows = await sent_rows(env, 701)
    assert rows[-1]["step_key"] == "s6" and rows[-1]["status"] == "ok" and not rows[-1]["ext_message_ids"]
    assert len(m) == 5
    # Telegram того же запуска получил свою версию
    tg = env.tg.sends(702)
    assert "https://t.me/+tgchan" in tg[0]["text"]
    assert tg[1]["text"] == "подпись только для TG"
    assert tg[4]["text"] == "только TG"


async def test_video_not_ready_retry_and_cache(env, fake, tmp_path):
    media = tmp_path / "data" / "media"
    media.mkdir(parents=True)
    (media / "v.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"1" * 64)
    await add_tenant(env, "vid", default=True)
    await start_rt(env, fake)
    await env.publish("vid", mk_funnel("fv", [{"key": "v", "type": "video", "text": "смотри",
                                               "media": {"video": "./static/v.mp4"}}], []))
    fake.not_ready_once.add("vtok1")      # первый же залитый ролик «ещё обрабатывается»
    await upd(env, bot_started(801, ""))
    await env.run_for(0)
    assert fake.media(fake.of(801)[0]) == ["video"]
    await upd(env, bot_started(802, ""))
    await env.run_for(0)
    assert len(fake.uploads) == 1         # токен видео – из кэша
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM media_cache WHERE kind='max_video'") == 1


# ── блокировка ───────────────────────────────────────────────────────────────

async def test_max_block_by_send_error_and_bot_stopped(env, fake):
    await add_tenant(env, "alpha", default=True)
    await add_tenant(env, "beta")
    await start_rt(env, fake)
    await env.publish("alpha", _two_step("fa"))
    await env.publish("beta", _two_step("fb"))
    # 403 при отправке = человек недоступен навсегда
    fake.fail_rules.append(lambda c: (403, {"code": "chat.denied", "message": "dialog.suspended"})
                           if c["user_id"] == 901 else None)
    await upd(env, bot_started(901, "beta_x"))
    await env.run_for(0)
    async with env.pool.acquire() as c:
        p = await c.fetchrow("SELECT blocked FROM participants WHERE uid=901")
        assert p["blocked"]
        assert await c.fetchval("SELECT count(*) FROM jobs WHERE uid=901 AND status='pending'") == 0
    # bot_stopped – блок во всех запусках этого бота
    await upd(env, bot_started(902, "alpha_x"))
    await upd(env, bot_started(902, "beta_x"))
    await env.run_for(0)
    assert await upd(env, stopped(902)) == "blocked"
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT bool_and(blocked) FROM participants WHERE uid=902")
        assert await c.fetchval("SELECT count(*) FROM jobs WHERE uid=902 AND status='pending'") == 0
    await env.run_for(700)
    assert len(fake.of(902)) == 2            # вторые шаги не ушли
    # новый вход снимает блок
    await upd(env, bot_started(902, "beta_again"))
    async with env.pool.acquire() as c:
        assert not await c.fetchval(
            "SELECT blocked FROM participants p JOIN tenants t ON t.id=p.tenant_id "
            "WHERE uid=902 AND t.slug='beta'")


# ── обычное сообщение и long-poll ────────────────────────────────────────────

async def test_text_goes_to_awaiting_tenant_and_poll_marker(env, fake):
    await add_tenant(env, "alpha", default=True)
    await add_tenant(env, "beta")
    await start_rt(env, fake)
    await env.publish("alpha", _two_step("fa"))
    await env.publish("beta", mk_funnel("fe", [{"key": "e1", "type": "email_request", "text": "почта?"},
                                               {"key": "e2", "text": "спасибо за почту"}],
                                        [("e1", "email", "e2", 0), ("e1", "skip", "e2", 600)]))
    uid = 1001
    fake.updates += [bot_started(uid, "beta_x"), bot_started(uid, "alpha_y")]
    bot = the_bot(env)
    assert await env.rt.max_hub.poll_once(bot, timeout=0) == 2
    assert bot.marker == 2 and "bot_started" in fake.polls[-1]["types"]
    await env.run_for(0)
    # alpha свежее, но почту ждёт beta – текст уходит туда
    fake.updates.append(message(uid, "Masha@Example.ru"))
    assert await env.rt.max_hub.poll_once(bot, timeout=0) == 1
    await env.run_for(1)
    assert fake.of(uid)[-1]["text"] == "спасибо за почту"
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT poll_marker FROM bots WHERE id=$1", bot.row_id) == 3
        em = await c.fetchval("SELECT email FROM participants p JOIN tenants t ON t.id=p.tenant_id "
                              "WHERE uid=$1 AND t.slug='beta'", uid)
    assert em == "masha@example.ru"
    # рестарт: маркер берётся из базы, старые апдейты второй раз не приходят
    await env.rt.close()
    await start_rt(env, fake)
    assert the_bot(env).marker == 3
    assert await env.rt.max_hub.poll_once(the_bot(env), timeout=0) == 0


async def test_cli_links_shared_bot(env, fake, monkeypatch):
    """tenant add --max-bot-env у второго запуска = тот же (общий) бот."""
    import argparse

    from runtime.cli import _link_max_bot
    async with env.pool.acquire() as c:
        t1 = await c.fetchval("INSERT INTO tenants (slug) VALUES ('one') RETURNING id")
        t2 = await c.fetchval("INSERT INTO tenants (slug) VALUES ('two') RETURNING id")
        a = argparse.Namespace(max_bot_env="FS_SHARED", max_prefix=None, max_default=False, slug="one")
        await _link_max_bot(c, t1, a)
        a2 = argparse.Namespace(max_bot_env="FS_SHARED", max_prefix=None, max_default=False, slug="two")
        msg = await _link_max_bot(c, t2, a2)
        rows = await c.fetch("SELECT tenant_id, prefix, is_default FROM bot_tenants ORDER BY tenant_id")
        assert await c.fetchval("SELECT count(*) FROM bots WHERE platform='max'") == 1
    assert "общий на 2" in msg
    assert [(r["tenant_id"], r["prefix"], r["is_default"]) for r in rows] == \
        [(t1, "one_", True), (t2, "two_", False)]
    from runtime.status import collect, render
    st = await collect(env.pool, "two", clock=env.clock)
    assert "метка «two_»" in render(st) and "общий на 2" in render(st)


async def test_max_channel_gate(env, fake):
    """Гейт канала в MAX: участники канала через API MAX; бот не админ – fail-open."""
    await add_tenant(env, "ch", settings={"channel": {"max_chat_id": -555}}, default=True)
    await start_rt(env, fake)
    await env.publish("ch", mk_funnel("fg", [
        {"key": "g1", "text": "привет"},
        {"key": "g2", "text": "вступи в канал", "conditions": {"channel": "not_joined"}},
        {"key": "g3", "text": "финал"}], [("g1", "done", "g2", 1), ("g2", "done", "g3", 1)]))
    fake.members[(-555, 1101)] = True
    fake.members[(-555, 1102)] = False
    for uid in (1101, 1102):
        await upd(env, bot_started(uid, ""))
    await env.run_for(10)
    assert [c["text"] for c in fake.of(1101)] == ["привет", "финал"]
    assert [c["text"] for c in fake.of(1102)] == ["привет", "вступи в канал", "финал"]
    # канал, где бот не админ (403) – проверить нельзя, шаг уходит, в actions – «не проверен»
    fake.members.clear()
    fake.members[(-777, 1)] = True
    await upd(env, bot_started(1103, ""))
    await env.run_for(10)
    assert len(fake.of(1103)) == 3
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM actions WHERE uid=1103 AND action='gate_unchecked'") == 1
