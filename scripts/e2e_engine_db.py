"""Помощник e2e «Опубликовать → движок»: своя база движка, запуск, «люди внутри», проверки.

    uv run python scripts/e2e_engine_db.py setup                       # база + миграции + запуск e2e-pub
    uv run python scripts/e2e_engine_db.py seed <funnel_id>            # версия «из CLI» с шагом z_old и люди
    uv run python scripts/e2e_engine_db.py versions <funnel_id>        # JSON: версии воронки в движке
    uv run python scripts/e2e_engine_db.py listen <funnel_id> [сек]    # ждёт NOTIFY fs_versions, печатает payload

База – STUDIO_E2E_DSN (по умолчанию fs_runtime_e2e_publish в контейнере fs-runtime-pg; создаётся
сама). Рабочую базу fs_runtime и тестовую fs_runtime_test* не трогает. Ботов здесь нет – движок
не запускается, проверяется только то, что публикация записала и объявила версию.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.db import CH_VERSIONS, _init_conn, migrate  # noqa: E402

DSN = os.environ.get("STUDIO_E2E_DSN", "postgresql://fs:fsruntime@127.0.0.1:55432/fs_runtime_e2e_publish")
TENANT = "e2e-pub"
BOT = "e2e_studio_test_bot"


async def connect() -> asyncpg.Connection:
    base, _, name = DSN.rpartition("/")
    admin = await asyncpg.connect(base + "/postgres", timeout=5)
    try:
        if not await admin.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", name):
            await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    c = await asyncpg.connect(DSN)
    await _init_conn(c)
    return c


async def setup() -> None:
    c = await connect()
    try:
        await migrate(c)
        for slug, title in ((TENANT, "E2E: публикация из Студии"), ("e2e-other", "Второй запуск")):
            await c.execute("INSERT INTO tenants (slug, title) VALUES ($1, $2) ON CONFLICT (slug) DO NOTHING",
                            slug, title)
        # у e2e-pub – «тестовый бот» (токена нет: движок здесь не запускается, нужен только username –
        # для надписей «Запустить в боте @…» и ссылки «Проверить в боте»)
        tid = await c.fetchval("SELECT id FROM tenants WHERE slug=$1", TENANT)
        await c.execute("INSERT INTO bots (tenant_id, platform, secret_ref, username) VALUES ($1, 'tg', $2, $3) "
                        "ON CONFLICT (platform, secret_ref) DO UPDATE SET username=EXCLUDED.username",
                        tid, "env:STUDIO_TEST_E2E_NO_TOKEN", BOT)
        print(json.dumps({"dsn": DSN, "tenant": TENANT, "bot": BOT}))
    finally:
        await c.close()


async def seed(funnel_id: str) -> None:
    """Как будто кто-то опубликовал из CLI версию с шагом z_old, и у троих он запланирован,
    а ещё двое ждут контакт на первом шаге (запрос телефона)."""
    from studio.model.funnel import Funnel
    c = await connect()
    try:
        tid = await c.fetchval("SELECT id FROM tenants WHERE slug=$1", TENANT)
        cur = await c.fetchrow("SELECT version, graph FROM funnel_versions WHERE tenant_id=$1 AND funnel_id=$2 "
                               "ORDER BY version DESC LIMIT 1", tid, funnel_id)
        g = cur["graph"]
        steps = {s["key"]: s for s in g["steps"]}
        old_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"e2e:{funnel_id}:z_old"))
        old = json.loads(json.dumps(steps["s_remind1"]))
        old.update(id=old_id, key="z_old", title="старый дожим (был только в движке)")
        g["steps"].append(old)
        g["edges"].append({"id": str(uuid.uuid4()), "from_step": old_id, "kind": "done",
                           "to_step": steps["s_remind2"]["id"], "delay_seconds": 600})
        Funnel.model_validate(g)
        v = cur["version"] + 1
        now = datetime.now(timezone.utc)
        await c.execute("INSERT INTO funnel_versions (tenant_id, funnel_id, version, graph, source, mode, "
                        "published_by, comment) VALUES ($1,$2,$3,$4,'cli:e2e','new-only','cli','шаг z_old')",
                        tid, funnel_id, v, g)
        ask = steps["s_confirm"]
        for uid in range(1, 6):
            await c.execute("INSERT INTO participants (tenant_id, platform, uid, first_name, created_at, updated_at) "
                            "VALUES ($1,'tg',$2,$3,$4,$4) ON CONFLICT DO NOTHING", tid, 700000 + uid, f"Гость {uid}", now)
            waiting = uid > 3
            await c.execute(
                "INSERT INTO positions (tenant_id, platform, uid, funnel_id, version, step_id, step_key, status, "
                "awaiting, entered_at, updated_at) VALUES ($1,'tg',$2,$3,$4,$5,$6,'active',$7,$8,$8) "
                "ON CONFLICT DO NOTHING", tid, 700000 + uid, funnel_id, v,
                uuid.UUID(ask["id"] if waiting else steps["s_welcome"]["id"]), "s_confirm" if waiting else "s_welcome",
                {"kind": "contact", "step_id": ask["id"], "version": v} if waiting else None, now)
            if not waiting:
                await c.execute(
                    "INSERT INTO jobs (tenant_id, platform, uid, funnel_id, version, step_id, step_key, kind, run_at, "
                    "reason, idem_key) VALUES ($1,'tg',$2,$3,$4,$5,'z_old','fire',$6,'e2e',$7)",
                    tid, 700000 + uid, funnel_id, v, uuid.UUID(old_id), now + timedelta(hours=2),
                    f"e2e:{funnel_id}:{uid}")
        print(json.dumps({"version": v, "z_old": old_id}))
    finally:
        await c.close()


async def versions(funnel_id: str) -> None:
    c = await connect()
    try:
        rows = await c.fetch(
            "SELECT v.version, v.source, v.mode, v.published_by, v.comment, "
            "(SELECT count(*) FROM jobs j WHERE j.tenant_id=v.tenant_id AND j.funnel_id=v.funnel_id "
            " AND j.version=v.version AND j.status='pending') AS pending "
            "FROM funnel_versions v JOIN tenants t ON t.id=v.tenant_id WHERE t.slug=$1 AND v.funnel_id=$2 "
            "ORDER BY v.version", TENANT, funnel_id)
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False))
    finally:
        await c.close()


async def listen(funnel_id: str, seconds: float) -> None:
    c = await connect()
    got: asyncio.Future = asyncio.get_running_loop().create_future()

    def on(_c, _pid, _ch, payload):
        if f":{funnel_id}:" in payload and not got.done():
            got.set_result(payload)
    await c.add_listener(CH_VERSIONS, on)
    print("ready", flush=True)
    try:
        print(await asyncio.wait_for(got, seconds), flush=True)
    except asyncio.TimeoutError:
        print("timeout", flush=True)
    finally:
        await c.close()


def main() -> None:
    cmd, *args = sys.argv[1:] or ["setup"]
    if cmd == "setup":
        asyncio.run(setup())
    elif cmd == "seed":
        asyncio.run(seed(args[0]))
    elif cmd == "versions":
        asyncio.run(versions(args[0]))
    elif cmd == "listen":
        asyncio.run(listen(args[0], float(args[1]) if len(args) > 1 else 60))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
