"""Процесс движка: пул, реестр запусков, боты, воркер, приём событий.

Один процесс обслуживает много запусков: все боты поллятся одним aiogram
Dispatcher (`start_polling(*bots)`), запуск определяется по id бота.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

import asyncpg
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession

from .clock import Clock
from .config import RuntimeConfig, mask, resolve_secret
from .db import CH_JOBS, CH_VERSIONS, create_pool, migrate
from .executor import Executor
from .gateway import Gateway
from .media import MediaStore
from .platforms.max_hub import ApiFactory, MaxHub
from .platforms.max_sender import MaxSender
from .registry import Registry
from .sender import TgSender

log = logging.getLogger("runtime")


@dataclass
class BotEntry:
    row_id: int
    tenant_id: int
    tg_bot_id: int
    bot: Bot
    username: str | None = None


SessionFactory = Callable[[], BaseSession | None]


class Runtime:
    def __init__(self, cfg: RuntimeConfig | None = None, *, clock: Clock | None = None,
                 session_factory: SessionFactory | None = None, pool: asyncpg.Pool | None = None,
                 max_api_factory: ApiFactory | None = None):
        self.cfg = cfg or RuntimeConfig()
        self.clock = clock or Clock()
        self._session_factory = session_factory
        self.pool: asyncpg.Pool | None = pool
        self._own_pool = pool is None
        self.registry: Registry | None = None
        self.media = MediaStore(self.cfg.data_dir, self._repo_root())
        self.sender: TgSender | None = None
        # MAX: боты (в т.ч. общий на несколько запусков) и отправка – runtime/platforms
        self.max_hub = MaxHub(self, max_api_factory)
        self.max_sender: MaxSender | None = None
        self.executor = Executor(self)
        self.gateway = Gateway(self)
        self.bots: dict[int, BotEntry] = {}          # tg_bot_id → бот
        self._tenant_bot: dict[int, BotEntry] = {}   # tenant_id → основной TG-бот
        self._wake = asyncio.Event()
        self._listener: asyncpg.Connection | None = None
        self._tenant_sems: dict[int, asyncio.Semaphore] = {}
        self.global_sem: asyncio.Semaphore | None = None

    def _repo_root(self):
        try:
            return self.cfg.bot_repo_path()
        except Exception:  # noqa: BLE001
            return None

    # ── жизненный цикл ───────────────────────────────────────────────────
    async def open(self) -> None:
        if self.pool is None:
            self.pool = await create_pool(self.cfg.dsn, max_size=self.cfg.pool_max)
        await migrate(self.pool)
        self.registry = Registry(self.pool)
        self.sender = TgSender(self.pool, self.media)
        self.max_sender = MaxSender(self.pool, self.media)
        # задач в работе не больше, чем соединений в пуле (минус запас на приём
        # событий от людей) – пул не выбирается до дна пачкой таймеров (22.09)
        self.global_sem = asyncio.Semaphore(max(1, self.cfg.pool_max - 2))
        await self.reload()

    async def reload(self) -> None:
        await self.registry.load_tenants()
        await self.registry.load_current()
        await self._load_bots()
        await self.max_hub.load()

    async def _load_bots(self) -> None:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM bots WHERE enabled AND platform='tg' ORDER BY id")
        seen = set()
        for r in rows:
            if r["tenant_id"] not in self.registry.tenants:
                continue
            existing = next((b for b in self.bots.values() if b.row_id == r["id"]), None)
            if existing is not None:
                seen.add(existing.tg_bot_id)
                self._tenant_bot.setdefault(r["tenant_id"], existing)
                continue
            try:
                token = resolve_secret(r["secret_ref"])
            except Exception as e:  # noqa: BLE001
                log.error("бот #%s запуска %s: %s – пропускаем", r["id"], r["tenant_id"], e)
                continue
            session = self._session_factory() if self._session_factory else self._real_session()
            bot = Bot(token=token, session=session, default=DefaultBotProperties(parse_mode="HTML"))
            entry = BotEntry(row_id=r["id"], tenant_id=r["tenant_id"], tg_bot_id=bot.id,
                             bot=bot, username=r["username"])
            self.bots[bot.id] = entry
            self._tenant_bot.setdefault(r["tenant_id"], entry)
            seen.add(bot.id)
            if r["tg_bot_id"] != bot.id:
                async with self.pool.acquire() as conn:
                    await conn.execute("UPDATE bots SET tg_bot_id=$2 WHERE id=$1", r["id"], bot.id)
            log.info("бот %s (токен %s) → запуск %s", bot.id, mask(token),
                     self.registry.tenant(r["tenant_id"]).slug)

    def _real_session(self) -> BaseSession | None:
        if self.cfg.tg_proxy:
            from aiogram.client.session.aiohttp import AiohttpSession
            return AiohttpSession(proxy=self.cfg.tg_proxy)
        return None

    def bot_for_tenant(self, tenant_id: int) -> BotEntry | None:
        return self._tenant_bot.get(tenant_id)

    def tenant_sem(self, tenant_id: int) -> asyncio.Semaphore:
        sem = self._tenant_sems.get(tenant_id)
        if sem is None:
            n = self.registry.tenant(tenant_id).settings.max_parallel_fires if \
                tenant_id in self.registry.tenants else 8
            sem = self._tenant_sems[tenant_id] = asyncio.Semaphore(max(1, n))
        return sem

    def wake(self) -> None:
        self._wake.set()

    # ── LISTEN/NOTIFY ────────────────────────────────────────────────────
    async def listen(self) -> None:
        """Отдельное соединение: «новая задача» будит воркер, «новая версия» –
        перечитывает граф без рестарта."""
        self._listener = await asyncpg.connect(self.cfg.dsn)

        def on_jobs(_c, _pid, _ch, payload):
            self._wake.set()

        def on_versions(_c, _pid, _ch, payload):
            try:
                t, rest = payload.split(":", 1)
                f, v = rest.rsplit(":", 1)
                asyncio.get_running_loop().create_task(
                    self.registry.on_published(int(t), f, int(v)))
            except Exception:  # noqa: BLE001
                log.exception("плохое уведомление о версии: %r", payload)
            self._wake.set()

        def on_tenants(_c, _pid, _ch, payload):
            async def _reload():
                await self.registry.load_tenants()
                self._tenant_sems.clear()
                log.info("настройки запусков перечитаны без рестарта")
            asyncio.get_running_loop().create_task(_reload())

        await self._listener.add_listener("fs_tenants", on_tenants)
        await self._listener.add_listener(CH_JOBS, on_jobs)
        await self._listener.add_listener(CH_VERSIONS, on_versions)

    async def close(self) -> None:
        for e in list(self.bots.values()):
            try:
                await e.bot.session.close()
            except Exception:  # noqa: BLE001
                pass
        await self.max_hub.close()
        if self._listener is not None:
            await self._listener.close()
        if self.pool is not None and self._own_pool:
            await self.pool.close()
