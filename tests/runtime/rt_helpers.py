"""Помощники тестов движка: настоящий Postgres из Docker, фальшивый Telegram,
управляемые часы.

Postgres: STUDIO_TEST_DSN (по умолчанию контейнер fs-runtime-pg на 55432,
база fs_runtime_test – создаётся сама). Недоступен – тесты с БД пропускаются
(skip), но прогонять их нужно по-настоящему – см. README, «Этап 3».
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import (
    GetChatMember, GetMe, PinChatMessage, SendAnimation, SendMediaGroup, SendMessage, SendPhoto,
    SendVideo, SendVideoNote,
)
from aiogram.types import (
    Animation, Chat, ChatMemberLeft, ChatMemberMember, InputFile, Message, PhotoSize, User, Video,
    VideoNote,
)

from runtime.clock import MSK, FakeClock
from runtime.config import RuntimeConfig
from runtime.db import migrate
from runtime.service import Runtime
from runtime.worker import Worker
from studio.model.funnel import (
    Branch, BranchRouter, BranchWindow, Button, Edge, EdgeKind, Funnel, Step,
)

TEST_DSN = os.environ.get("STUDIO_TEST_DSN",
                          "postgresql://fs:fsruntime@127.0.0.1:55432/fs_runtime_test")
_NS = uuid.UUID("7d1f2c10-0000-4000-8000-00000000fa11")

_db_ready: bool | None = None


async def _ensure_db() -> bool:
    global _db_ready
    if _db_ready is not None:
        return _db_ready
    base, _, dbname = TEST_DSN.rpartition("/")
    try:
        admin = await asyncpg.connect(base + "/postgres", timeout=3)
    except Exception:  # noqa: BLE001
        _db_ready = False
        return False
    try:
        if not await admin.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", dbname):
            await admin.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await migrate(conn)
    finally:
        await conn.close()
    _db_ready = True
    return True


TABLES = ("jobs", "sent_messages", "actions", "media_cache", "positions", "participants",
          "funnel_versions", "bots", "tenants")


class SimulatedCrash(BaseException):
    """Процесс «упал» посреди шага (BaseException – мимо всех except Exception)."""


# ── фальшивый Telegram ───────────────────────────────────────────────────────

class FakeTelegram:
    def __init__(self, clock: FakeClock | None = None):
        self.clock = clock
        self.calls: list[dict] = []
        self._mid = 1000
        self.fail_rules: list = []        # fn(call: dict) -> Exception | None
        self.crash_rules: list = []       # fn(call: dict) -> bool: упасть ПОСЛЕ отправки
        self.members: dict[tuple[int, int], str] = {}
        self.delay = 0.0
        self.concurrent = 0
        self.max_concurrent = 0

    def session(self) -> "FakeSession":
        return FakeSession(self)

    @staticmethod
    def _media(v):
        if v is None:
            return None
        if isinstance(v, InputFile):
            return "file:" + Path(getattr(v, "path", "") or getattr(v, "filename", "") or "?").name
        return str(v)

    def sends(self, chat_id: int | None = None) -> list[dict]:
        return [c for c in self.calls if c["method"].startswith("Send")
                and (chat_id is None or c["chat_id"] == chat_id)]

    async def handle(self, bot, method):
        name = type(method).__name__
        call = {"method": name, "bot": bot.id, "chat_id": getattr(method, "chat_id", None),
                "text": getattr(method, "text", None) or getattr(method, "caption", None),
                "reply_markup": getattr(method, "reply_markup", None),
                "at": self.clock.now() if self.clock else None}
        for f in ("photo", "video", "video_note", "animation"):
            if hasattr(method, f):
                call["media"] = self._media(getattr(method, f))
                call["kind"] = f
        for fld in ("message_id", "rich_message", "action"):
            if getattr(method, fld, None) is not None:
                call[fld] = getattr(method, fld)
        for fld in ("width", "height", "duration", "length"):
            if getattr(method, fld, None) is not None:
                call[fld] = getattr(method, fld)
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            for rule in self.fail_rules:
                err = rule(call)
                if err is not None:
                    call["error"] = str(err)
                    self.calls.append(call)
                    if isinstance(err, type):
                        err = err(method=method, message="fake")
                    raise err
            self.calls.append(call)
            res = self._result(bot, method, name, call)
            if isinstance(res, Message):
                call["_mid"] = res.message_id
            for rule in self.crash_rules:
                if rule(call):
                    raise SimulatedCrash(f"упали после {name}")
            return res
        finally:
            self.concurrent -= 1

    def _msg(self, chat_id, **kw) -> Message:
        self._mid += 1
        return Message(message_id=self._mid, date=datetime.now(MSK),
                       chat=Chat(id=chat_id, type="private"), **kw)

    def _result(self, bot, method, name, call):
        cid = call["chat_id"]
        n = self._mid + 1
        if isinstance(method, SendMessage):
            return self._msg(cid, text=method.text)
        if isinstance(method, SendPhoto):
            return self._msg(cid, photo=[PhotoSize(file_id=f"ph{n}", file_unique_id=f"u{n}",
                                                   width=10, height=10)], caption=method.caption)
        if isinstance(method, SendVideo):
            return self._msg(cid, video=Video(file_id=f"vd{n}", file_unique_id=f"u{n}", width=10,
                                              height=10, duration=1))
        if isinstance(method, SendVideoNote):
            return self._msg(cid, video_note=VideoNote(file_id=f"vn{n}", file_unique_id=f"u{n}",
                                                       length=10, duration=1))
        if isinstance(method, SendAnimation):
            return self._msg(cid, animation=Animation(file_id=f"an{n}", file_unique_id=f"u{n}",
                                                      width=10, height=10, duration=1))
        if isinstance(method, SendMediaGroup):
            return [self._msg(cid, photo=[PhotoSize(file_id=f"ph{n}{i}", file_unique_id=f"u{n}{i}",
                                                    width=1, height=1)]) for i in range(len(method.media))]
        if isinstance(method, GetMe):
            return User(id=bot.id, is_bot=True, first_name="Test", username=f"test{bot.id}_bot")
        if isinstance(method, GetChatMember):
            st = self.members.get((method.chat_id, method.user_id))
            if st is None:
                raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
            u = User(id=method.user_id, is_bot=False, first_name="x")
            return ChatMemberMember(user=u) if st == "member" else ChatMemberLeft(user=u)
        if isinstance(method, PinChatMessage):
            return True
        if name.startswith("Send") and name != "SendChatAction":
            # прочие отправки (sendRichMessage и т.п.) – обычное сообщение
            return self._msg(cid, text=call.get("text") or "")
        return True


class FakeSession(BaseSession):
    def __init__(self, tg: FakeTelegram):
        super().__init__()
        self.tg = tg

    async def make_request(self, bot, method, timeout=None):
        return await self.tg.handle(bot, method)

    async def stream_content(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:
        return None


def forbidden_for(chat_id: int):
    return lambda c: (TelegramForbiddenError if c["chat_id"] == chat_id and
                      c["method"].startswith("Send") else None)


# ── графы для тестов ─────────────────────────────────────────────────────────

def sid(fid: str, key: str) -> uuid.UUID:
    return uuid.uuid5(_NS, f"{fid}:{key}")


def mk_funnel(fid: str, steps: list[dict], edges: list[tuple], **kw) -> Funnel:
    """steps: [{"key", "type"?, "text"?, "schedule"?, "buttons"?, …}];
    edges: [(from_key, kind, to_key, delay)]."""
    out_steps = []
    for s in steps:
        s = dict(s)
        key = s.pop("key")
        text = s.pop("text", f"текст {key}")
        buttons = s.pop("buttons", [])
        claim = s.pop("claim", None)
        content = {"tg": {"text": text, "buttons": [Button(**b) for b in buttons],
                          "claim_button_text": claim}}
        out_steps.append(Step(id=sid(fid, key), key=key, content=content, **s))
    out_edges = []
    for i, (a, kind, b, delay) in enumerate(edges):
        out_edges.append(Edge(id=uuid.uuid5(_NS, f"{fid}:e{i}:{a}:{b}"), from_step=sid(fid, a),
                              kind=EdgeKind(kind), to_step=sid(fid, b), delay_seconds=delay))
    return Funnel(id=fid, steps=out_steps, edges=out_edges, **kw)


def router_fixed(fid: str, entry: str, date: str, time: str = "11:00") -> BranchRouter:
    return BranchRouter(branches=[Branch(id="M", entry_step=sid(fid, entry), webinar_time=time,
                                         windows=[BranchWindow(**{"from": "00:00", "to": "23:59",
                                                                  "day": "fixed", "date": date})])])


# ── окружение теста ──────────────────────────────────────────────────────────

class Env:
    def __init__(self, pool, clock: FakeClock, tg: FakeTelegram, tmp: Path, monkeypatch):
        self.pool, self.clock, self.tg, self.tmp, self.mp = pool, clock, tg, tmp, monkeypatch
        self.rt: Runtime | None = None
        self.worker: Worker | None = None
        self._n = 0

    async def tenant(self, slug: str, *, settings: dict | None = None, bot_id: int | None = None) -> int:
        self._n += 1
        bot_id = bot_id or 900000 + self._n
        env = f"FS_TEST_TOKEN_{slug.upper().replace('-', '_')}"
        self.mp.setenv(env, f"{bot_id}:TEST-{slug}")
        async with self.pool.acquire() as c:
            tid = await c.fetchval("INSERT INTO tenants (slug, title, settings) VALUES ($1,$1,$2) RETURNING id",
                                   slug, settings or {})
            await c.execute("INSERT INTO bots (tenant_id, platform, secret_ref) VALUES ($1,'tg',$2)",
                            tid, f"env:{env}")
        return tid

    async def start(self, *, pool_max: int = 10, max_held: int = 64) -> Runtime:
        cfg = RuntimeConfig(dsn=TEST_DSN, data_dir=self.tmp / "data", pool_max=pool_max, max_held=max_held)
        self.rt = Runtime(cfg, clock=self.clock, session_factory=self.tg.session, pool=self.pool)
        await self.rt.open()
        self.worker = Worker(self.rt, name="w1")
        return self.rt

    async def publish(self, slug: str, funnel: Funnel, **kw):
        from runtime.publish import publish
        rep = await publish(self.pool, slug, funnel, clock=self.clock, **kw)
        if self.rt is not None:
            await self.rt.registry.on_published(self.rt.registry.by_slug(slug).id, funnel.id,
                                                rep.to_version)
        return rep

    def bot(self, slug: str):
        t = self.rt.registry.by_slug(slug)
        return self.rt.bot_for_tenant(t.id)

    async def start_user(self, slug: str, uid: int, payload: str = "", name: str = "Аня") -> str:
        from runtime.gateway import TgUser
        self._n += 1
        return await self.rt.gateway.on_start(self.bot(slug), TgUser(uid, name), payload, f"u{self._n}")

    async def contact(self, slug: str, uid: int, phone: str = "+79991234567") -> str:
        self._n += 1
        return await self.rt.gateway.on_contact(self.bot(slug), uid, phone, f"c{self._n}")

    async def click(self, slug: str, uid: int, data: str) -> str:
        self._n += 1
        return await self.rt.gateway.on_callback(self.bot(slug), uid, data, f"k{self._n}")

    async def pending(self, **where) -> list:
        q = "SELECT * FROM jobs WHERE status='pending'"
        args = []
        for k, v in where.items():
            args.append(v)
            q += f" AND {k}=${len(args)}"
        async with self.pool.acquire() as c:
            return await c.fetch(q + " ORDER BY run_at, id", *args)

    async def run_until(self, until: datetime, *, worker: Worker | None = None, max_steps: int = 5000) -> int:
        """Перематывает часы, выполняя задачи по порядку времени. → сколько выполнено."""
        w = worker or self.worker
        done = 0
        for _ in range(max_steps):
            async with self.pool.acquire() as c:
                nxt = await c.fetchval(
                    """SELECT min(t) FROM (
                         SELECT min(run_at) t FROM jobs WHERE status='pending'
                         UNION ALL SELECT min(lease_until) FROM jobs WHERE status='running') x""")
            if nxt is None or nxt > until:
                break
            if nxt > self.clock.now():
                self.clock.set(nxt)
            n = await w.tick()
            if n == 0:
                self.clock.advance(1)
            done += n
        if self.clock.now() < until:
            self.clock.set(until)
        return done

    async def run_for(self, seconds: float, **kw) -> int:
        return await self.run_until(self.clock.now() + timedelta(seconds=seconds), **kw)


