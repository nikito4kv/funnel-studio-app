"""Кнопка «Опубликовать» Студии → движок: сервер Студии на настоящем Postgres движка.

Сценарии: движок не подключён / не отвечает; выбор запуска запоминается; пробная
публикация с отчётом по живым людям; одна операция «версия Студии + движок» с
NOTIFY и горячей подменой; отказ движка не создаёт версию; карта перенаправления
из подсказки; движок отказал уже после записи версии – журнал честный, повтор;
откат = публикация прошлой версии; источник графа – хранилище версий Студии.
"""
from __future__ import annotations

import asyncio
import warnings

import asyncpg
import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from rt_helpers import TEST_DSN, mk_funnel, sid
from runtime.db import CH_VERSIONS
from runtime.publish import PublishError, StudioStorageSource, StudioVersionSource
from studio.server import create_app

pytestmark = pytest.mark.asyncio
NAME = "t1__f"


def v1():
    return mk_funnel("f", [{"key": "a"}, {"key": "b"}, {"key": "c"}],
                     [("a", "done", "b", 3600), ("b", "done", "c", 3600)])


def studio(tmp_path, dsn: str | None = TEST_DSN) -> TestClient:
    app = create_app(data_dir=tmp_path / "studio", dist_dir=tmp_path / "no_dist", runtime_dsn=dsn or "")
    return TestClient(app)


def put(c: TestClient, f) -> None:
    if c.get(f"/api/funnels/{NAME}").status_code == 404:
        assert c.post("/api/funnels", json={"launch": "t1", "name": "f", "id": f.id}).status_code == 200
    r = c.put(f"/api/funnels/{NAME}", json=f.model_dump(mode="json", by_alias=True))
    assert r.status_code == 200, r.text


def publish(c: TestClient, *, tenant="t1", mode="new-only", redirects=None, comment="", author="Alex"):
    return c.post(f"/api/funnels/{NAME}/publish", json={
        "comment": comment, "author": author,
        "engine": {"tenant": tenant, "mode": mode, "redirects": redirects or {}}})


async def synced(env, slug: str, version: int) -> None:
    """Движок (env.rt, rt.listen()) подхватил опубликованную из Студии версию по NOTIFY."""
    tid = env.rt.registry.by_slug(slug).id
    for _ in range(100):
        if env.rt.registry.current_version(tid, "f") == version:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"движок не подхватил v{version}")


async def versions(env) -> list[asyncpg.Record]:
    async with env.pool.acquire() as c:
        return await c.fetch("SELECT version, source, mode, published_by, comment FROM funnel_versions "
                             "ORDER BY version")


# ── подключение ──────────────────────────────────────────────────────────────

async def test_engine_not_configured_keeps_stage2(env, tmp_path):
    c = studio(tmp_path, dsn=None)
    info = c.get("/api/engine").json()
    assert info["configured"] is False and "не подключён" in info["error"]
    put(c, v1())
    r = publish(c)
    assert r.status_code == 409 and "не подключён" in r.json()["message"]
    assert c.get(f"/api/funnels/{NAME}/versions").json()["published"] is None
    # без движка – как на этапе 2
    r = c.post(f"/api/funnels/{NAME}/publish", json={"comment": "только Студия"})
    assert r.status_code == 200 and r.json()["number"] == 1 and "engine" not in r.json()


async def test_engine_down_is_explained(env, tmp_path):
    c = studio(tmp_path, dsn="postgresql://fs:fsruntime@127.0.0.1:1/nope")
    info = c.get("/api/engine").json()
    assert info["configured"] and "не отвечает" in info["error"] and info["where"] == "127.0.0.1:1/nope"
    put(c, v1())
    r = c.post(f"/api/funnels/{NAME}/engine/check", json={"tenant": "t1"})
    assert r.status_code == 503 and "не отвечает" in r.json()["message"]


async def test_tenants_and_remembered_target(env, tmp_path):
    await env.tenant("t1")
    await env.tenant("t2")
    c = studio(tmp_path)
    put(c, v1())
    info = c.get("/api/engine").json()
    assert [t["slug"] for t in info["tenants"]] == ["t1", "t2"] and info["error"] is None
    assert c.get(f"/api/funnels/{NAME}/engine").json()["tenant"] is None
    assert c.put(f"/api/funnels/{NAME}/engine", json={"tenant": "t2"}).status_code == 200
    got = c.get(f"/api/funnels/{NAME}/engine").json()
    assert got["tenant"] == "t2" and got["funnel_id"] == "f" and got["state"]["versions"] == []


# ── публикация ───────────────────────────────────────────────────────────────

async def test_publish_goes_to_engine_with_notify(env, tmp_path):
    await env.tenant("t1")
    rt = await env.start()
    await rt.listen()
    heard: list[str] = []
    lis = await asyncpg.connect(TEST_DSN)
    await lis.add_listener(CH_VERSIONS, lambda *a: heard.append(a[3]))
    try:
        c = studio(tmp_path)
        put(c, v1())
        chk = c.post(f"/api/funnels/{NAME}/engine/check", json={"tenant": "t1"}).json()
        assert chk["ok"] and chk["report"]["to_version"] == 1 and chk["report"]["dry_run"]
        assert not await versions(env)                       # пробный прогон ничего не пишет
        r = publish(c, comment="первая", author="Alex")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["number"] == 1 and body["engine"]["ok"] and body["engine"]["engine_version"] == 1
        rows = await versions(env)
        assert [(x["version"], x["source"], x["mode"], x["published_by"], x["comment"]) for x in rows] == \
            [(1, "studio:t1__f@v1", "new-only", "Alex", "первая")]
        tid = rt.registry.by_slug("t1").id
        for _ in range(100):
            if heard and rt.registry.current_version(tid, "f") == 1:
                break
            await asyncio.sleep(0.05)
        assert heard == [f"{tid}:f:1"] and rt.registry.current_version(tid, "f") == 1
        # история Студии: в какой запуск и каким режимом ушла версия; запуск запомнился
        info = c.get(f"/api/funnels/{NAME}/versions").json()
        sent = info["versions"][0]["engine"]
        assert info["engine_target"] == "t1"
        assert [(x["tenant"], x["mode"], x["status"], x["engine_version"]) for x in sent] == \
            [("t1", "new-only", "ok", 1)]
    finally:
        await lis.close()


async def test_report_counts_live_people_and_new_only(env, tmp_path):
    await env.tenant("t1")
    rt = await env.start()
    await rt.listen()
    c = studio(tmp_path)
    put(c, v1())
    assert publish(c).status_code == 200
    await synced(env, "t1", 1)
    for uid in (1, 2, 3):
        await env.start_user("t1", uid)
    await env.run_for(60)                                  # a ушёл, b ждёт у троих
    f2 = v1()
    f2.steps[1].content.tg.text = "новый b"
    put(c, f2)
    chk = c.post(f"/api/funnels/{NAME}/engine/check", json={"tenant": "t1"}).json()
    rep = chk["report"]
    assert chk["ok"] and rep["positions_active"] == 3 and rep["pending_total"] == 3
    line = next(x for x in rep["lines"] if x["key"] == "b")
    assert line["pending"] == 3 and line["status"] == "changed"
    assert "у 3 чел. запланирован этот шаг" in chk["text"]
    r = publish(c)
    assert r.status_code == 200 and r.json()["engine"]["engine_version"] == 2
    async with env.pool.acquire() as cn:          # «только новым»: задачи остались на v1
        assert await cn.fetchval("SELECT count(*) FROM jobs WHERE status='pending' AND version=1") == 3


async def test_engine_refusal_blocks_publish_and_redirect_from_suggestion(env, tmp_path):
    """В движке – версия с шагом «old» (например, опубликована из CLI), у людей он запланирован.
    В Студии такого шага нет: «перевести всех» без карты – отказ, версия Студии не создаётся;
    подсказка карты ведёт на следующий шаг; с ней публикация проходит и людей переносит."""
    await env.tenant("t1")
    await env.start()
    cli = mk_funnel("f", [{"key": "a"}, {"key": "old"}, {"key": "b"}, {"key": "c"}],
                    [("a", "done", "old", 3600), ("old", "done", "b", 60), ("b", "done", "c", 3600)])
    await env.publish("t1", cli)
    await env.start_user("t1", 1)
    await env.start_user("t1", 2)
    await env.run_for(60)
    c = studio(tmp_path)
    put(c, v1())
    chk = c.post(f"/api/funnels/{NAME}/engine/check", json={"tenant": "t1", "mode": "migrate"}).json()
    assert not chk["ok"] and "шаг «old» удалён" in chk["error"] and "у 2 чел. запланирован" in chk["error"]
    assert "--map" not in chk["error"] and "карта – ниже" in chk["error"]      # подсказка CLI – не для диалога
    old_id, b_id = str(sid("f", "old")), str(sid("f", "b"))
    assert chk["suggestions"] == {old_id: b_id}
    line = next(x for x in chk["report"]["lines"] if x["key"] == "old")
    assert line["status"] == "removed" and line["pending"] == 2
    r = publish(c, mode="migrate")
    assert r.status_code == 409 and r.json()["message"].startswith("Движок отказал")
    assert r.json()["engine"]["suggestions"] == {old_id: b_id}
    info = c.get(f"/api/funnels/{NAME}/versions").json()
    assert info["published"] is None and info["versions"] == []           # Студия ничего не записала
    assert [x["version"] for x in await versions(env)] == [1]
    # «только новым» тот же шаг – предупреждение, не отказ
    chk2 = c.post(f"/api/funnels/{NAME}/engine/check", json={"tenant": "t1"}).json()
    assert chk2["ok"] and any("old" in w for w in chk2["report"]["warnings"])
    # с картой из подсказки
    r = publish(c, mode="migrate", redirects=chk["suggestions"])
    assert r.status_code == 200, r.text
    eng = r.json()["engine"]
    assert eng["ok"] and eng["engine_version"] == 2 and eng["report"]["migrated"] == 2
    assert eng["redirects"] == {old_id: b_id}
    async with env.pool.acquire() as cn:
        rows = await cn.fetch("SELECT step_key, version FROM jobs WHERE status='pending'")
    assert sorted((x["step_key"], x["version"]) for x in rows) == [("b", 2), ("b", 2)]


async def test_engine_fails_after_studio_version_is_honest_and_retry(env, tmp_path, monkeypatch):
    await env.tenant("t1")
    c = studio(tmp_path)
    put(c, v1())
    eng = c.app.state.engine

    def boom(*a, **kw):
        raise PublishError("гонка: за это время что-то поменялось")
    monkeypatch.setattr(eng, "publish", boom)
    r = publish(c, comment="v1")
    assert r.status_code == 200
    body = r.json()
    assert body["number"] == 1 and body["engine"]["ok"] is False and "гонка" in body["engine"]["error"]
    info = c.get(f"/api/funnels/{NAME}/versions").json()
    assert info["published"] == 1                                         # в Студии – опубликована
    assert [(x["status"], x["engine_version"]) for x in info["versions"][0]["engine"]] == [("failed", None)]
    assert not await versions(env)                                        # в движке – нет
    monkeypatch.undo()
    # повтор: отправить опубликованную v1
    r = c.post(f"/api/funnels/{NAME}/versions/1/engine", json={"tenant": "t1", "author": "Alex"})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["engine_version"] == 1
    info = c.get(f"/api/funnels/{NAME}/versions").json()
    assert [x["status"] for x in info["versions"][0]["engine"]] == ["failed", "ok"]
    # не опубликованную версию – нельзя
    assert c.post(f"/api/funnels/{NAME}/versions/7/engine", json={"tenant": "t1"}).status_code == 409


async def test_rollback_is_publish_of_previous_version(env, tmp_path):
    await env.tenant("t1")
    c = studio(tmp_path)
    put(c, v1())
    assert publish(c, comment="v1").status_code == 200
    f2 = v1()
    f2.steps[0].content.tg.text = "поменяли a"
    put(c, f2)
    assert publish(c, comment="v2").status_code == 200
    assert c.post(f"/api/funnels/{NAME}/rollback", json={"version": 1}).status_code == 200
    r = publish(c, comment="откат к v1")
    assert r.status_code == 200 and r.json()["number"] == 3 and r.json()["engine"]["engine_version"] == 3
    async with env.pool.acquire() as cn:
        g1, g3 = [await cn.fetchval("SELECT graph FROM funnel_versions WHERE version=$1", v) for v in (1, 3)]
    assert g3["steps"] == g1["steps"] and g3["edges"] == g1["edges"]
    assert [x["source"] for x in await versions(env)] == \
        ["studio:t1__f@v1", "studio:t1__f@v2", "studio:t1__f@v3"]


async def test_published_source_reads_studio_versions(env, tmp_path):
    c = studio(tmp_path, dsn=None)
    put(c, v1())
    assert c.post(f"/api/funnels/{NAME}/publish", json={}).status_code == 200
    f2 = v1()
    f2.steps[0].content.tg.text = "черновик"
    put(c, f2)                                  # черновик отличается от опубликованной
    data = tmp_path / "studio"
    src = StudioVersionSource(data)
    assert src.load(NAME).steps[0].content.tg.text == "текст a"      # опубликованная, не черновик
    assert src.describe(NAME) == "studio:t1__f@v1"
    assert src.load(NAME + "@1").id == "f"
    assert StudioStorageSource(data).load(NAME).steps[0].content.tg.text == "черновик"
    with pytest.raises(FileNotFoundError):
        StudioVersionSource(data).load("t1__nope")


async def test_report_shows_who_waits_for_contact(env, tmp_path):
    await env.tenant("t1")
    rt = await env.start()
    await rt.listen()
    f = mk_funnel("f", [{"key": "ask", "type": "phone_request"}, {"key": "ok"}],
                  [("ask", "contact", "ok", 0)])
    c = studio(tmp_path)
    put(c, f)
    assert publish(c).status_code == 200
    await synced(env, "t1", 1)
    await env.start_user("t1", 1)
    await env.start_user("t1", 2)
    await env.run_for(1)
    chk = c.post(f"/api/funnels/{NAME}/engine/check", json={"tenant": "t1"}).json()
    line = next(x for x in chk["report"]["lines"] if x["key"] == "ask")
    assert line["awaiting"] == 2 and line["awaiting_kinds"] == {"contact": 2}
