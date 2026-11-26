"""`python -m runtime run`: все запуски, все боты, один процесс.

  * aiogram Dispatcher поллит всех TG-ботов (`start_polling(*bots)`), запуск
    определяется по id бота; FSM не используется – состояние человека живёт
    в positions;
  * воркер очереди; LISTEN/NOTIFY – новая задача / новая версия / настройки;
  * health и статус на http://127.0.0.1:8781 (`/health`, `/status`).
"""
from __future__ import annotations

import asyncio
import logging
import signal

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from aiohttp import web

from .config import RuntimeConfig
from .gateway import TgUser
from .gates.http import add_routes as add_gate_routes
from . import steps
from .service import Runtime
from .status import collect
from .worker import Worker

log = logging.getLogger("runtime")


def build_router(rt: Runtime) -> Router:
    r = Router(name="studio_runtime")

    def entry_of(bot: Bot):
        return rt.bots.get(bot.id)

    @r.message(CommandStart())
    async def on_start(message: Message, bot: Bot, command: CommandObject) -> None:
        entry = entry_of(bot)
        if entry is None or message.from_user is None:
            return
        u = message.from_user
        await rt.gateway.on_start(entry, TgUser(u.id, u.first_name, u.last_name, u.username),
                                  (command.args or "").strip(),
                                  f"{bot.id}:{message.chat.id}:{message.message_id}")

    @r.message(F.contact)
    async def on_contact(message: Message, bot: Bot) -> None:
        entry = entry_of(bot)
        if entry is None or message.from_user is None:
            return
        c = message.contact
        if c.user_id and c.user_id != message.from_user.id:
            await message.answer("Это чужой контакт 🙂 Нажмите кнопку ниже, чтобы отправить свой.")
            return
        await rt.gateway.on_contact(entry, message.from_user.id, c.phone_number,
                                    f"{bot.id}:{message.chat.id}:{message.message_id}")

    @r.callback_query()
    async def on_callback(cb: CallbackQuery, bot: Bot) -> None:
        entry = entry_of(bot)
        ack = ""
        if entry is not None and cb.from_user and cb.data:
            try:
                ack = await rt.gateway.on_callback(entry, cb.from_user.id, cb.data, f"{bot.id}:cb{cb.id}")
            except Exception:  # noqa: BLE001
                log.exception("кнопка %r от %s не обработана", cb.data, cb.from_user.id)
        try:
            await cb.answer(ack or None)
        except Exception:  # noqa: BLE001
            pass

    @r.message(F.text)
    async def on_text(message: Message, bot: Bot) -> None:
        entry = entry_of(bot)
        if entry is None or message.from_user is None:
            return
        reply = await rt.gateway.on_text(entry, message.from_user.id, message.text or "",
                                         f"{bot.id}:{message.chat.id}:{message.message_id}")
        if reply:
            await message.answer(reply)

    @r.my_chat_member()
    async def on_member(ev: ChatMemberUpdated, bot: Bot) -> None:
        entry = entry_of(bot)
        if entry is None or ev.chat.type != "private":
            return
        st = ev.new_chat_member.status
        if st == ChatMemberStatus.KICKED:
            await rt.gateway.on_block(entry, ev.from_user.id, True)
        elif st == ChatMemberStatus.MEMBER:
            await rt.gateway.on_block(entry, ev.from_user.id, False)

    return r


async def _health_app(rt: Runtime, worker: Worker, started: dict, stop: asyncio.Event) -> web.AppRunner:
    async def health(_req):
        async with rt.pool.acquire() as conn:
            q = {r["status"]: r["n"] for r in await conn.fetch(
                "SELECT status, count(*) n FROM jobs WHERE status IN ('pending','running') GROUP BY 1")}
        return web.json_response({
            "ok": True,
            "bots": [{"id": e.tg_bot_id, "username": e.username, "tenant": rt.registry.tenant(e.tenant_id).slug}
                     for e in rt.bots.values()],
            "max_bots": [{"row": b.row_id, "id": b.platform_bot_id, "username": b.username,
                          "marker": b.marker,
                          "tenants": [{"slug": rt.registry.tenant(lk.tenant_id).slug, "prefix": lk.prefix,
                                       "default": lk.is_default} for lk in b.links]}
                         for b in rt.max_hub.bots.values()],
            "queue": q, "held": len(worker.held), "worker": worker.stats,
            "versions": [f"{t}:{f}:v{v}" for t, f, v in rt.registry.loaded_versions()],
            "started": started.get("at"),
        })

    async def status(req):
        return web.json_response(await collect(rt.pool, req.query.get("tenant"), clock=rt.clock),
                                 dumps=lambda o: __import__("json").dumps(o, ensure_ascii=False, default=str))

    async def shutdown(req):
        # только с этой машины: сервер и так слушает 127.0.0.1
        if req.remote not in ("127.0.0.1", "::1"):
            return web.json_response({"ok": False}, status=403)
        log.info("остановка по запросу /shutdown")
        stop.set()
        return web.json_response({"ok": True, "stopping": True})

    app = web.Application()
    app.router.add_post("/shutdown", shutdown)
    app.router.add_get("/health", health)
    app.router.add_get("/status", status)
    add_gate_routes(app, rt)   # вебхук кассы, /go, /govip (runtime/gates/http.py)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, rt.cfg.http_host, rt.cfg.http_port).start()
    return runner


async def run_forever(cfg: RuntimeConfig) -> int:
    from .clock import msk
    rt = Runtime(cfg)
    await rt.open()
    await rt.listen()
    started = {"at": msk(rt.clock.now())}
    if not rt.bots and not rt.max_hub.bots:
        log.warning("нет ни одного бота – заведите: python -m runtime tenant add <slug> --bot-env ИМЯ")
    # getMe + снять вебхук, если он стоит (иначе polling не получит апдейтов)
    for e in list(rt.bots.values()):
        me = await e.bot.get_me()
        e.username = me.username
        async with rt.pool.acquire() as conn:
            await conn.execute("UPDATE bots SET username=$2, tg_bot_id=$3 WHERE id=$1",
                               e.row_id, me.username, me.id)
        info = await e.bot.get_webhook_info()
        if info.url:
            log.warning("у @%s стоял вебхук – снимаем, чтобы поллить", me.username)
            await e.bot.delete_webhook(drop_pending_updates=False)
        log.info("getMe ок: @%s (id %s) → запуск %s; в очереди Telegram %s апдейтов",
                 me.username, me.id, rt.registry.tenant(e.tenant_id).slug, info.pending_update_count)
    await rt.max_hub.identify()
    for (t, f), v in sorted(rt.registry._current.items()):
        log.info("воронка %s/%s: опубликована v%s", rt.registry.tenant(t).slug, f, v)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))

    worker = Worker(rt)
    http = await _health_app(rt, worker, started, stop)
    log.info("health: http://%s:%s/health", cfg.http_host, cfg.http_port)
    dp = Dispatcher()
    dp.include_router(build_router(rt))
    tasks = [asyncio.create_task(worker.run_forever(stop), name="worker")]
    if rt.bots:
        tasks.append(asyncio.create_task(dp.start_polling(
            *[e.bot for e in rt.bots.values()], handle_signals=False, close_bot_session=False,
            allowed_updates=["message", "callback_query", "my_chat_member"]), name="polling"))

    async def pruner():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=600)
            except asyncio.TimeoutError:
                n = await rt.registry.prune()
                if n:
                    log.info("выгружено старых версий без людей: %s", n)

    tasks.append(asyncio.create_task(pruner(), name="pruner"))
    tasks += rt.max_hub.start_polling(stop)   # MAX: long-poll каждого бота (прокси не нужен)
    # смена состояний закрепа эфира по часам движка (runtime/steps/air_pin.py)
    tasks.append(asyncio.create_task(steps.periodic(rt, stop), name="air_pin"))
    await stop.wait()
    log.info("остановка…")
    if rt.bots:
        try:
            await dp.stop_polling()
        except Exception:  # noqa: BLE001
            pass
    await asyncio.gather(*tasks, return_exceptions=True)
    await http.cleanup()
    await rt.close()
    log.info("движок остановлен")
    return 0
