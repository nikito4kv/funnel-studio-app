"""Postgres движка: пул соединений и миграции.

Миграции – SQL-файлы `runtime/migrations/NNN_имя.sql`, применяются на старте по
порядку номера; применённые записываются в `schema_version`. Одновременный
старт двух процессов не применит миграцию дважды – держим advisory-lock.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import asyncpg

log = logging.getLogger("runtime.db")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{3})_([A-Za-z0-9_]+)\.sql$")
_LOCK_KEY = 0x5F5E_0003  # произвольная константа advisory-lock «миграции движка»

CH_JOBS = "fs_jobs"          # NOTIFY: появилась задача (payload – run_at, epoch)
CH_VERSIONS = "fs_versions"  # NOTIFY: опубликована версия (payload – tenant_id:funnel_id:version)


async def _init_conn(conn: asyncpg.Connection) -> None:
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=lambda v: json.dumps(v, ensure_ascii=False),
                                  decoder=json.loads, schema="pg_catalog")


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    # command_timeout – страховка от вечного ожидания; ожидание свободного
    # соединения задаётся при acquire (см. семафоры воркера: задач в работе
    # никогда не больше, чем соединений в пуле).
    return await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size,
                                     init=_init_conn, command_timeout=60)


def migration_files() -> list[tuple[int, str, Path]]:
    out = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _MIGRATION_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), m.group(2), p))
    return out


async def migrate(pool_or_conn) -> list[int]:
    """Применяет недостающие миграции. → номера применённых сейчас."""
    applied_now: list[int] = []

    async def _run(conn: asyncpg.Connection):
        await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_KEY)
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version    int PRIMARY KEY,
                    name       text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )""")
            done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_version")}
            for num, name, path in migration_files():
                if num in done:
                    continue
                sql = path.read_text(encoding="utf-8")
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute("INSERT INTO schema_version (version, name) VALUES ($1, $2)",
                                       num, name)
                log.info("миграция %03d_%s применена", num, name)
                applied_now.append(num)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_KEY)

    if isinstance(pool_or_conn, asyncpg.Pool):
        async with pool_or_conn.acquire() as conn:
            await _run(conn)
    else:
        await _run(pool_or_conn)
    return applied_now
