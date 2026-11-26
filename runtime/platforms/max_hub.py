"""MAX-боты движка: long-poll и раскладка апдейтов по запускам.

Один MAX-бот может обслуживать несколько запусков («зонтичный» бот; в bot-engine
это делал внешний мост, здесь роутинг внутри движка). Какие запуски у бота и как их
различать – таблица bot_tenants (метка входа + запуск по умолчанию).

Куда идёт апдейт:
  * вход (`bot_started` или `message_created` с текстом «/start <метка>» – так
    MAX присылает ПОВТОРНЫЙ вход, bot_started приходит только первый раз):
    метка `<slug>_…` → этот запуск, метка снимается, остаток разбирается как
    обычно (`webinar_reg_vk_post` → запуск webinar, payload `reg_vk_post`);
    самая длинная подходящая метка важнее (`promo16_` раньше `promo_`);
    метки нет или она чужая – запуск по умолчанию; его нет – вход не принят;
  * нажатие кнопки: payload `f:<slug>:…` (каждая callback-кнопка несёт слаг,
    CLAUDE.md bot-engine §36) → запуск slug. Слаг чужой для этого бота или
    кнопка без слага – в воронки не попадает, только «✓» на кнопке;
  * обычное сообщение, контакт – запуск, где человек сейчас: сначала тот, где
    от него ждут ответа (телефон/почта), иначе с самой свежей позицией;
  * `bot_stopped` / `dialog_removed` – человек остановил бота: блок во ВСЕХ
    запусках этого бота (бот-то у него один). Новый вход снимает блок.

Прокси для MAX не нужен (MAX российский). Маркер long-poll хранится в
bots.poll_marker и двигается после обработки пачки – рестарт не теряет и не
задваивает апдейты (повторная доставка отсекается ключами идемпотентности).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import asyncpg

from ..config import mask, resolve_secret
from ..engine_lib.phone import _normalize_phone
from ..gateway import TgUser
from .max_api import DEFAULT_BASE_URL, UPDATE_TYPES, MaxApi

if TYPE_CHECKING:
    from ..service import Runtime

log = logging.getLogger("runtime.max")

ApiFactory = Callable[[str], MaxApi]
CLAIM_ACK = "✓"


@dataclass
class MaxLink:
    tenant_id: int
    prefix: str
    is_default: bool


@dataclass
class MaxBot:
    row_id: int
    api: MaxApi
    links: list[MaxLink] = field(default_factory=list)
    username: str | None = None
    platform_bot_id: int | None = None
    marker: int | None = None

    @property
    def bot_key(self) -> int:
        """Ключ бота в media_cache (виды max_* – с TG не пересекаются)."""
        return self.row_id

    def default_tenant(self) -> int | None:
        return next((lk.tenant_id for lk in self.links if lk.is_default), None)


@dataclass
class MaxRoute:
    """То, что gateway получает вместо BotEntry: запуск + платформа."""
    tenant_id: int
    bot: MaxBot
    platform: str = "max"


def route_start(bot: MaxBot, payload: str) -> tuple[int, str] | None:
    """Метка входа → (запуск, payload без метки). None – вход не принят."""
    payload = (payload or "").strip()
    for lk in sorted(bot.links, key=lambda x: len(x.prefix), reverse=True):
        if not lk.prefix:
            continue
        if payload.startswith(lk.prefix):
            return lk.tenant_id, payload[len(lk.prefix):]
        if payload == lk.prefix.rstrip("_"):
            return lk.tenant_id, ""
    d = bot.default_tenant()
    return (d, payload) if d is not None else None


def split_callback(payload: str) -> tuple[str, str] | None:
    """`f:<slug>:<данные>` → (slug, данные). Без слага – None."""
    if not payload.startswith("f:"):
        return None
    parts = payload.split(":", 2)
    if len(parts) < 3 or not parts[1]:
        return None
    return parts[1], parts[2]


def person(u: dict) -> TgUser:
    """Имя человека MAX: `name` бывает пустым – тогда first/last/username."""
    uid = int(u.get("user_id") or 0)
    fn = (u.get("first_name") or "").strip()
    ln = (u.get("last_name") or "").strip()
    if not fn:
        name = (u.get("name") or "").strip()
        if name:
            parts = name.split()
            fn, ln = parts[0], ln or " ".join(parts[1:])
    un = (u.get("username") or "").strip().lstrip("@") or None
    return TgUser(uid, fn or un or None, ln or None, un)


def extract_contact_phone(attachments) -> str | None:
    """Телефон из вложения `contact` (тап по кнопке request_contact). Форма
    payload'а у MAX не задокументирована – обходим рекурсивно, берём первое
    похожее на номер из ключей «…phone…» или из TEL-строки vCard (как
    services/max_phone.extract_contact_phone в bot-engine)."""
    def walk(node, depth=0):
        if depth > 6:
            return None
        if isinstance(node, str):
            for line in node.splitlines():
                if "TEL" in line.upper() and ":" in line:
                    got = _normalize_phone(line.rsplit(":", 1)[-1])
                    if got:
                        return got
            return None
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (str, int)) and "phone" in str(k).lower():
                    got = _normalize_phone(str(v))
                    if got:
                        return got
            for v in node.values():
                got = walk(v, depth + 1)
                if got:
                    return got
        if isinstance(node, (list, tuple)):
            for v in node:
                got = walk(v, depth + 1)
                if got:
                    return got
        return None

    for att in attachments or []:
        if isinstance(att, dict) and str(att.get("type") or "").lower() == "contact":
            got = walk(att)
            if got:
                return got
            log.warning("MAX: во вложении contact не нашли телефон: %s", str(att)[:400])
    return None


_TRANSIENT = (OSError, asyncpg.PostgresConnectionError, asyncpg.InterfaceError,
              asyncio.TimeoutError)


class MaxHub:
    def __init__(self, rt: "Runtime", api_factory: ApiFactory | None = None):
        self.rt = rt
        self._factory = api_factory or (lambda token: MaxApi(
            token, os.environ.get("STUDIO_MAX_API_BASE") or DEFAULT_BASE_URL))
        self.bots: dict[int, MaxBot] = {}
        self.poll_timeout = 30
        self.error_pause = 5.0

    # ── боты ─────────────────────────────────────────────────────────────
    async def load(self) -> None:
        async with self.rt.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM bots WHERE enabled AND platform='max' ORDER BY id")
            links = await conn.fetch(
                """SELECT bt.* FROM bot_tenants bt JOIN bots b ON b.id=bt.bot_id
                   WHERE b.platform='max' ORDER BY bt.bot_id, bt.tenant_id""")
        by_bot: dict[int, list[MaxLink]] = {}
        for lk in links:
            if lk["tenant_id"] in self.rt.registry.tenants:
                by_bot.setdefault(lk["bot_id"], []).append(
                    MaxLink(lk["tenant_id"], lk["prefix"], lk["is_default"]))
        for r in rows:
            lks = by_bot.get(r["id"])
            if lks is None and r["tenant_id"] in self.rt.registry.tenants:
                # бот заведён без таблицы привязок – он бот одного запуска
                slug = self.rt.registry.tenant(r["tenant_id"]).slug
                lks = [MaxLink(r["tenant_id"], f"{slug}_", True)]
            if not lks:
                continue
            bot = self.bots.get(r["id"])
            if bot is None:
                try:
                    token = resolve_secret(r["secret_ref"])
                except Exception as e:  # noqa: BLE001
                    log.error("MAX-бот #%s: %s – пропускаем", r["id"], e)
                    continue
                bot = MaxBot(row_id=r["id"], api=self._factory(token), username=r["username"],
                             platform_bot_id=r["platform_bot_id"], marker=r["poll_marker"])
                self.bots[r["id"]] = bot
                log.info("MAX-бот #%s (токен %s) → запуски %s", r["id"], mask(token),
                         ", ".join(self._describe(lk) for lk in lks))
            bot.links = lks

    def _describe(self, lk: MaxLink) -> str:
        slug = self.rt.registry.tenant(lk.tenant_id).slug
        return f"{slug} (метка «{lk.prefix}»{', по умолчанию' if lk.is_default else ''})"

    def bot_for_tenant(self, tenant_id: int) -> MaxBot | None:
        """MAX-бот, через который запуск пишет людям (первый по id)."""
        for bot in sorted(self.bots.values(), key=lambda b: b.row_id):
            if any(lk.tenant_id == tenant_id for lk in bot.links):
                return bot
        return None

    def _tenant_by_slug(self, bot: MaxBot, slug: str) -> int | None:
        for lk in bot.links:
            t = self.rt.registry.tenants.get(lk.tenant_id)
            if t is not None and t.slug == slug:
                return lk.tenant_id
        return None

    async def _current_tenant(self, bot: MaxBot, uid: int) -> int | None:
        """Где человек сейчас: ждут ответа → самая свежая позиция → участник."""
        tids = [lk.tenant_id for lk in bot.links]
        async with self.rt.pool.acquire() as conn:
            tid = await conn.fetchval(
                """SELECT p.tenant_id FROM participants p
                   LEFT JOIN LATERAL (
                       SELECT bool_or(po.awaiting IS NOT NULL) aw, max(po.updated_at) upd
                       FROM positions po WHERE po.tenant_id=p.tenant_id AND po.platform='max'
                         AND po.uid=p.uid) x ON true
                   WHERE p.platform='max' AND p.uid=$1 AND p.tenant_id = ANY($2)
                   ORDER BY coalesce(x.aw, false) DESC, x.upd DESC NULLS LAST, p.updated_at DESC
                   LIMIT 1""", uid, tids)
        return tid

    # ── апдейт ───────────────────────────────────────────────────────────
    async def handle_update(self, bot: MaxBot, upd: dict) -> str:
        """Разбирает один апдейт MAX. → что сделали (для логов и тестов)."""
        t = upd.get("update_type") or ""
        if t == "bot_started":
            u = person(upd.get("user") or {})
            key = f"max{bot.row_id}:bs:{u.id}:{upd.get('timestamp') or ''}"
            return await self._start(bot, u, upd.get("payload") or "", key)
        if t == "message_created":
            return await self._message(bot, upd)
        if t == "message_callback":
            return await self._callback(bot, upd)
        if t in ("bot_stopped", "dialog_removed"):
            u = person(upd.get("user") or {})
            n = 0
            for lk in bot.links:
                await self.rt.gateway.on_block(MaxRoute(lk.tenant_id, bot), u.id, True)
                n += 1
            log.info("MAX %s остановил бота #%s (%s) – блок в %s запусках", u.id, bot.row_id, t, n)
            return "blocked"
        return "skipped"

    async def _start(self, bot: MaxBot, u: TgUser, payload: str, key: str) -> str:
        if u.id <= 0:
            return "no_user"
        routed = route_start(bot, payload)
        if routed is None:
            log.warning("MAX-бот #%s: вход %s с меткой %r – ни один запуск её не знает, "
                        "запуска по умолчанию нет", bot.row_id, u.id, payload)
            return "no_tenant"
        tid, rest = routed
        return await self.rt.gateway.on_start(MaxRoute(tid, bot), u, rest, key)

    async def _message(self, bot: MaxBot, upd: dict) -> str:
        msg = upd.get("message") or {}
        body = msg.get("body") or {}
        u = person(msg.get("sender") or {})
        if u.id <= 0:
            return "no_user"
        text = (body.get("text") or "").strip()
        key = f"max{bot.row_id}:{body.get('mid') or upd.get('timestamp') or ''}"
        if text.startswith("/start"):
            # повторный вход: MAX шлёт bot_started только один раз
            parts = text.split(maxsplit=1)
            return await self._start(bot, u, parts[1].strip() if len(parts) > 1 else "", key)
        tid = await self._current_tenant(bot, u.id)
        if tid is None:
            log.info("MAX-бот #%s: сообщение от %s, который ни в одном запуске бота", bot.row_id, u.id)
            return "unknown_user"
        route = MaxRoute(tid, bot)
        phone = extract_contact_phone(body.get("attachments")) if not text else None
        if phone:
            return await self.rt.gateway.on_contact(route, u.id, phone, key)
        if not text:
            return "skipped"
        reply = await self.rt.gateway.on_text(route, u.id, text, key)
        if reply:
            try:
                await bot.api.send_message(user_id=u.id, text=reply)
            except Exception as e:  # noqa: BLE001
                log.warning("MAX: ответ %s не ушёл: %s", u.id, e)
        return "text"

    async def _callback(self, bot: MaxBot, upd: dict) -> str:
        cb = upd.get("callback") or {}
        payload = (cb.get("payload") or "").strip()
        cb_id = cb.get("callback_id") or ""
        u = person(cb.get("user") or {})
        ack, result = "", "ignored"
        split = split_callback(payload)
        if u.id <= 0:
            result = "no_user"
        elif split is None:
            log.info("MAX-бот #%s: кнопка без слага %r от %s – в воронки не передаём (§36)",
                     bot.row_id, payload, u.id)
        else:
            slug, data = split
            tid = self._tenant_by_slug(bot, slug)
            if tid is None:
                log.info("MAX-бот #%s: кнопка запуска «%s», которого у бота нет (%s)",
                         bot.row_id, slug, u.id)
                ack, result = "Эта кнопка устарела", "foreign"
            else:
                try:
                    ack = await self.rt.gateway.on_callback(MaxRoute(tid, bot), u.id, data,
                                                            f"max{bot.row_id}:cb{cb_id}")
                    result = "callback"
                except Exception:  # noqa: BLE001
                    log.exception("MAX: кнопка %r от %s не обработана", payload, u.id)
                    result = "error"
        if cb_id:
            try:
                await bot.api.answer_callback(cb_id, notification=ack or CLAIM_ACK)
            except Exception as e:  # noqa: BLE001
                log.debug("MAX answer_callback: %s", e)
        return result

    # ── long-poll ────────────────────────────────────────────────────────
    async def _save_marker(self, bot: MaxBot) -> None:
        async with self.rt.pool.acquire() as conn:
            await conn.execute("UPDATE bots SET poll_marker=$2 WHERE id=$1", bot.row_id, bot.marker)

    async def poll_once(self, bot: MaxBot, *, timeout: int | None = None) -> int:
        """Одна пачка апдейтов. → сколько обработано. Маркер двигается, только
        если пачка разобрана (сбой базы – перечитаем ту же пачку)."""
        resp = await bot.api.get_updates(bot.marker, timeout=self.poll_timeout if timeout is None
                                         else timeout, types=UPDATE_TYPES)
        ups = (resp or {}).get("updates") or []
        done = 0
        for upd in ups:
            try:
                res = await self.handle_update(bot, upd)
                log.debug("MAX #%s %s → %s", bot.row_id, upd.get("update_type"), res)
            except _TRANSIENT:
                log.exception("MAX #%s: сбой при разборе апдейта – перечитаем пачку", bot.row_id)
                return done
            except Exception:  # noqa: BLE001 – «ядовитый» апдейт не должен стопорить бота
                log.exception("MAX #%s: апдейт %s не разобран – пропускаем", bot.row_id,
                              upd.get("update_type"))
            done += 1
        nxt = (resp or {}).get("marker")
        if nxt is not None and nxt != bot.marker:
            bot.marker = nxt
            await self._save_marker(bot)
        return done

    async def _poll_forever(self, bot: MaxBot, stop: asyncio.Event) -> None:
        log.info("MAX-бот #%s @%s: long-poll запущен", bot.row_id, bot.username or "?")
        while not stop.is_set():
            task = asyncio.create_task(self.poll_once(bot))
            stopper = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if task not in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                break
            stopper.cancel()
            exc = task.exception()
            if exc is not None:
                log.warning("MAX-бот #%s: long-poll не удался (%s) – пауза %s с", bot.row_id, exc,
                            self.error_pause)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.error_pause)
                except asyncio.TimeoutError:
                    pass
        log.info("MAX-бот #%s: long-poll остановлен", bot.row_id)

    async def identify(self) -> None:
        """/me каждого бота – username и id в bots (для status)."""
        for bot in self.bots.values():
            try:
                me = await bot.api.get_me()
            except Exception as e:  # noqa: BLE001
                log.error("MAX-бот #%s: /me не ответил: %s", bot.row_id, e)
                continue
            bot.username = me.get("username") or bot.username
            bot.platform_bot_id = me.get("user_id") or bot.platform_bot_id
            async with self.rt.pool.acquire() as conn:
                await conn.execute("UPDATE bots SET username=$2, platform_bot_id=$3 WHERE id=$1",
                                   bot.row_id, bot.username, bot.platform_bot_id)
            log.info("MAX /me ок: @%s (id %s) → %s", bot.username, bot.platform_bot_id,
                     ", ".join(self._describe(lk) for lk in bot.links))

    def start_polling(self, stop: asyncio.Event) -> list[asyncio.Task]:
        return [asyncio.create_task(self._poll_forever(b, stop), name=f"max-poll-{b.row_id}")
                for b in self.bots.values()]

    async def close(self) -> None:
        for bot in self.bots.values():
            try:
                await bot.api.close()
            except Exception:  # noqa: BLE001
                pass
