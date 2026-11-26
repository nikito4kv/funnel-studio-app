"""Публикация без рестарта и люди, которые уже внутри воронки."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from rt_helpers import mk_funnel
from runtime.publish import PublishError, publish

pytestmark = pytest.mark.asyncio


def v1():
    return mk_funnel("f", [{"key": "a"}, {"key": "b"}, {"key": "c"}],
                     [("a", "done", "b", 3600), ("b", "done", "c", 3600)])


async def sent(env, uid):
    async with env.pool.acquire() as c:
        return [(r["step_key"], r["version"]) for r in await c.fetch(
            "SELECT step_key, version FROM sent_messages WHERE uid=$1 AND status='ok' ORDER BY id", uid)]


async def test_removed_step_with_pending_needs_redirect(env):
    await env.tenant("t1")
    await env.start()
    await env.publish("t1", v1())
    await env.start_user("t1", 1)
    await env.start_user("t1", 2)
    await env.run_for(60)        # a ушёл, b ждёт через час у двоих
    v2 = mk_funnel("f", [{"key": "a"}, {"key": "c"}, {"key": "b2", "text": "новый b"}],
                   [("a", "done", "b2", 3600), ("b2", "done", "c", 3600)])
    # без карты – отказ с понятным текстом
    with pytest.raises(PublishError) as ei:
        await env.publish("t1", v2, mode="migrate")
    text = ei.value.report.text()
    assert "шаг «b» удалён" in text and "у 2 чел. запланирован" in text and "--map b=" in text
    async with env.pool.acquire() as c:
        assert await c.fetchval("SELECT max(version) FROM funnel_versions") == 1   # ничего не записано
    # dry-run показывает отчёт без записи
    rep = await publish(env.pool, "t1", v2, mode="migrate", redirects={"b": "b2"}, dry_run=True,
                        clock=env.clock)
    assert rep.dry_run and any(ln.key == "b" and ln.redirect == "b2" for ln in rep.lines)
    # с картой – задачи переведены на b2 новой версии
    rep = await env.publish("t1", v2, mode="migrate", redirects={"b": "b2"}, comment="b→b2")
    assert rep.to_version == 2 and rep.migrated == 2
    await env.run_for(3 * 3600)
    assert await sent(env, 1) == [("a", 1), ("b2", 2), ("c", 2)]
    async with env.pool.acquire() as c:
        pos = await c.fetchrow("SELECT version, status FROM positions WHERE uid=1")
    assert pos["version"] == 2 and pos["status"] == "done"


async def test_tombstone_keeps_people_without_map(env):
    """Шаг не удалили, а сделали надгробием (jump на другой шаг, id тот же) – карта не нужна."""
    await env.tenant("t1")
    await env.start()
    await env.publish("t1", v1())
    await env.start_user("t1", 1)
    await env.run_for(60)
    v2 = mk_funnel("f", [{"key": "a"}, {"key": "b", "type": "jump"}, {"key": "c"}, {"key": "d"}],
                   [("a", "done", "d", 3600), ("b", "jump", "d", None), ("d", "done", "c", 10)])
    rep = await env.publish("t1", v2, mode="migrate")
    assert rep.migrated == 1
    await env.run_for(2 * 3600)
    assert [k for k, _v in await sent(env, 1)] == ["a", "d", "c"]


async def test_new_only_old_people_finish_old_version(env):
    await env.tenant("t1")
    await env.start()
    await env.publish("t1", v1())
    await env.start_user("t1", 1)
    await env.run_for(60)
    v2 = mk_funnel("f", [{"key": "a", "text": "новое a"}, {"key": "b", "text": "новое b"},
                         {"key": "c", "text": "новое c"}],
                   [("a", "done", "b", 60), ("b", "done", "c", 60)])
    rep = await env.publish("t1", v2)          # режим по умолчанию – только новым
    assert rep.to_version == 2 and rep.migrated == 0
    await env.start_user("t1", 2)
    await env.run_for(3 * 3600)
    assert await sent(env, 1) == [("a", 1), ("b", 1), ("c", 1)]
    assert await sent(env, 2) == [("a", 2), ("b", 2), ("c", 2)]
    texts1 = [c["text"] for c in env.tg.sends(1)]
    assert texts1 == ["текст a", "текст b", "текст c"]
    assert [c["text"] for c in env.tg.sends(2)] == ["новое a", "новое b", "новое c"]
    # старая версия выгружается, когда на ней никого не осталось
    await env.rt.registry.prune()
    assert (env.rt.registry.by_slug("t1").id, "f", 1) not in env.rt.registry.loaded_versions()


async def test_migrate_recomputes_time_and_skip_if_past(env):
    f1 = mk_funnel("f", [{"key": "a"}, {"key": "b", "schedule": {"absolute_time_msk": "18:00"}},
                         {"key": "c", "schedule": {"absolute_time_msk": "20:00"}}, {"key": "d"}],
                   [("a", "done", "b", 0), ("b", "done", "c", 0), ("c", "done", "d", 60)])
    await env.tenant("t1")
    await env.start()
    await env.publish("t1", f1)
    t0 = env.clock.now()              # 12:00
    await env.start_user("t1", 1)
    await env.run_for(60)
    job = (await env.pending(step_key="b"))[0]
    assert job["run_at"] == t0 + timedelta(hours=6)
    # b переносим на 19:00; ещё раз – на 11:00 (уже прошло → пропуск, сразу c)
    f2 = f1.model_copy(deep=True)
    f2.steps[1].schedule = f2.steps[1].schedule.model_copy(update={"absolute_time_msk": "19:00"})
    rep = await env.publish("t1", f2, mode="migrate")
    assert rep.retimed == 1 and any(ln.key == "b" and ln.status == "time" for ln in rep.lines)
    job = (await env.pending(step_key="b"))[0]
    assert job["run_at"] == t0 + timedelta(hours=7) and job["version"] == 2
    f3 = f1.model_copy(deep=True)
    f3.steps[1].schedule = f3.steps[1].schedule.model_copy(update={"absolute_time_msk": "11:00"})
    rep = await env.publish("t1", f3, mode="migrate")
    assert rep.skipped_past == 1
    assert not await env.pending(step_key="b")
    assert (await env.pending(step_key="c"))[0]["run_at"] == t0 + timedelta(hours=8)
    await env.run_for(86400)
    assert [k for k, _v in await sent(env, 1)] == ["a", "c", "d"]


async def test_hot_swap_via_notify(env):
    """Публикация из другого процесса – воркер получает NOTIFY и берёт новый граф без рестарта."""
    await env.tenant("t1")
    rt = await env.start()
    await rt.listen()
    await publish(env.pool, "t1", v1(), clock=env.clock)       # как CLI: без прямого вызова реестра
    for _ in range(50):
        if rt.registry.current_version(rt.registry.by_slug("t1").id, "f") == 1:
            break
        await asyncio.sleep(0.05)
    assert rt.registry.current_version(rt.registry.by_slug("t1").id, "f") == 1
    v2 = mk_funnel("f", [{"key": "a", "text": "горячее a"}], [])
    await publish(env.pool, "t1", v2, clock=env.clock)
    for _ in range(50):
        if rt.registry.current_version(rt.registry.by_slug("t1").id, "f") == 2:
            break
        await asyncio.sleep(0.05)
    await env.start_user("t1", 1)
    await env.run_for(5)
    assert [c["text"] for c in env.tg.sends(1)] == ["горячее a"]


async def test_two_tenants_isolated(env):
    await env.tenant("alpha")
    await env.tenant("beta")
    await env.start()
    fa = mk_funnel("f", [{"key": "a", "text": "альфа"}, {"key": "b", "text": "альфа b"}],
                   [("a", "done", "b", 100)])
    fb = mk_funnel("f", [{"key": "a", "text": "бета"}, {"key": "b", "text": "бета b"}],
                   [("a", "done", "b", 200)])
    await env.publish("alpha", fa)
    await env.publish("beta", fb)
    # один и тот же человек в двух ботах
    await env.start_user("alpha", 42)
    await env.start_user("beta", 42)
    await env.run_for(10)
    # блок в альфе не трогает бету
    await env.rt.gateway.on_block(env.bot("alpha"), 42, True)
    await env.run_for(600)
    ba, bb = env.bot("alpha").tg_bot_id, env.bot("beta").tg_bot_id
    by_bot = {}
    for c in env.tg.sends(42):
        by_bot.setdefault(c["bot"], []).append(c["text"])
    assert by_bot == {ba: ["альфа"], bb: ["бета", "бета b"]}
    async with env.pool.acquire() as c:
        rows = await c.fetch("""SELECT t.slug, p.blocked FROM participants p JOIN tenants t ON t.id=p.tenant_id
                                ORDER BY t.slug""")
    assert [(r["slug"], r["blocked"]) for r in rows] == [("alpha", True), ("beta", False)]


async def test_600_jobs_semaphore_holds_pool(env):
    """Авария 22.09: пачка таймеров в одну минуту. Семафор запуска держит 8
    параллельных шагов, пул не исчерпан, все 600 сообщений ушли."""
    await env.tenant("t1")
    await env.start(pool_max=12, max_held=100)
    await env.publish("t1", mk_funnel("f", [{"key": "a"}, {"key": "b"}], [("a", "done", "b", 60)]))
    for uid in range(1, 601):
        await env.start_user("t1", uid)
    await env.run_for(0)          # 600 × «a»
    env.tg.max_concurrent = 0
    env.tg.delay = 0.005          # отправка «длится» – шаги налезают друг на друга
    await env.run_for(120)        # 600 × «b» – в одну секунду
    async with env.pool.acquire() as c:
        ok = await c.fetchval("SELECT count(*) FROM sent_messages WHERE status='ok' AND step_key='b'")
        bad = await c.fetchval("SELECT count(*) FROM jobs WHERE status IN ('failed','pending','running')")
        errs = await c.fetchval("SELECT count(*) FROM actions WHERE action IN ('step_error','send_error')")
    assert ok == 600 and bad == 0 and errs == 0
    assert env.worker.stats["max_parallel"] == 8     # упёрлись ровно в семафор запуска
    assert env.tg.max_concurrent <= 8
    assert env.worker.stats["retried"] == 0
