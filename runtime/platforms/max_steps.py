"""Что шаг воронки значит в MAX – вызывается из executor точками
`if u.platform == "max"` (остальной код исполнения общий с Telegram).

Семантика – как у движка bot-engine (max_runner.py / platforms/max/sender.py,
коммит f171a355):
  * контент: `content.max.text|buttons|claim_button_text` – None «как в TG»,
    пустое («"» / []) – «в MAX этого нет»; формулировка когорты (`variants`)
    перебивает общий текст, её `text_max` – MAX-текст шага;
    `max_video_note_caption: false` гасит подпись кружков, у которых нет
    своего MAX-текста;
  * `{channel.link}` → `channel.max_link`, `{stream_url}` →
    `webinar.stream_url_max` (render_text с platform="max");
  * кнопки: ссылки как в TG; callback-кнопка – ТОЛЬКО из `content.max.buttons`
    (в зонтичном боте нажатие раскладывается по слагу в payload, §36 –
    телеграмный payload без слага нажимался бы в пустоту); claim-кнопка шага –
    `f:<slug>:c:<id шага>`;
  * запрос телефона: без `manual_phone_max` – шаг пропускается
    (`on_skip_if_phone_known` / `on_skip` / `on_contact`, задержка «телефон
    известен»); с `manual_phone_max` – кнопка «поделиться контактом» и
    ожидание, как в TG (номер текстом тоже принимается);
  * гейт канала – участники канала MAX (`channel.max_chat_id`), бот должен
    быть админом; проверить нельзя – fail-open, как у TG в Студии.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from studio.model.funnel import EdgeKind, Step, StepType

from .. import repo
from ..clock import MSK
from ..engine_lib.branch import branch_by_id, shift_for_step
from ..engine_lib.render import render_text
from ..graph import CompiledFunnel, cohort_matches
from ..planner import plan, plan_also
from ..repo import JobSpec, Outcome

if TYPE_CHECKING:
    from ..executor import Executor, UserCtx

log = logging.getLogger("runtime.max.steps")

PHONE_WAIT_DEFAULT_S = 300   # bot-engine: ожидание номера в MAX по умолчанию 5 мин


def claim_payload(slug: str, step: Step) -> str:
    """Callback claim-кнопки в MAX: со слагом запуска (зонтичный бот, §36)."""
    return f"f:{slug}:c:{step.id.hex}"


def content(u: "UserCtx", g: CompiledFunnel, step: Step):
    """(text, buttons, claim, max_buttons_explicit) для MAX."""
    tg, mx = step.content.tg, step.content.max
    text, buttons, claim = tg.text, list(tg.buttons), tg.claim_button_text
    text_max = mx.text
    if text_max is None and step.type == StepType.video_note and not g.funnel.max_video_note_caption:
        text_max = ""   # кружок: реплика уже в ролике, подпись продублировала бы её (§25)
    if step.content.variants:
        ctx = {k: (u.participant.get(k) or "") for k in
               ("utm_source", "utm_medium", "utm_campaign", "trigger")}
        for v in step.content.variants:
            if cohort_matches(v.when, ctx, "max"):
                text = v.text if v.text is not None else text
                text_max = v.text_max if v.text_max is not None else text_max
                buttons = list(v.buttons) if v.buttons is not None else buttons
                claim = v.claim_button_text if v.claim_button_text is not None else claim
                break
    explicit = mx.buttons is not None
    return (text if text_max is None else text_max,
            buttons if mx.buttons is None else list(mx.buttons),
            claim if mx.claim_button_text is None else mx.claim_button_text,
            explicit)


def render(ex: "Executor", u: "UserCtx", g: CompiledFunnel, step: Step, *,
           text_override: str | None = None, contact_kb: bool = False):
    """→ (html-текст, ряды кнопок MAX)."""
    from ..executor import _URL_OK
    tenant = ex.rt.registry.tenant(u.tenant_id)
    cfg = ex.render_cfg(tenant, g)
    text, buttons, claim, explicit = content(u, g, step)
    if text_override is not None:
        text = text_override
    wat = u.position.get("webinar_at")
    branch = branch_by_id(g.router, u.position.get("branch"))
    wdt = wat.astimezone(MSK) if wat else None
    p = u.participant
    utm = {k: p.get(k) for k in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")}
    kw = dict(webinar_dt=shift_for_step(wdt, step.schedule, branch),
              webinar_main_dt=shift_for_step(wdt, step.schedule, branch, main=True),
              max_id=u.uid, utm=utm, step_key=step.key)
    name = p.get("first_name")
    rendered = render_text(text or "", cfg, name, "max", **kw)
    rows: list[list[dict]] = []
    if contact_kb:
        rows.append([{"type": "request_contact", "text": step.contact.contact_button}])
        return rendered, rows
    if claim and g.edge(step.id, EdgeKind.callback) is not None:
        rows.append([{"type": "callback", "text": render_text(claim, cfg, name, "max", **kw),
                      "payload": claim_payload(u.slug, step)}])
    for b in buttons:
        label = render_text(b.text, cfg, name, "max", **kw)
        if b.url:
            url = render_text(b.url, cfg, None, "max", **kw).strip()
            if not _URL_OK.match(url):
                log.warning("[%s] шаг %s: кнопка «%s» без рабочей ссылки (%r) – в MAX не показываем",
                            u.slug, step.key, b.text, url)
                continue
            # web_app в MAX нет – обычная ссылка
            rows.append([{"type": "link", "text": label, "url": url}])
        elif b.callback_data and explicit:
            rows.append([{"type": "callback", "text": label, "payload": b.callback_data[:128]}])
    return rendered, rows


async def send_step(ex: "Executor", u: "UserCtx", g: CompiledFunnel, step: Step, job, out: Outcome, *,
                    text_override: str | None = None, contact_kb: bool = False) -> bool:
    """MAX-версия Executor._send. → True, если человек заблокировал бота."""
    rt = ex.rt
    tenant = rt.registry.tenant(u.tenant_id)
    async with rt.pool.acquire() as conn:
        prev = await repo.sent_for_job(conn, job["id"])
    if prev is not None:
        log.warning("[%s] MAX %s: задача %s повторно – отправка %s уже была (%s), не дублируем",
                    u.slug, u.uid, job["id"], step.key, prev["status"])
        out.act("resend_skipped", step, payload=prev["status"])
        return False
    bot = rt.max_hub.bot_for_tenant(u.tenant_id) if rt.max_hub else None
    if bot is None:
        out.act("send_error", step, payload="у запуска нет MAX-бота")
        log.error("[%s] у запуска нет подключённого MAX-бота – шаг %s не отправлен", u.slug, step.key)
        return False
    text, rows = render(ex, u, g, step, text_override=text_override, contact_kb=contact_kb)
    # кнопки-ссылки → прокси движка /go (учёт кликов для гейтов, §26: текст – нет)
    from ..gates.links import wrap_max_rows
    rows = await wrap_max_rows(rt, tenant, u.uid, step, rows)
    async with rt.pool.acquire() as conn:
        sent_id = await repo.sent_begin(conn, tenant_id=u.tenant_id, platform=u.platform, uid=u.uid,
                                        funnel_id=g.funnel_id, version=g.version, step=step,
                                        job_id=job["id"], now=u.now)
    res = await rt.max_sender.send(bot.api, bot.bot_key, u.uid, step, text, rows,
                                   static_launch=tenant.settings.static_launch
                                   or (g.funnel.source.launch if g.funnel.source else ""))
    async with rt.pool.acquire() as conn:
        await repo.sent_finish(conn, sent_id, ok=res.ok, message_ids=[],
                               error=res.error or res.note, now=rt.clock.now(),
                               ext_message_ids=res.ext_ids or None)
    if res.blocked:
        log.info("[%s] MAX %s заблокировал бота – задачи человека отменены", u.slug, u.uid)
        out.act("blocked", step, payload=res.error)
        out.participant.update(blocked=True, blocked_at=u.now)
        out.cancel_user_everywhere = "человек заблокировал бота"
        return True
    if not res.ok:
        log.warning("[%s] MAX %s: шаг %s не отправлен (%s) – цепочка продолжается",
                    u.slug, u.uid, step.key, res.error)
        out.act("send_error", step, payload=res.error)
    elif not res.ext_ids:
        from ..executor import empty_step
        empty_step(out, u, step, res.note)
    else:
        log.info("[%s] MAX %s: ушёл шаг %s (%s) %s", u.slug, u.uid, step.key,
                 ",".join(res.ext_ids) or "-", res.note or "")
        if step.pin.pin:
            # закрепа в диалоге с ботом у MAX нет (bot-engine тоже не закрепляет)
            out.act("pin_skipped", step, payload="в MAX закрепа в диалоге с ботом нет")
    return False


async def edit(ex: "Executor", u: "UserCtx", mid: str, html: str, rows: list[list[dict]] | None, *,
               keep_attachments: bool = False) -> str:
    """Правка своего сообщения в MAX (`PUT /messages`). → "ok" / "blocked" / "fail".

    rows=None – клавиатуру убрать. ⚠️ Вложения MAX заменяются целиком, поэтому
    сообщение с картинкой/видео так править нельзя (картинка пропала бы) –
    `keep_attachments=True` правит только текст, не трогая вложений."""
    bot = ex.rt.max_hub.bot_for_tenant(u.tenant_id) if ex.rt.max_hub else None
    if bot is None or not mid:
        return "fail"
    from .max_html import html_to_max
    from .max_sender import keyboard
    kb = keyboard(rows or [])
    try:
        await bot.api.edit_message(mid, text=html_to_max(html or ""),
                                   attachments=None if keep_attachments else ([kb] if kb else []))
        return "ok"
    except Exception as e:  # noqa: BLE001
        from .max_api import is_block_error
        if is_block_error(e):
            return "blocked"
        log.info("[%s] MAX %s: правка сообщения %s не удалась (%s)", u.slug, u.uid, mid, e)
        return "fail"


async def phone_request(ex: "Executor", u: "UserCtx", g: CompiledFunnel, step: Step, job, ctx,
                        out: Outcome) -> None:
    cs = step.contact
    known = bool((u.participant.get("phone") or "").strip())
    known_edge = g.edge(step.id, EdgeKind.skip_if_phone_known)
    known_delay = (known_edge.delay_seconds if known_edge else 0) or 0

    def go_on(reason: str) -> None:
        e = known_edge or g.edge(step.id, EdgeKind.skip) or g.edge(step.id, EdgeKind.contact)
        if e is not None:
            out.specs.extend(plan(g, e.to_step, known_delay, ctx, reason=reason))
        out.specs.extend(plan_also(g, step, ctx))

    if not cs.manual_phone_max or (cs.skip_if_known and known):
        # в MAX номер не спрашиваем (или он уже есть) – шаг пропускается
        if cs.manual_phone_max:
            if cs.text_if_known and await send_step(ex, u, g, step, job, out,
                                                    text_override=cs.text_if_known):
                return
            e = known_edge or g.edge(step.id, EdgeKind.contact) or g.edge(step.id, EdgeKind.skip)
            if e is not None:
                out.specs.extend(plan(g, e.to_step, known_delay, ctx,
                                      reason=f"MAX: телефон известен ({step.key})"))
            out.specs.extend(plan_also(g, step, ctx))
            out.act("phone_known", step)
            return
        if cs.always_send_text:
            if await send_step(ex, u, g, step, job, out, text_override=cs.text_if_known or None):
                return
        out.act("phone_skipped_max", step)
        log.info("[%s] MAX %s: запрос телефона %s в MAX пропускается", u.slug, u.uid, step.key)
        go_on(f"MAX: запрос телефона {step.key} пропущен")
        return

    # manual_phone_max: кнопка «поделиться контактом» + ожидание (номер текстом тоже годится)
    if await send_step(ex, u, g, step, job, out, text_override=cs.manual_phone_max_text or None,
                       contact_kb=True):
        return
    out.awaiting = {"kind": "contact", "step_id": str(step.id), "version": g.version}
    skip = g.edge(step.id, EdgeKind.skip)
    wait = skip.delay_seconds if (skip is not None and skip.delay_seconds is not None) \
        else PHONE_WAIT_DEFAULT_S
    if wait:
        out.specs.append(JobSpec(step_id=step.id, step_key=step.key, kind="skip",
                                 run_at=u.now + timedelta(seconds=wait),
                                 reason=f"MAX: ждём телефон {wait} с"))
    out.specs.extend(plan_also(g, step, ctx))


async def channel_state(ex: "Executor", u: "UserCtx") -> bool | None:
    s = ex.rt.registry.tenant(u.tenant_id).settings.channel
    chat_id = int(getattr(s, "max_chat_id", 0) or 0)
    if not chat_id:
        return None
    bot = ex.rt.max_hub.bot_for_tenant(u.tenant_id) if ex.rt.max_hub else None
    if bot is None:
        return None
    return await bot.api.is_chat_member(chat_id, u.uid)

