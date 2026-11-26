"""Запуски и опубликованные версии воронок в памяти процесса.

Горячая подмена: публикация пишет новую строку в funnel_versions и шлёт
NOTIFY fs_versions – воркер перечитывает граф без рестарта. Старые версии
держатся в памяти, пока на них есть задачи или позиции (режим «только новым»:
уже идущие доезжают по своей версии).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import asyncpg

from studio.model.funnel import Funnel

from .graph import CompiledFunnel
from .settings import TenantSettings

log = logging.getLogger("runtime.registry")


@dataclass
class Tenant:
    id: int
    slug: str
    title: str
    settings: TenantSettings
    status: str = "active"


class Registry:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self.tenants: dict[int, Tenant] = {}
        self._graphs: dict[tuple[int, str, int], CompiledFunnel] = {}
        self._current: dict[tuple[int, str], int] = {}
        self._lock = asyncio.Lock()

    # ── запуски ──
    async def load_tenants(self) -> None:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM tenants ORDER BY id")
        self.tenants = {r["id"]: Tenant(id=r["id"], slug=r["slug"], title=r["title"],
                                        settings=TenantSettings.model_validate(r["settings"] or {}),
                                        status=r["status"]) for r in rows}

    def tenant(self, tenant_id: int) -> Tenant:
        return self.tenants[tenant_id]

    def by_slug(self, slug: str) -> Tenant | None:
        return next((t for t in self.tenants.values() if t.slug == slug), None)

    # ── версии ──
    async def load_current(self) -> None:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT DISTINCT ON (tenant_id, funnel_id) tenant_id, funnel_id, version, graph
                   FROM funnel_versions ORDER BY tenant_id, funnel_id, version DESC""")
        for r in rows:
            cf = CompiledFunnel.build(r["tenant_id"], r["version"], Funnel.model_validate(r["graph"]))
            self._graphs[(r["tenant_id"], r["funnel_id"], r["version"])] = cf
            self._current[(r["tenant_id"], r["funnel_id"])] = r["version"]

    async def get(self, tenant_id: int, funnel_id: str, version: int) -> CompiledFunnel | None:
        key = (tenant_id, funnel_id, version)
        cf = self._graphs.get(key)
        if cf is not None:
            return cf
        async with self._lock:
            cf = self._graphs.get(key)
            if cf is not None:
                return cf
            async with self.pool.acquire() as conn:
                raw = await conn.fetchval(
                    "SELECT graph FROM funnel_versions WHERE tenant_id=$1 AND funnel_id=$2 AND version=$3",
                    tenant_id, funnel_id, version)
            if raw is None:
                return None
            cf = CompiledFunnel.build(tenant_id, version, Funnel.model_validate(raw))
            self._graphs[key] = cf
            return cf

    def current_version(self, tenant_id: int, funnel_id: str) -> int | None:
        return self._current.get((tenant_id, funnel_id))

    async def current(self, tenant_id: int, funnel_id: str) -> CompiledFunnel | None:
        v = self.current_version(tenant_id, funnel_id)
        if v is None:
            return None
        return await self.get(tenant_id, funnel_id, v)

    def funnel_ids(self, tenant_id: int) -> list[str]:
        """Воронки, опубликованные в запуске."""
        return sorted(f for (t, f) in self._current if t == tenant_id)

    def entry_funnel_id(self, tenant: Tenant) -> str | None:
        """Воронка, в которую ведёт /start."""
        if tenant.settings.entry_funnel:
            return tenant.settings.entry_funnel
        funnels = sorted(f for (t, f) in self._current if t == tenant.id)
        return funnels[0] if funnels else None

    async def on_published(self, tenant_id: int, funnel_id: str, version: int) -> None:
        """Горячая подмена графа (NOTIFY fs_versions)."""
        self._graphs.pop((tenant_id, funnel_id, version), None)
        cf = await self.get(tenant_id, funnel_id, version)
        if cf is None:
            log.warning("опубликованная версия %s/%s v%s не найдена", tenant_id, funnel_id, version)
            return
        prev = self._current.get((tenant_id, funnel_id))
        if prev is None or version >= prev:
            self._current[(tenant_id, funnel_id)] = version
        log.info("граф обновлён без рестарта: запуск %s, воронка %s, v%s (была v%s)",
                 tenant_id, funnel_id, version, prev)

    async def prune(self) -> int:
        """Выгружает из памяти старые версии, на которых никого не осталось."""
        async with self.pool.acquire() as conn:
            live = {(r["tenant_id"], r["funnel_id"], r["version"]) for r in await conn.fetch(
                """SELECT DISTINCT tenant_id, funnel_id, version FROM jobs
                   WHERE status IN ('pending','running')
                   UNION SELECT DISTINCT tenant_id, funnel_id, version FROM positions
                   WHERE status='active'""")}
        drop = [k for k in self._graphs
                if k not in live and self._current.get((k[0], k[1])) != k[2]]
        for k in drop:
            self._graphs.pop(k, None)
        return len(drop)

    def loaded_versions(self) -> list[tuple[int, str, int]]:
        return sorted(self._graphs)
