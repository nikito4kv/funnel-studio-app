"""Источник «кликнул»: своя прокси-ссылка движка и учёт кликов.

  {base_url}/go/<code>?u=<id>&p=<tg|max>   302 на исходный адрес + запись клика
  {base_url}/t/<запуск>/govip?u=&pl=&s=     ВИП-страница с данными человека
                                             ({vip_link} в текстах, render.py)

Обёртка при отправке (`wrap_message`): кнопки-ссылки и ссылки в тексте
заменяются на /go. Метки – как в bot-engine, чтобы гейты импортированных воронок
(`no_link_click: "funnel_s_d3_"`) работали без правок:
  кнопка – `funnel_<ключ шага>`, ссылка из текста – `text_<ключ шага>`,
  ВИП – `vip_<ключ шага>`.
Не оборачиваются: t.me / max.ru / vk (переход в мессенджер), tg://, web_app,
уже наши ссылки.

§22: факт клика берётся из событий (gate_clicks), а не из UTM человека.
§26: в MAX ссылку ИЗ ТЕКСТА платформа открывает сама при доставке – там такие
ссылки не оборачиваются вовсе, а человеческим кликом считаются только кнопки
(и ВИП-прокси). Переходы превью-роботов (TelegramBot и т.п.) пишутся с
is_bot и гейтами не считаются.
"""
from __future__ import annotations

import html
import logging
import re
import secrets
import string
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .base import as_int

if TYPE_CHECKING:
    from ..registry import Tenant
    from ..service import Runtime

log = logging.getLogger("runtime.gates.links")

_ALPH = string.ascii_letters + string.digits
_HREF = re.compile(r'(<a\s[^>]*?href=")([^"]+)(")', re.I)
_TAG = re.compile(r"<[^>]+>")
_BARE = re.compile(r"https?://[^\s<>\"']+")
_BOT_UA = re.compile(r"bot|preview|crawler|spider|facebookexternalhit|vkshare|whatsapp", re.I)
HUMAN_SOURCES_MAX = ("button", "vip")


def _code() -> str:
    return "".join(secrets.choice(_ALPH) for _ in range(9))


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def trackable(url: str, t: "Tenant") -> bool:
    ls = t.settings.links
    if not url.lower().startswith(("http://", "https://")):
        return False
    if url.startswith(ls.base + "/"):
        return False   # уже наша (/govip, /go)
    host = (urlsplit(url).hostname or "").lower()
    return not any(host == h or host.endswith("." + h) for h in ls.skip_hosts)


async def link_code(conn, t: "Tenant", url: str, label: str, source: str, now: datetime) -> str:
    """Код ссылки (одна на запуск+адрес+метку+источник – повторный рендер даёт тот же)."""
    code = await conn.fetchval(
        "SELECT code FROM gate_links WHERE tenant_id=$1 AND target_url=$2 AND label=$3 AND source=$4",
        t.id, url, label, source)
    if code:
        return code
    for _ in range(5):
        code = await conn.fetchval(
            """INSERT INTO gate_links (tenant_id, code, target_url, label, source, created_at)
               VALUES ($1,$2,$3,$4,$5,$6)
               ON CONFLICT (tenant_id, target_url, label, source) DO UPDATE SET code=gate_links.code
               RETURNING code""", t.id, _code(), url, label, source, now)
        if code:
            return code
    raise RuntimeError("не удалось завести прокси-ссылку")


def go_url(t: "Tenant", code: str, platform: str, uid: int) -> str:
    return f"{t.settings.links.base}/go/{code}?u={uid}&p={platform}"


async def wrap_message(rt: "Runtime", tenant: "Tenant", platform: str, uid: int, step,
                       text: str, kb):
    """Оборачивает ссылки сообщения в /go. Ссылки не включены – всё как было."""
    ls = tenant.settings.links
    if not ls.configured():
        return text, kb
    now = rt.clock.now()
    async with rt.pool.acquire() as conn:
        if text and ls.wrap_text and platform != "max":
            text = await _wrap_text(conn, tenant, platform, uid, text, f"text_{step.key}", now)
        if ls.wrap_buttons and isinstance(kb, InlineKeyboardMarkup):
            rows = []
            for row in kb.inline_keyboard:
                new_row = []
                for b in row:
                    if b.url and not b.web_app and trackable(b.url, tenant):
                        code = await link_code(conn, tenant, b.url, f"funnel_{step.key}", "button", now)
                        b = b.model_copy(update={"url": go_url(tenant, code, platform, uid)})
                    new_row.append(b)
                rows.append(new_row)
            kb = InlineKeyboardMarkup(inline_keyboard=rows)
    return text, kb


async def wrap_max_rows(rt: "Runtime", tenant: "Tenant", uid: int, step,
                        rows: list[list[dict]]) -> list[list[dict]]:
    """То же для MAX: кнопки-ссылки (`{"type": "link", "url": …}`) → /go с p=max.
    Текст в MAX не оборачивается никогда (§26: платформа сама открывает ссылки
    из текста при доставке – клик был бы у каждого получателя). Честный клик
    в MAX – кнопка (метка `funnel_<шаг>`, источник button) и ВИП-прокси."""
    ls = tenant.settings.links
    if not ls.configured() or not ls.wrap_buttons or not rows:
        return rows
    now = rt.clock.now()
    out: list[list[dict]] = []
    async with rt.pool.acquire() as conn:
        for row in rows:
            new_row = []
            for b in row:
                url = b.get("url") if b.get("type") == "link" else None
                if url and trackable(url, tenant):
                    code = await link_code(conn, tenant, url, f"funnel_{step.key}", "button", now)
                    b = {**b, "url": go_url(tenant, code, "max", uid)}
                new_row.append(b)
            out.append(new_row)
    return out


async def _wrap_text(conn, t, platform, uid, text: str, label: str, now) -> str:
    """href в <a> и голые адреса вне тегов (как _wrap_text_urls bot-engine)."""
    async def sub(url: str) -> str:
        raw = html.unescape(url)
        if not trackable(raw, t):
            return url
        code = await link_code(conn, t, raw, label, "text", now)
        return html.escape(go_url(t, code, platform, uid), quote=True)

    out, pos, in_a = [], 0, False
    # один проход: теги (с href) и текст между ними; подпись внутри <a>…</a> не трогаем
    for m in _TAG.finditer(text):
        chunk = text[pos:m.start()]
        out.append(chunk if in_a else await _wrap_bare(chunk, sub))
        tag = m.group(0)
        low = tag.lower()
        if low.startswith("<a "):
            in_a = True
        elif low.startswith("</a"):
            in_a = False
        hm = _HREF.search(tag)
        if hm:
            tag = tag[:hm.start(2)] + await sub(hm.group(2)) + tag[hm.end(2):]
        out.append(tag)
        pos = m.end()
    out.append(await _wrap_bare(text[pos:], sub))
    return "".join(out)


async def _wrap_bare(chunk: str, sub) -> str:
    if "http" not in chunk:
        return chunk
    parts, pos = [], 0
    for m in _BARE.finditer(chunk):
        url = m.group(0).rstrip(".,;:!?)»")
        parts.append(chunk[pos:m.start()])
        parts.append(await sub(url))
        pos = m.start() + len(url)
    parts.append(chunk[pos:])
    return "".join(parts)


# ── приём кликов ─────────────────────────────────────────────────────────────

def _with_params(url: str, extra: dict[str, str]) -> str:
    sp = urlsplit(url)
    q = dict(parse_qsl(sp.query, keep_blank_values=True))
    q.update({k: v for k, v in extra.items() if v not in (None, "")})
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(q), sp.fragment))


async def _record(conn, *, tenant_id, link_id, platform, uid, label, source, target, ua, now) -> None:
    await conn.execute(
        """INSERT INTO gate_clicks (tenant_id, link_id, platform, uid, label, source, target_url,
                                    user_agent, is_bot, clicked_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
        tenant_id, link_id, platform, uid, label, source, target, (ua or "")[:300] or None,
        bool(ua and _BOT_UA.search(ua)), now)


async def go(rt: "Runtime", code: str, query: dict, user_agent: str | None) -> tuple[int, str]:
    """/go/<code> → (код, адрес для Location или текст ошибки)."""
    async with rt.pool.acquire() as conn:
        link = await conn.fetchrow("SELECT * FROM gate_links WHERE code=$1", code)
        if link is None:
            return 404, "ссылка не найдена"
        target = link["target_url"]
        uid = as_int(query.get("u"))
        platform = (query.get("p") or "tg").strip().lower()[:8]
        t = rt.registry.tenants.get(link["tenant_id"])
        if uid:
            try:
                now = rt.clock.now()
                await _record(conn, tenant_id=link["tenant_id"], link_id=link["id"], platform=platform,
                              uid=uid, label=link["label"], source=link["source"], target=target,
                              ua=user_agent, now=now)
                if t is not None and _is_bizon(target) and t.settings.bizon.autologin:
                    target = _with_params(target, await _person_params(conn, t, platform, uid,
                                                                       autologin=True))
            except Exception:  # noqa: BLE001 – человек должен дойти до цели при любом сбое учёта
                log.exception("клик по /go/%s (u=%s) не записан", code, uid)
    return 302, target


def _is_bizon(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower().endswith("bizon365.ru")


async def _person_params(conn, t, platform, uid, *, autologin=False) -> dict[str, str]:
    p = await conn.fetchrow("SELECT * FROM participants WHERE tenant_id=$1 AND platform=$2 AND uid=$3",
                            t.id, platform, uid)
    out = {"id": str(uid)}
    if p is not None:
        out.update({"phone": p["phone"] or "", "email": p["email"] or ""})
        out["username" if autologin else "name"] = p["first_name"] or ""
        for k in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"):
            out[k] = p[k] or ""
    if autologin:
        out["autologin"] = "1"
    return out


async def govip(rt: "Runtime", slug: str, query: dict, user_agent: str | None) -> tuple[int, str]:
    """/t/<запуск>/govip: клик `vip_<шаг>` + редирект на ВИП-страницу с id человека.

    `id` касса кладёт в bot_meta и возвращает в вебхуке – по нему оплата
    привязывается к человеку точно, без телефона. `utm_content` – метка шага
    (из какого сообщения купили), остальные UTM – исходные метки человека.
    """
    t = rt.registry.by_slug(slug)
    if t is None:
        return 404, "запуск не найден"
    target = t.settings.webinar.vip_target
    if not target:
        return 404, "страница ВИПа у запуска не настроена"
    uid = as_int(query.get("u"))
    platform = (query.get("pl") or query.get("p") or "tg").strip().lower()[:8]
    step = re.sub(r"[^A-Za-z0-9_-]", "", str(query.get("s") or ""))[:64]
    label = f"vip_{step}" if step else "vip"
    if uid is None:
        return 302, target
    extra: dict[str, str] = {}
    async with rt.pool.acquire() as conn:
        try:
            await _record(conn, tenant_id=t.id, link_id=None, platform=platform, uid=uid, label=label,
                          source="vip", target=target, ua=user_agent, now=rt.clock.now())
            extra = await _person_params(conn, t, platform, uid)
        except Exception:  # noqa: BLE001
            log.exception("[%s] клик по /govip (u=%s) не записан", slug, uid)
            extra = {"id": str(uid)}
    extra["tg_id" if platform == "tg" else f"{platform}_id"] = str(uid)
    extra["pl"] = platform
    extra["utm_content"] = label
    return 302, _with_params(target, extra)


# ── гейт ─────────────────────────────────────────────────────────────────────

async def clicked(conn, t: "Tenant", platform: str, uid: int, prefix: str) -> bool | None:
    """Кликал ли человек ссылку с меткой на `prefix`. None – ссылки движка не включены."""
    if not t.settings.links.configured():
        return None
    return bool(await conn.fetchval(
        """SELECT EXISTS (SELECT 1 FROM gate_clicks WHERE tenant_id=$1 AND platform=$2 AND uid=$3
             AND NOT is_bot AND label LIKE $4 ESCAPE '\\'
             AND ($5::text[] IS NULL OR source = ANY($5)))""",
        t.id, platform, uid, _escape_like(prefix) + "%",
        list(HUMAN_SOURCES_MAX) if platform == "max" else None))


async def clicked_room(conn, t: "Tenant", platform: str, uid: int, room: str,
                       window: tuple[datetime, datetime] | None) -> bool:
    """Переходил ли по ссылке эфира (адрес с именем зала) в окне – сигнал «был»."""
    if not room or not t.settings.links.configured():
        return False
    return bool(await conn.fetchval(
        """SELECT EXISTS (SELECT 1 FROM gate_clicks WHERE tenant_id=$1 AND platform=$2 AND uid=$3
             AND NOT is_bot AND target_url LIKE $4 ESCAPE '\\'
             AND ($5::timestamptz IS NULL OR (clicked_at >= $5 AND clicked_at < $6))
             AND ($7::text[] IS NULL OR source = ANY($7)))""",
        t.id, platform, uid, "%" + _escape_like(room) + "%",
        window[0] if window else None, window[1] if window else None,
        list(HUMAN_SOURCES_MAX) if platform == "max" else None))
