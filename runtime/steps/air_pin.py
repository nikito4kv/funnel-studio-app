"""Закреп с отсчётом до эфира – перенос services/air_pin.py bot-engine.

Два способа поставить закреп (как в движке):
  * шаг `type: air_pin` – отдельное сообщение-закреп (своего текста у шага
    нет, содержимое собирается здесь); у человека он уже есть – просто
    перерисовывается под текущее состояние;
  * обычный шаг с `pin.air_pin` – «усыновление»: шаг ушёл, сверху его текста
    дорисовывается шапка с отсчётом, кнопка шага меняется на кнопку закрепа,
    сообщение закрепляется.

Состояния (state_at): ждём (`wait`) → за soon_minutes «скоро начинаем»
(`soon`, кнопка уже в зал) → эфир идёт (`live`) → следующий эфир → … →
все прошли (`done`: after_text или закреп снимается). Наш текст «4 дня / 6 ч.»
меняется на смене гранулы отсчёта (сутки → часы → минуты) – поэтому у
каждого закрепа в базе своё `next_at`: момент ближайшей перерисовки. Его
обходит `refresh_due` по часам движка (фоновая задача процесса, раз в
REFRESH_EVERY_S), а не джобы APScheduler, как в bot-engine.

Дни эфиров: `air_pin.days` в настройках запуска (фиксированные даты), а
если их нет – эфир САМОГО человека из его позиции (branch_router) или
`webinar.datetime_msk`: так закреп работает и у вечнозелёного автовеба.

Отличия от bot-engine:
  * ссылка на зал – как есть (у движка Студии нет персональных прокси-ссылок
    /go с автовходом в Bizon);
  * значки – обычные эмодзи (премиум-набор ui_emoji и icon_custom_emoji_id
    кнопок не перенесены);
  * кружок с pin.air_pin (текста нет – дорисовывать не на что) – закреп
    ставится отдельным сообщением, а не молча пропадает.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from studio.model.funnel import EdgeKind, Step, StepType

from ..clock import MSK
from ..planner import plan_also, plan_edge
from .common import begin, finish, mark_blocked, sent_row
from .config import AirPinSettings

if TYPE_CHECKING:
    from ..executor import Executor, UserCtx
    from ..graph import CompiledFunnel
    from ..repo import Outcome

log = logging.getLogger("runtime.steps.air_pin")

REFRESH_EVERY_S = 15
_ORDINAL = ("Первый эфир", "Второй эфир", "Третий эфир", "Четвёртый эфир", "Пятый эфир")
_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
           "октября", "ноября", "декабря")
_DEFAULT = {
    "wait": "{счётчик} до эфира.\n{дата}",
    "soon": "{title} →\nЗаходите в зал – скоро начинаем (в {время} МСК)",
    "live": "{title} идёт, вход →\nЗаходите в зал – начали.",
}


@dataclass
class Day:
    i: int
    start: datetime          # aware, МСК
    end: datetime
    title: str
    url: str = ""
    subtitle: str = ""
    desc: str = ""


# ── расписание ───────────────────────────────────────────────────────────────

def days_for(ap: AirPinSettings, *, webinar_at: datetime | None = None, fallback_iso: str = "",
             stream_url: str = "") -> list[Day]:
    out: list[Day] = []
    src = [d for d in ap.days if (d.when or "").strip()]
    for i, d in enumerate(src):
        try:
            start = datetime.fromisoformat(d.when.strip())
        except ValueError:
            log.warning("air_pin: не разобрал дату «%s» – день пропущен", d.when)
            continue
        start = MSK.localize(start) if start.tzinfo is None else start.astimezone(MSK)
        title = (d.title or "").strip()
        if not title or len(title) > 32:
            title = _ORDINAL[i] if i < len(_ORDINAL) else f"Эфир {i + 1}"
        out.append(Day(i, start, start + timedelta(minutes=int(d.live_minutes or ap.live_minutes)),
                       title, (d.url or "").strip(), (d.subtitle or "").strip(), (d.desc or "").strip()))
    if not src:
        start = webinar_at.astimezone(MSK) if webinar_at else None
        if start is None and fallback_iso:
            try:
                s = datetime.fromisoformat(fallback_iso)
                start = MSK.localize(s) if s.tzinfo is None else s.astimezone(MSK)
            except ValueError:
                start = None
        if start is not None:
            out.append(Day(0, start, start + timedelta(minutes=ap.live_minutes),
                           ap.title or "Прямой эфир", stream_url))
    out.sort(key=lambda d: d.start)
    return out


def state_at(days: list[Day], now: datetime, soon_minutes: int) -> tuple[str, Day | None]:
    now = now.astimezone(MSK)
    for d in days:
        if now < d.start:
            if soon_minutes and now >= d.start - timedelta(minutes=soon_minutes):
                return "soon", d
            return "wait", d
        if now < d.end:
            return "live", d
    return "done", None


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    d = n % 10
    if 10 < n < 20:
        return many
    if 1 < d < 5:
        return few
    return one if d == 1 else many


def _unit(sec: float) -> int:
    return 86400 if sec >= 86400 else 3600 if sec >= 3600 else 60


def counter_text(start: datetime, now: datetime) -> str:
    sec = (start - now).total_seconds()
    if sec <= 0:
        return "вот-вот"
    if sec >= 86400:
        n = int(sec // 86400)
        return f"{n} {_plural(n, 'день', 'дня', 'дней')}"
    if sec >= 3600:
        return f"{int(sec // 3600)} ч."
    return f"{max(1, int(sec // 60))} мин."


def left_text(start: datetime, now: datetime) -> str:
    sec = (start - now).total_seconds()
    if sec <= 0:
        return "вот-вот"
    if sec >= 86400:
        n = int(sec // 86400)
        return f"через {n} {_plural(n, 'день', 'дня', 'дней')}"
    if sec >= 3600:
        n = int(sec // 3600)
        return f"через {n} {_plural(n, 'час', 'часа', 'часов')}"
    n = max(1, int(sec // 60))
    return f"через {n} {_plural(n, 'минуту', 'минуты', 'минут')}"


def state_key(days: list[Day], now: datetime, soon_minutes: int) -> str:
    kind, d = state_at(days, now, soon_minutes)
    key = f"{kind}:{d.i if d else '-'}"
    if d is not None and kind == "wait":
        sec = (d.start - now).total_seconds()
        u = _unit(sec)
        key += f":{'dhm'[(86400, 3600, 60).index(u)]}{int(sec // u)}"
    return key


def next_change(days: list[Day], now: datetime, soon_minutes: int) -> datetime | None:
    """Ближайший момент, когда закреп должен измениться (+1 с за границу)."""
    pts: list[datetime] = []
    for d in days:
        if soon_minutes:
            pts.append(d.start - timedelta(minutes=soon_minutes))
        pts += [d.start, d.end]
    cand = [p for p in pts if p > now]
    kind, d = state_at(days, now, soon_minutes)
    if kind == "wait" and d is not None:
        sec = (d.start - now).total_seconds()
        u = _unit(sec)
        cand.append(d.start - timedelta(seconds=int(sec // u) * u))
    cand = [c for c in cand if c >= now]
    return (min(cand) + timedelta(seconds=1)) if cand else None


# ── текст ────────────────────────────────────────────────────────────────────

def render(ap: AirPinSettings, days: list[Day], now: datetime, *, channel_link: str = "") \
        -> tuple[str, str, str]:
    """(html, url кнопки, подпись кнопки) для текущего состояния. Пустой html –
    показывать нечего (эфиры прошли, after_text пуст) – закреп снимается."""
    kind, d = state_at(days, now, ap.soon_minutes)
    if kind == "done" or d is None:
        return (ap.after_text or ""), "", ""
    link = d.url
    ts = int(d.start.timestamp())
    human = f"{d.start.day} {_MONTHS[d.start.month - 1]}, {d.start:%H:%M} МСК"
    fields = {
        "title": d.title,
        "через": f'<tg-time unix="{ts}" format="r">совсем скоро</tg-time>',
        "дата": f'<tg-time unix="{ts}">{human}</tg-time>',
        "ссылка": f'<a href="{link}">смотреть</a>' if link else "ссылка вот-вот появится здесь",
        "время": f"{d.start:%H:%M}",
        "осталось": left_text(d.start, now),
        "счётчик": counter_text(d.start, now),
    }
    tpl = (getattr(ap, f"text_{kind}", "") or "").strip() or _DEFAULT[kind]
    try:
        body = tpl.format(**fields)
    except (KeyError, IndexError, ValueError):
        log.warning("air_pin: в тексте состояния «%s» неизвестная подстановка – беру по умолчанию", kind)
        body = _DEFAULT[kind].format(**fields)
    if not link and kind in ("soon", "live"):
        body = re.sub(r"[,\s]*(?:вход|смотреть)?\s*(?:→|->)\s*$", "", body.split("\n")[0]).rstrip()
        body += "\nСсылка на зал появится здесь."
    quote = ""
    if ap.quote and (d.subtitle or d.desc):
        parts = ([f"<b>{d.subtitle}</b>"] if d.subtitle else []) + ([f"<i>{d.desc}</i>"] if d.desc else [])
        quote = "\n\n<blockquote>" + "\n".join(parts) + "</blockquote>"
    lines = body.split("\n")
    emoji = getattr(ap, f"emoji_{kind}", "") or ""
    head = f"{emoji} <b>{lines[0]}</b>".strip()
    rest = ("\n" + "\n".join(lines[1:])) if len(lines) > 1 else ""
    if kind == "wait":
        return f"{head}{rest}{quote}", (ap.button_wait_url or channel_link), ap.button_wait_text
    return f"{head}{rest}{quote}", link, ap.button_text


def keyboard(url: str, text: str) -> InlineKeyboardMarkup | None:
    if not url or not text or not url.startswith(("https://", "http://", "tg://")):
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, url=url)]])


def compose(head: str, body: str) -> str:
    body = (body or "").strip()
    return f"{head}\n\n{body}" if body else head


# ── контекст человека ────────────────────────────────────────────────────────

@dataclass
class PinCtx:
    ap: AirPinSettings
    days: list[Day]
    channel_link: str


def pin_ctx(rt, tenant_id: int, g: "CompiledFunnel | None", webinar_at) -> PinCtx:
    from .config import steps_settings
    tenant = rt.registry.tenant(tenant_id)
    try:
        ap = steps_settings(tenant.settings).air_pin
    except Exception:  # noqa: BLE001
        ap = AirPinSettings()
    room = g.funnel.webinar_room if g is not None else None
    stream = (room.stream_url if room and room.stream_url else "") or tenant.settings.webinar.stream_url
    days = days_for(ap, webinar_at=webinar_at, fallback_iso=tenant.settings.webinar.datetime_msk,
                    stream_url=stream)
    return PinCtx(ap, days, tenant.settings.channel.link)


# ── Telegram ─────────────────────────────────────────────────────────────────

async def _edit(bot, uid: int, mid: int, html: str, kb, kind: str) -> str:
    """→ ok / gone (сообщения или чата нет, заблокировал) / retry / kind (другой вид сообщения)."""
    try:
        if kind == "caption":
            await bot.edit_message_caption(chat_id=uid, message_id=mid, caption=html, reply_markup=kb)
        else:
            await bot.edit_message_text(chat_id=uid, message_id=mid, text=html, reply_markup=kb,
                                        disable_web_page_preview=True)
        return "ok"
    except TelegramRetryAfter:
        return "retry"
    except TelegramForbiddenError:
        return "gone"
    except TelegramBadRequest as e:
        m = str(e)
        if "not modified" in m:
            return "ok"
        if "not found" in m or "MESSAGE_ID_INVALID" in m:
            return "gone"
        if "no text" in m or "no caption" in m or "there is no" in m:
            return "kind"
        return "retry"
    except Exception:  # noqa: BLE001
        return "retry"


async def _edit_any(bot, uid, mid, html, kb, kind) -> tuple[str, str]:
    r = await _edit(bot, uid, mid, html, kb, kind)
    if r == "kind":
        kind = "text" if kind == "caption" else "caption"
        r = await _edit(bot, uid, mid, html, kb, kind)
        if r == "kind":
            r = "retry"
    return r, kind


async def _pin(bot, uid: int, mid: int) -> bool:
    try:
        await bot.pin_chat_message(uid, mid, disable_notification=True)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("закреп не встал у %s: %s", uid, e)
        return False


async def _remember(conn, *, tenant_id, platform, uid, funnel_id, mid, state, now, next_at,
                    body=None, kind=None, pin_at=None) -> None:
    sets = ["funnel_id=EXCLUDED.funnel_id", "message_id=EXCLUDED.message_id", "state=EXCLUDED.state",
            "next_at=EXCLUDED.next_at", "tries=0", "updated_at=EXCLUDED.updated_at"]
    if body is not None:
        sets += ["body=EXCLUDED.body", "kind=EXCLUDED.kind"]
    if pin_at is not None:
        sets.append("pin_at=EXCLUDED.pin_at")
    await conn.execute(
        f"""INSERT INTO air_pins (tenant_id, platform, uid, funnel_id, message_id, state, body, kind,
                                  next_at, pin_at, created_at, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11)
            ON CONFLICT (tenant_id, platform, uid) DO UPDATE SET {', '.join(sets)}""",
        tenant_id, platform, uid, funnel_id, mid, state, body or "", kind or "text", next_at, pin_at, now)


# ── шаг type: air_pin ────────────────────────────────────────────────────────

MAX_SKIP = "в MAX закрепа в диалоге с ботом нет – шаг пропущен (как в bot-engine)"


async def fire_air_pin(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step, job, ctx,
                       out: "Outcome") -> None:
    if u.platform == "max":
        # bot-engine max_runner: в MAX нет ни сущности времени, ни закрепа в диалоге с
        # ботом – шаг пропускается, цепочка идёт дальше, человек ничего не теряет
        out.act("air_pin_skipped", step, payload=MAX_SKIP)
        out.specs += plan_edge(g, step, EdgeKind.done, ctx)
        out.specs += plan_also(g, step, ctx)
        return
    try:
        await attach(ex, u, g, step, job, out)
    except Exception as e:  # noqa: BLE001 – закреп не встал: цепочку не рвём (как движок)
        log.exception("[%s] %s: закреп эфира не поставился", u.slug, u.uid)
        out.act("air_pin_error", step, payload=f"{type(e).__name__}: {e}")
    if out.cancel_user_everywhere:
        return
    out.specs += plan_edge(g, step, EdgeKind.done, ctx)
    out.specs += plan_also(g, step, ctx)


async def attach(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step, job,
                 out: "Outcome") -> None:
    rt = ex.rt
    if await sent_row(ex, job["id"]) is not None:
        out.act("resend_skipped", step, payload="air_pin")
        return
    entry = rt.bot_for_tenant(u.tenant_id)
    if entry is None:
        out.act("air_pin_error", step, payload="у запуска нет бота")
        return
    pc = pin_ctx(rt, u.tenant_id, g, u.position.get("webinar_at"))
    html, url, btn = render(pc.ap, pc.days, u.now, channel_link=pc.channel_link)
    if not html:
        out.act("air_pin_skipped", step, payload="нечего показывать: дат эфира нет или все прошли")
        log.info("[%s] %s: закреп эфира – показывать нечего", u.slug, u.uid)
        return
    key = state_key(pc.days, u.now, pc.ap.soon_minutes)
    nxt = next_change(pc.days, u.now, pc.ap.soon_minutes)
    kb = keyboard(url, btn)
    async with rt.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM air_pins WHERE tenant_id=$1 AND platform=$2 AND uid=$3",
                                  u.tenant_id, u.platform, u.uid)
    if row is not None:
        r, kind = await _edit_any(entry.bot, u.uid, row["message_id"], compose(html, row["body"]), kb,
                                  row["kind"])
        if r == "ok":
            async with rt.pool.acquire() as conn:
                await _remember(conn, tenant_id=u.tenant_id, platform=u.platform, uid=u.uid,
                                funnel_id=g.funnel_id, mid=row["message_id"], state=key, now=u.now,
                                next_at=nxt, body=row["body"], kind=kind)
            out.act("air_pin", step, payload=f"обновлён {key}")
            return
        # gone / retry – шлём новый
    sent_id = await begin(ex, u, g, step, job)
    try:
        msg = await entry.bot.send_message(u.uid, html, reply_markup=kb, disable_web_page_preview=True)
    except TelegramForbiddenError as e:
        await finish(ex, sent_id, ok=False, error=str(e))
        mark_blocked(u, step, out, str(e))
        return
    except Exception as e:  # noqa: BLE001
        await finish(ex, sent_id, ok=False, error=f"{type(e).__name__}: {e}")
        out.act("air_pin_error", step, payload=f"{type(e).__name__}: {e}")
        return
    await finish(ex, sent_id, ok=True, message_ids=[msg.message_id])
    delay = int(pc.ap.pin_delay_sec or 0)
    pin_at = u.now + timedelta(seconds=delay) if delay > 0 else None
    async with rt.pool.acquire() as conn:
        await _remember(conn, tenant_id=u.tenant_id, platform=u.platform, uid=u.uid,
                        funnel_id=g.funnel_id, mid=msg.message_id, state=key, now=u.now, next_at=nxt,
                        body="", kind="text", pin_at=pin_at)
    if pin_at is None:
        await _pin(entry.bot, u.uid, msg.message_id)
    out.act("air_pin", step, payload=f"поставлен {key}")
    log.info("[%s] %s: закреп эфира поставлен (%s), следующая перерисовка %s", u.slug, u.uid, key,
             nxt.astimezone(MSK).strftime("%d.%m %H:%M:%S МСК") if nxt else "–")


# ── обычный шаг с pin.air_pin ────────────────────────────────────────────────

async def adopt(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step, job,
                out: "Outcome", message_id: int | None, message_count: int) -> None:
    """Сделать закрепом уже отправленное сообщение шага. Сбой – не повод считать
    шаг несостоявшимся (как в движке)."""
    rt = ex.rt
    try:
        if step.type == StepType.video_note or not message_id:
            # кружок: дорисовывать шапку не на что – отдельным сообщением
            await _attach_separate(ex, u, g, step, out)
            return
        entry = rt.bot_for_tenant(u.tenant_id)
        pc = pin_ctx(rt, u.tenant_id, g, u.position.get("webinar_at"))
        html, url, btn = render(pc.ap, pc.days, u.now, channel_link=pc.channel_link)
        if not html or entry is None:
            out.act("air_pin_skipped", step, payload="нечего показывать")
            return
        body, _kb = ex.render(u, g, step)
        media = step.type in (StepType.photo, StepType.video, StepType.animation)
        kind = "caption" if (media and message_count == 1 and len(body or "") <= 1000) else "text"
        r, kind = await _edit_any(entry.bot, u.uid, message_id, compose(html, body), keyboard(url, btn),
                                  kind)
        key = state_key(pc.days, u.now, pc.ap.soon_minutes)
        if r != "ok":
            out.act("air_pin_error", step, payload=f"шапка не дорисовалась ({r})")
            return
        async with rt.pool.acquire() as conn:
            await _remember(conn, tenant_id=u.tenant_id, platform=u.platform, uid=u.uid,
                            funnel_id=g.funnel_id, mid=message_id, state=key, now=u.now,
                            next_at=next_change(pc.days, u.now, pc.ap.soon_minutes), body=body, kind=kind)
        await _pin(entry.bot, u.uid, message_id)
        out.act("air_pin", step, payload=f"закреп из шага {key}")
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] %s: закреп из шага %s не получился: %s", u.slug, u.uid, step.key, e)
        out.act("air_pin_error", step, payload=f"{type(e).__name__}: {e}")


async def _attach_separate(ex, u, g, step, out) -> None:
    """Закреп отдельным сообщением без привязки к задаче (для кружка)."""
    rt = ex.rt
    entry = rt.bot_for_tenant(u.tenant_id)
    pc = pin_ctx(rt, u.tenant_id, g, u.position.get("webinar_at"))
    html, url, btn = render(pc.ap, pc.days, u.now, channel_link=pc.channel_link)
    if not html or entry is None:
        out.act("air_pin_skipped", step, payload="нечего показывать")
        return
    msg = await entry.bot.send_message(u.uid, html, reply_markup=keyboard(url, btn),
                                       disable_web_page_preview=True)
    async with rt.pool.acquire() as conn:
        await _remember(conn, tenant_id=u.tenant_id, platform=u.platform, uid=u.uid,
                        funnel_id=g.funnel_id, mid=msg.message_id,
                        state=state_key(pc.days, u.now, pc.ap.soon_minutes), now=u.now,
                        next_at=next_change(pc.days, u.now, pc.ap.soon_minutes), body="", kind="text")
    await _pin(entry.bot, u.uid, msg.message_id)
    out.act("air_pin", step, payload="отдельным сообщением")


# ── смена состояний по часам движка ──────────────────────────────────────────

async def refresh_due(rt, *, limit: int = 200) -> dict:
    """Перерисовать закрепы, у которых подошло время (next_at), и закрепить
    отложенные (pin_at). Строки берутся «в аренду» (next_at + 2 мин), поэтому
    два процесса одну и ту же не возьмут."""
    now = rt.clock.now()
    stats = {"pinned": 0, "updated": 0, "gone": 0, "retry": 0}
    async with rt.pool.acquire() as conn:
        pins = await conn.fetch(
            """WITH due AS (SELECT tenant_id, platform, uid FROM air_pins WHERE pin_at <= $1
                              ORDER BY pin_at LIMIT $2 FOR UPDATE SKIP LOCKED)
               UPDATE air_pins a SET pin_at=NULL FROM due
               WHERE a.tenant_id=due.tenant_id AND a.platform=due.platform AND a.uid=due.uid
               RETURNING a.*""", now, limit)
        rows = await conn.fetch(
            """WITH due AS (SELECT tenant_id, platform, uid FROM air_pins WHERE next_at <= $1
                              ORDER BY next_at LIMIT $2 FOR UPDATE SKIP LOCKED)
               UPDATE air_pins a SET next_at=$3, tries=a.tries+1 FROM due
               WHERE a.tenant_id=due.tenant_id AND a.platform=due.platform AND a.uid=due.uid
               RETURNING a.*""", now, limit, now + timedelta(minutes=2))
    for r in pins:
        if r["platform"] != "tg":
            continue   # закреп бывает только в Telegram
        e = rt.bot_for_tenant(r["tenant_id"])
        if e is not None and await _pin(e.bot, r["uid"], r["message_id"]):
            stats["pinned"] += 1
    for r in rows:
        try:
            res = await _refresh_one(rt, r, now)
        except Exception:  # noqa: BLE001
            log.exception("закреп эфира %s не обновился", r["uid"])
            res = "retry"
        stats[res] = stats.get(res, 0) + 1
    if rows:
        log.info("закреп эфира: обновлено %s · снято %s · повторим %s", stats["updated"], stats["gone"],
                 stats["retry"])
    return stats


async def _refresh_one(rt, r, now) -> str:
    tid, platform, uid = r["tenant_id"], r["platform"], r["uid"]
    if platform != "tg":
        # закреп бывает только в Telegram; чужая строка – брак, убираем её
        async with rt.pool.acquire() as conn:
            await conn.execute("DELETE FROM air_pins WHERE tenant_id=$1 AND platform=$2 AND uid=$3",
                               tid, platform, uid)
        return "gone"
    if tid not in rt.registry.tenants:
        return "retry"
    entry = rt.bot_for_tenant(tid)
    async with rt.pool.acquire() as conn:
        pos = await conn.fetchrow(
            "SELECT * FROM positions WHERE tenant_id=$1 AND platform=$2 AND uid=$3 AND funnel_id=$4",
            tid, platform, uid, r["funnel_id"])
        part = await conn.fetchrow(
            "SELECT blocked FROM participants WHERE tenant_id=$1 AND platform=$2 AND uid=$3",
            tid, platform, uid)
    g = await rt.registry.get(tid, r["funnel_id"], pos["version"]) if pos else None
    pc = pin_ctx(rt, tid, g, pos["webinar_at"] if pos else None)
    html, url, btn = render(pc.ap, pc.days, now, channel_link=pc.channel_link)

    async def forget():
        async with rt.pool.acquire() as conn:
            await conn.execute("DELETE FROM air_pins WHERE tenant_id=$1 AND platform=$2 AND uid=$3",
                               tid, platform, uid)

    if part is None or part["blocked"]:
        await forget()
        return "gone"
    if entry is None:
        return "retry"
    if not html:
        try:
            await entry.bot.unpin_chat_message(uid, message_id=r["message_id"])
        except Exception:  # noqa: BLE001
            pass
        await forget()
        log.info("закреп эфира у %s снят – эфиры прошли", uid)
        return "gone"
    res, kind = await _edit_any(entry.bot, uid, r["message_id"], compose(html, r["body"]),
                                keyboard(url, btn), r["kind"])
    key = state_key(pc.days, now, pc.ap.soon_minutes)
    if res == "ok":
        async with rt.pool.acquire() as conn:
            await conn.execute(
                """UPDATE air_pins SET state=$4, kind=$5, next_at=$6, tries=0, updated_at=$7
                   WHERE tenant_id=$1 AND platform=$2 AND uid=$3""",
                tid, platform, uid, key, kind, next_change(pc.days, now, pc.ap.soon_minutes), now)
        return "updated"
    if res == "gone":
        await forget()
        return "gone"
    backoff = min(60 * (2 ** min(int(r["tries"]), 4)), 900)
    async with rt.pool.acquire() as conn:
        await conn.execute("UPDATE air_pins SET next_at=$4 WHERE tenant_id=$1 AND platform=$2 AND uid=$3",
                           tid, platform, uid, now + timedelta(seconds=backoff))
    return "retry"
