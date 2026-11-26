"""Живой тест владельца 24.09.2026: «Проверить в боте», пустые шаги, повторный /start,
стартовая воронка и состояние «в боте» для шапки Студии."""
from __future__ import annotations

import warnings

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from rt_helpers import TEST_DSN, mk_funnel
from runtime import studio_test
from studio.server import create_app

async def sent(env, uid) -> list[str]:
    async with env.pool.acquire() as c:
        return [r["step_key"] for r in await c.fetch(
            "SELECT step_key FROM sent_messages WHERE uid=$1 AND status='ok' ORDER BY id", uid)]


async def actions(env, uid, action) -> list:
    async with env.pool.acquire() as c:
        return await c.fetch("SELECT * FROM actions WHERE uid=$1 AND action=$2 ORDER BY id", uid, action)


def main_funnel():
    return mk_funnel("main", [{"key": "m1"}, {"key": "m2"}], [("m1", "done", "m2", 3600)])


def f2_funnel():
    return mk_funnel("f2", [{"key": "a"}, {"key": "b"}, {"key": "c"}],
                     [("a", "done", "b", 30), ("b", "done", "c", 3600)])


# ── служебный payload ───────────────────────────────────────────────────────

@pytest.mark.filterwarnings("ignore")
def test_payload_fits_telegram():
    assert studio_test.payload_for("e2e_funnel") == "studio_test_e2e_funnel"
    assert studio_test.match("studio_test_e2e_funnel", ["x", "e2e_funnel"]) == "e2e_funnel"
    odd = "воронка с пробелами"
    p = studio_test.payload_for(odd)
    assert p.startswith("studio_th_") and len(p) <= 64 and p.isascii()
    assert studio_test.match(p, ["x", odd]) == odd
    long_id = "f" * 60
    assert len(studio_test.payload_for(long_id)) <= 64
    assert studio_test.match(studio_test.payload_for(long_id), [long_id]) == long_id
    assert studio_test.match("studio_test_nope", ["x"]) is None
    assert studio_test.link("@bot_x", "f") == "https://t.me/bot_x?start=studio_test_f"


# ── «Проверить в боте» ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_studio_test_start_runs_this_funnel_from_scratch(env):
    """/start бота ведёт в main; studio_test_f2 – в f2, хоть она и не стартовая. Повтор через 10 с
    не упирается в анти-дубль 90 с: ожидающие задачи f2 отменяются, проход идёт с начала."""
    await env.tenant("t1", settings={"entry_funnel": "main"})
    await env.start()
    await env.publish("t1", main_funnel())
    await env.publish("t1", f2_funnel())

    assert await env.start_user("t1", 1) == "started"
    await env.run_for(5)
    assert await sent(env, 1) == ["m1"]

    assert await env.start_user("t1", 1, studio_test.payload_for("f2")) == "started"
    await env.run_for(40)
    assert await sent(env, 1) == ["m1", "a", "b"]
    # второй раз через 10 с – снова с начала (при политике engine обычный /start был бы проигнорирован)
    assert await env.start_user("t1", 1, "studio_test_f2") == "started"
    await env.run_for(10)
    assert await sent(env, 1) == ["m1", "a", "b", "a"]
    assert not await actions(env, 1, "dup_skip")
    async with env.pool.acquire() as c:
        # старый таймер «c» первой проверки отменён, новый проход дойдёт до конца
        cancelled = await c.fetchval("SELECT count(*) FROM jobs WHERE uid=1 AND funnel_id='f2' "
                                     "AND step_key='c' AND status='cancelled'")
        part = await c.fetchrow("SELECT utm_source, trigger, first_payload FROM participants WHERE uid=1")
    assert cancelled == 1
    assert part["utm_source"] is None and part["first_payload"] is None   # UTM не из служебного входа
    await env.run_for(4000)
    assert await sent(env, 1) == ["m1", "a", "b", "a", "b", "m2", "c"]   # m2 – своя воронка, своим ходом
    fresh = await actions(env, 1, "start_fresh")
    assert len(fresh) == 2 and fresh[0]["funnel_id"] == "f2" and "Студии" in fresh[0]["payload"]


@pytest.mark.asyncio
async def test_studio_test_unknown_funnel_is_plain_start(env):
    await env.tenant("t1")
    await env.start()
    await env.publish("t1", main_funnel())
    assert await env.start_user("t1", 1, "studio_test_nope") == "started"
    await env.run_for(5)
    assert await sent(env, 1) == ["m1"]


# ── повторный /start ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restart_policy_repeat_start_not_eaten_by_dedup(env):
    """restart: второй /start через 10 с шлёт первый шаг снова (раньше – молчание анти-дубля)."""
    f = mk_funnel("f", [{"key": "a"}, {"key": "b"}], [("a", "done", "b", 3600)])
    await env.tenant("t1", settings={"restart_policy": "restart"})
    await env.start()
    await env.publish("t1", f)
    await env.start_user("t1", 1)
    await env.run_for(10)
    assert await env.start_user("t1", 1) == "started"
    await env.run_for(10)
    assert await sent(env, 1) == ["a", "a"]
    assert not await actions(env, 1, "dup_skip")
    await env.run_for(4000)
    assert await sent(env, 1) == ["a", "a", "b"]      # таймер первого прохода отменён


@pytest.mark.asyncio
async def test_engine_policy_ignored_start_is_explained(env):
    f = mk_funnel("f", [{"key": "a"}, {"key": "b"}], [("a", "done", "b", 3600)])
    await env.tenant("t1")
    await env.start()
    await env.publish("t1", f)
    await env.start_user("t1", 1)
    await env.run_for(10)
    assert await env.start_user("t1", 1) == "ignored"
    ign = await actions(env, 1, "start_ignored")
    assert len(ign) == 1 and "меньше 90 с" in ign[0]["payload"]


# ── пустые шаги ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_step_is_recorded_and_chain_goes_on(env, caplog):
    f = mk_funnel("f", [{"key": "a"}, {"key": "empty", "text": "  <b> </b> "}, {"key": "c"}],
                  [("a", "done", "empty", 5), ("empty", "done", "c", 5)])
    await env.tenant("t1")
    await env.start()
    rep = await env.publish("t1", f)
    assert any("«empty» пустой – бот ничего не отправит" in w for w in rep.warnings)
    with caplog.at_level("WARNING", logger="runtime.executor"):
        await env.start_user("t1", 1)
        await env.run_for(60)
    assert [c["text"] for c in env.tg.sends(1)] == ["текст a", "текст c"]
    emp = await actions(env, 1, "empty_step")
    assert len(emp) == 1 and emp[0]["step_key"] == "empty" and "ничего не отправлено" in emp[0]["payload"]
    assert any("пустой" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_photo_without_image_is_a_warning_only(env):
    f = mk_funnel("f", [{"key": "p", "type": "photo", "title": "Картинка"}], [])
    await env.tenant("t1")
    rep = await env.publish("t1", f)
    assert any("Картинка" in w and "картинки нет" in w for w in rep.warnings)


# ── сервер Студии: состояние «в боте», стартовая воронка, пустой шаг ───────────

NAME = "t1__f2"


def studio(tmp_path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path / "studio", dist_dir=tmp_path / "no_dist",
                                 runtime_dsn=TEST_DSN))


def put(c: TestClient, f) -> None:
    if c.get(f"/api/funnels/{NAME}").status_code == 404:
        assert c.post("/api/funnels", json={"launch": "t1", "name": "f2", "id": f.id}).status_code == 200
    r = c.put(f"/api/funnels/{NAME}", json=f.model_dump(mode="json", by_alias=True))
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_studio_bot_state_test_link_and_entry(env, tmp_path):
    await env.tenant("t1", settings={"entry_funnel": "main"})
    async with env.pool.acquire() as c:
        await c.execute("UPDATE bots SET username='funnel_studio_TEST_bot', secret_ref='env:STUDIO_TEST_TG_TOKEN'")
    await env.publish("t1", main_funnel())
    c = studio(tmp_path)
    put(c, f2_funnel())
    info = c.get("/api/engine").json()
    t1 = info["tenants"][0]
    assert t1["bots"] == [{"platform": "tg", "username": "funnel_studio_TEST_bot", "enabled": True, "test": True}]
    assert t1["entry_funnel"] == "main" and t1["entry_explicit"]
    c.put(f"/api/funnels/{NAME}/engine", json={"tenant": "t1"})
    got = c.get(f"/api/funnels/{NAME}/engine").json()
    assert got["in_bot"] is None and got["test_link"] is None and not got["is_entry"]

    r = c.post(f"/api/funnels/{NAME}/publish", json={
        "comment": "", "author": "Alex", "engine": {"tenant": "t1", "make_entry": True}})
    assert r.status_code == 200, r.text
    assert r.json()["engine"]["ok"] and r.json()["engine"]["entry"] == {"ok": True, "funnel_id": "f2"}
    got = c.get(f"/api/funnels/{NAME}/engine").json()
    assert got["in_bot"]["studio_number"] == 1 and got["in_bot"]["engine_version"] == 1
    assert got["is_entry"]
    assert got["test_link"] == "https://t.me/funnel_studio_TEST_bot?start=studio_test_f2"
    async with env.pool.acquire() as cn:
        assert await cn.fetchval("SELECT settings->>'entry_funnel' FROM tenants WHERE slug='t1'") == "f2"


@pytest.mark.asyncio
async def test_combat_bot_is_not_test(env, tmp_path):
    """Бот боевого запуска bot-engine (username из launch.yaml) тестовым не считается."""
    repo = tmp_path / "repo"
    (repo / "launches" / "extra").mkdir(parents=True)
    (repo / "launches" / "extra" / "launch.yaml").write_text(
        "bot:\n  tg:\n    username: northwind_extra_bot\n", encoding="utf-8")
    await env.tenant("t1")
    async with env.pool.acquire() as c:
        await c.execute("UPDATE bots SET username='northwind_extra_bot', secret_ref='env:STUDIO_TEST_X'")
    app = create_app(data_dir=tmp_path / "studio", repo_root=repo, dist_dir=tmp_path / "no_dist",
                     runtime_dsn=TEST_DSN)
    info = TestClient(app).get("/api/engine").json()
    assert info["tenants"][0]["bots"][0]["test"] is False


@pytest.mark.asyncio
async def test_empty_step_blocks_launch(env, tmp_path):
    await env.tenant("t1")
    c = studio(tmp_path)
    f = mk_funnel("f2", [{"key": "a", "title": "Текст", "text": ""},
                         {"key": "p", "type": "photo", "title": "Фото"}], [("a", "done", "p", 5)])
    put(c, f)
    chk = c.post(f"/api/funnels/{NAME}/publish/check").json()
    empty = [b for b in chk["blockers"] if b["kind"] == "empty"]
    assert [b["text"] for b in empty] == ["Шаг «Текст» пустой – бот ничего не отправит"]
    assert any("картинки нет" in w["text"] for w in chk["step_warnings"])
    r = c.post(f"/api/funnels/{NAME}/publish", json={"comment": "", "engine": {"tenant": "t1"}})
    assert r.status_code == 409 and r.json()["blockers"][0]["kind"] == "empty"


@pytest.mark.asyncio
async def test_bot_state_when_published_from_cli(env, tmp_path):
    """Версию отправили в бот командной строкой (журнала Студии нет) – шапка всё равно знает, в каком
    она боте и какой это номер Студии (так было на сервере 24.09: studio:…@v4 → движок v2)."""
    from runtime.publish import StudioVersionSource
    await env.tenant("t1", settings={"entry_funnel": "f2"})
    async with env.pool.acquire() as c:
        await c.execute("UPDATE bots SET username='funnel_studio_TEST_bot'")
    c = studio(tmp_path)
    put(c, f2_funnel())
    assert c.post(f"/api/funnels/{NAME}/publish", json={"comment": "только Студия"}).status_code == 200
    app_store = c.app.state.store
    src = StudioVersionSource(app_store)
    await env.publish("t1", src.load(NAME), source=src.describe(NAME))
    got = c.get(f"/api/funnels/{NAME}/engine").json()
    assert got["tenant"] == "t1" and got["is_entry"]
    assert got["in_bot"]["studio_number"] == 1 and got["in_bot"]["engine_version"] == 1
    assert got["test_link"] == "https://t.me/funnel_studio_TEST_bot?start=studio_test_f2"
