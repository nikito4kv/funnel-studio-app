"""Исполнение шага воронки – перенос `FunnelRunner._fire_step_impl` движка bot-engine.

Порядок тот же, что в движке (runner.py ~410–719): неизвестный шаг → анти-дубль
90 с → лог «step» → jump / телефон / почта → гейты → отправка → переход по
on_done → also_fire.

ГЛАВНОЕ ПРАВИЛО (инцидент 15.05 и 22.09): что бы ни случилось внутри шага –
ошибка отправки, гейта, чего угодно – on_done всё равно планируется. Один
кривой шаг = пропуск этого шага, но не смерть воронки. Исключение – человек
заблокировал бота: тогда он помечается, а его задачи отменяются.

Отличие от движка в устройстве, а не в поведении: шаг не планирует следующий
«в конце своей работы». Итог шага (Outcome) – следующие задачи, сдвиг
позиции, события – применяется ОДНОЙ транзакцией вместе с пометкой задачи
выполненной. Упал процесс до коммита – задача вернётся после аренды и
выполнится снова, а запись в sent_messages не даст отправить сообщение
второй раз.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo,
)

from studio.model.funnel import EdgeKind, Step, StepType

from . import repo, steps
from .clock import MSK
from .engine_lib.branch import branch_by_id, shift_for_step
from .engine_lib.render import render_text
from .gates.evaluate import evaluate as gates_evaluate
from .gates.links import wrap_message
from .graph import SENDABLE, CompiledFunnel, cohort_matches, unsupported_reason
from .planner import PlanCtx, plan, plan_also, plan_edge
from .repo import JobSpec, Outcome
from .settings import RenderCfg

if TYPE_CHECKING:
    from .service import Runtime

log = logging.getLogger("runtime.executor")

DEDUP_WINDOW_S = 90          # _DEDUP_WINDOW_S движка
# Задача «повторный /start – только первое сообщение ещё раз» (resend_entry_on_repeat):
# мимо анти-дубля, без переходов и позиции – как send_step движка в обход fire_step.
RESEND_ENTRY = "повторный /start – первое сообщение ещё раз (resend_entry_on_repeat)"
EMAIL_WAIT_DEFAULT_S = 300   # движок: on_skip_after_seconds у почты по умолчанию
_URL_OK = re.compile(r"^(https?://|tg://)\S+$")
_CHANNEL_MEMBER = ("member", "administrator", "creator", "restricted")


def claim_callback_data(step: Step) -> str:
    return f"c:{step.id.hex}"


@dataclass
class UserCtx:
    tenant_id: int
    slug: str
    platform: str
    uid: int
    participant: dict
    position: dict
    now: datetime

    def plan_ctx(self, dnd) -> PlanCtx:
        return PlanCtx(now=self.now, webinar_at=self.position.get("webinar_at"),
                       entered_at=self.participant["created_at"],
                       branch_id=self.position.get("branch"), dnd=dnd)


class Executor:
    def __init__(self, rt: "Runtime"):
        self.rt = rt

    # ── вход из воркера ──────────────────────────────────────────────────
    async def execute_job(self, job) -> str:
        """Выполняет задачу и применяет итог. → итоговый статус задачи."""
        rt = self.rt
        now = rt.clock.now()
        tenant = rt.registry.tenants.get(job["tenant_id"])
        g = await rt.registry.get(job["tenant_id"], job["funnel_id"], job["version"])
        async with rt.pool.acquire() as conn:
            part = await repo.get_participant(conn, job["tenant_id"], job["platform"], job["uid"])
            pos = await repo.get_position(conn, job["tenant_id"], job["platform"], job["uid"],
                                          job["funnel_id"])
        out = Outcome()
        if tenant is None or g is None:
            out.job_status, out.job_reason = "failed", "нет запуска или версии воронки"
            log.error("задача %s: %s", job["id"], out.job_reason)
        elif part is None or pos is None:
            out.job_status, out.job_reason = "cancelled", "человека нет в базе"
        elif part["blocked"] or pos["status"] == "stopped":
            out.job_status, out.job_reason = "cancelled", "человек заблокировал бота / остановлен"
        else:
            u = UserCtx(tenant_id=tenant.id, slug=tenant.slug, platform=job["platform"],
                        uid=job["uid"], participant=dict(part), position=dict(pos), now=now)
            try:
                if job["kind"] == "skip":
                    await self._on_skip(u, g, job, out)
                else:
                    await self._fire(u, g, job, out)
            except Exception as e:  # noqa: BLE001 – главное правило: цепочку спасаем
                log.exception("[%s] шаг %s упал у %s – всё равно планируем on_done",
                              tenant.slug, job["step_key"], job["uid"])
                out = self._rescue(u, g, job, e)
        await self._finalize(job, out, now)
        return out.job_status

    def _rescue(self, u: UserCtx, g: CompiledFunnel, job, err: Exception) -> Outcome:
        out = Outcome()
        step = g.step(job["step_id"])
        out.act("step_error", step, payload=f"{type(err).__name__}: {err}")
        if step is not None and job["kind"] != "skip":
            dnd = self.rt.registry.tenant(u.tenant_id).settings.dnd
            out.specs += plan_edge(g, step, EdgeKind.done, u.plan_ctx(dnd),
                                   reason=f"спасение цепочки после ошибки в {step.key}")
        elif step is not None:
            out.specs += plan_edge(g, step, EdgeKind.skip, u.plan_ctx(
                self.rt.registry.tenant(u.tenant_id).settings.dnd), delay_override=0)
        return out

    async def _finalize(self, job, out: Outcome, now: datetime) -> None:
        async with self.rt.pool.acquire() as conn:
            async with conn.transaction():
                # порядок блокировок везде один: человек → позиция → задачи
                # (иначе событие от человека и финал шага могли бы взаимно ждать)
                await conn.execute(
                    "SELECT 1 FROM participants WHERE tenant_id=$1 AND platform=$2 AND uid=$3 FOR UPDATE",
                    job["tenant_id"], job["platform"], job["uid"])
                await conn.execute(
                    """SELECT 1 FROM positions WHERE tenant_id=$1 AND platform=$2 AND uid=$3
                       AND funnel_id=$4 FOR UPDATE""",
                    job["tenant_id"], job["platform"], job["uid"], job["funnel_id"])
                r = await conn.execute(
                    """UPDATE jobs SET status=$2, reason=CASE WHEN $3 = '' THEN reason ELSE $3 END,
                              finished_at=$4, updated_at=$4, lease_until=NULL
                       WHERE id=$1 AND status='running' AND locked_by=$5""",
                    job["id"], out.job_status, out.job_reason, now, job["locked_by"])
                if r.endswith(" 0"):
                    # аренду перехватил другой процесс – его итог главнее
                    raise LeaseLost(job["id"])
                await repo.apply_outcome(
                    conn, tenant_id=job["tenant_id"], platform=job["platform"], uid=job["uid"],
                    funnel_id=job["funnel_id"], version=job["version"], out=out, now=now,
                    idem_prefix=f"j{job['id']}", job_id=job["id"])

    # ── шаг ──────────────────────────────────────────────────────────────
    async def _fire(self, u: UserCtx, g: CompiledFunnel, job, out: Outcome) -> None:
        rt = self.rt
        tenant = rt.registry.tenant(u.tenant_id)
        ctx = u.plan_ctx(tenant.settings.dnd)
        step = g.step(job["step_id"])
        if step is None:
            # движок: «Unknown step» → return. Публикация не пропускает удаление
            # шага с ожидающими задачами, так что сюда попадает только брак данных.
            log.error("[%s] задача %s ведёт в шаг %s, которого нет в v%s", u.slug, job["id"],
                      job["step_key"], g.version)
            out.act("unknown_step", payload=f"{job['step_key']} v{g.version}")
            return

        if job["reason"] == RESEND_ENTRY:
            await self._resend_entry(u, g, step, job, out)
            return

        # анти-дубль: тот же шаг этому человеку только что выполнялся другой задачей
        async with rt.pool.acquire() as conn:
            dup = await repo.recently_fired(
                conn, tenant_id=u.tenant_id, platform=u.platform, uid=u.uid, funnel_id=g.funnel_id,
                step_id=step.id, since=u.now - timedelta(seconds=DEDUP_WINDOW_S), except_job=job["id"])
        if dup:
            log.info("[%s] %s: шаг %s DUP (<%s с) – не шлём повторно", u.slug, u.uid, step.key,
                     DEDUP_WINDOW_S)
            out.act("dup_skip", step, payload=f"этот шаг уже уходил человеку меньше {DEDUP_WINDOW_S} с "
                                              "назад – второй раз не шлём (анти-дубль)")
            await self._recover_tail(u, g, step, job, ctx, out)
            return

        out.act("step", step)
        out.position_step = (step.id, step.key)

        if step.type == StepType.jump:
            out.specs += plan_edge(g, step, EdgeKind.jump, ctx, delay_override=0)
            return
        if step.type == StepType.phone_request:
            if u.platform == "max":   # MAX: свой запрос номера (runtime/platforms/max_steps.py)
                from .platforms import max_steps
                await max_steps.phone_request(self, u, g, step, job, ctx, out)
                return
            await self._phone_request(u, g, step, job, ctx, out)
            return
        if step.type == StepType.email_request:
            await self._email_request(u, g, step, job, ctx, out)
            return

        skip = await self._gates(u, step, out, g)
        if skip:
            log.info("[%s] %s: шаг %s пропущен гейтом (%s)", u.slug, u.uid, step.key, skip)
            out.act("gated_out", step, payload=skip)
            out.specs += plan_edge(g, step, EdgeKind.done, ctx)
            return

        # опрос, звонок, SMS, закреп эфира, статья, правка на месте – runtime/steps
        if await steps.fire(self, u, g, step, job, ctx, out):
            return

        why = unsupported_reason(step, g)
        if why:
            log.info("[%s] %s: шаг %s – %s, не поддерживается в фундаменте, идём дальше",
                     u.slug, u.uid, step.key, why)
            out.act("unsupported", step, payload=why)
            nxt = g.edge(step.id, EdgeKind.done) or g.edge(step.id, EdgeKind.survey_next)
            if nxt is not None:
                out.specs += plan(g, nxt.to_step, nxt.delay_seconds or 0, ctx,
                                  reason=f"после неподдержанного {step.key}")
            if not step.survey.qid:
                out.specs += plan_also(g, step, ctx)
            return

        blocked = await self._send(u, g, step, job, out)
        if blocked:
            return
        # закреп эфира из шага, переход в воронку (важнее on_done) или on_done, also_fire
        await steps.after_send(self, u, g, step, job, ctx, out)

    async def _resend_entry(self, u: UserCtx, g: CompiledFunnel, step: Step, job, out: Outcome) -> None:
        """Повторный /start в окне анти-дубля при `resend_entry_on_repeat`: только само сообщение.
        Таймеры, позиция, on_done и also_fire не трогаются (runner.start_for_tg движка)."""
        if step.type not in SENDABLE:
            out.act("entry_resend_skipped", step,
                    payload=f"первый шаг – {step.type.value}: повторно шлём только сообщения")
            return
        if await self._send(u, g, step, job, out):
            return
        out.act("entry_resent", step, payload="повторный /start – первое сообщение ещё раз")
        log.info("[%s] %s: повторный /start – шаг %s отправлен ещё раз, таймеры не тронуты",
                 u.slug, u.uid, step.key)

    async def _recover_tail(self, u, g, step, job, ctx, out) -> None:
        """DUP-восстановление (`_reschedule_chain_tail` движка): идём по on_done
        и ставим первый шаг, который не выполнялся и чьей задачи нет."""
        rt = self.rt
        since = u.now - timedelta(seconds=DEDUP_WINDOW_S)

        async with rt.pool.acquire() as conn:
            async def lost(sid) -> bool:
                if await repo.has_pending(conn, tenant_id=u.tenant_id, platform=u.platform,
                                          uid=u.uid, funnel_id=g.funnel_id, step_id=sid,
                                          slot="fire", except_job=job["id"]):
                    return False
                return not await repo.recently_fired(
                    conn, tenant_id=u.tenant_id, platform=u.platform, uid=u.uid,
                    funnel_id=g.funnel_id, step_id=sid, since=since, except_job=job["id"])

            for e in g.edges(step.id, EdgeKind.also_fire):
                if await lost(e.to_step):
                    out.specs += plan(g, e.to_step, e.delay_seconds or 0, ctx, kind="also",
                                      reason="восстановление после DUP")
            edge = g.edge(step.id, EdgeKind.done)
            for _ in range(8):
                if edge is None or edge.to_step is None:
                    return
                if await repo.has_pending(conn, tenant_id=u.tenant_id, platform=u.platform,
                                          uid=u.uid, funnel_id=g.funnel_id, step_id=edge.to_step,
                                          slot="fire", except_job=job["id"]):
                    return   # цепочка жива
                if await lost(edge.to_step):
                    out.specs += plan(g, edge.to_step, edge.delay_seconds or 0, ctx,
                                      reason="восстановление хвоста после DUP")
                    return
                nxt = g.step(edge.to_step)
                if nxt is None:
                    return
                for e in g.edges(nxt.id, EdgeKind.also_fire):
                    if await lost(e.to_step):
                        out.specs += plan(g, e.to_step, e.delay_seconds or 0, ctx, kind="also",
                                          reason="восстановление после DUP")
                edge = g.edge(nxt.id, EdgeKind.done)

    # ── телефон и почта ──────────────────────────────────────────────────
    async def _phone_request(self, u, g, step, job, ctx, out) -> None:
        cs = step.contact
        if cs.skip_if_known and (u.participant.get("phone") or "").strip():
            if cs.always_send_text:
                if await self._send(u, g, step, job, out, text_override=cs.text_if_known or None,
                                    contact_kb=False):
                    return
            log.info("[%s] %s: телефон известен – %s пропускаем", u.slug, u.uid, step.key)
            out.act("phone_known", step)
            out.specs += plan_edge(g, step, EdgeKind.skip_if_phone_known, ctx)
            return
        if await self._send(u, g, step, job, out, contact_kb=True):
            return
        out.awaiting = {"kind": "contact", "step_id": str(step.id), "version": g.version}
        skip = g.edge(step.id, EdgeKind.skip)
        if skip is not None and skip.delay_seconds:
            out.specs.append(JobSpec(step_id=step.id, step_key=step.key, kind="skip",
                                     run_at=u.now + timedelta(seconds=skip.delay_seconds),
                                     reason=f"ждём телефон {skip.delay_seconds} с"))
        out.specs += plan_also(g, step, ctx)

    async def _email_request(self, u, g, step, job, ctx, out) -> None:
        es = step.email
        if es.skip_if_known and (u.participant.get("email") or "").strip():
            if es.text_if_known:
                if await self._send(u, g, step, job, out, text_override=es.text_if_known):
                    return
            e = (g.edge(step.id, EdgeKind.skip_if_email_known) or g.edge(step.id, EdgeKind.email)
                 or g.edge(step.id, EdgeKind.skip))
            known = g.edge(step.id, EdgeKind.skip_if_email_known)
            out.act("email_known", step)
            if e is not None:
                out.specs += plan(g, e.to_step, (known.delay_seconds if known else 0) or 0, ctx,
                                  reason=f"почта известна ({step.key})")
            return
        if await self._send(u, g, step, job, out):
            return
        out.awaiting = {"kind": "email", "step_id": str(step.id), "version": g.version}
        skip = g.edge(step.id, EdgeKind.skip)
        wait = skip.delay_seconds if (skip is not None and skip.delay_seconds is not None) \
            else EMAIL_WAIT_DEFAULT_S
        if skip is not None and wait:
            out.specs.append(JobSpec(step_id=step.id, step_key=step.key, kind="skip",
                                     run_at=u.now + timedelta(seconds=wait),
                                     reason=f"ждём почту {wait} с"))
        out.specs += plan_also(g, step, ctx)

    async def _on_skip(self, u, g, job, out) -> None:
        """Человек не ответил за отведённое время (телефон / почта)."""
        step = g.step(job["step_id"])
        if step is None:
            out.act("unknown_step", payload=job["step_key"])
            return
        ctx = u.plan_ctx(self.rt.registry.tenant(u.tenant_id).settings.dnd)
        if step.type == StepType.phone_request and not (u.participant.get("phone") or "").strip():
            out.participant["phone_auto_skipped"] = True
        aw = u.position.get("awaiting") or {}
        if aw.get("step_id") == str(step.id):
            out.awaiting = None
        out.act("skip_timeout", step)
        log.info("[%s] %s: не ответил на %s – идём по «не дал»", u.slug, u.uid, step.key)
        out.specs += plan_edge(g, step, EdgeKind.skip, ctx, delay_override=0)

    # ── гейты ────────────────────────────────────────────────────────────
    async def _gates(self, u: UserCtx, step: Step, out: Outcome, g: CompiledFunnel) -> str | None:
        """→ причина пропуска шага или None (шлём).

        Оплата / эфир / клики / прочее – источники данных runtime/gates:
        положительный гейт без данных – fail-safe (пропуск), дожим – fail-open.
        """
        c = step.conditions
        skip = await gates_evaluate(self.rt, u, step, g, out)
        if skip:
            return skip
        if c.channel:
            state = await self._channel_state(u)
            if state is None:
                out.act("gate_unchecked", step, payload="channel")
                log.info("[%s] %s: шаг %s – подписку на канал проверить нельзя, fail-open",
                         u.slug, u.uid, step.key)
            elif c.channel == "not_joined" and state is True:
                return "уже в канале"
            elif c.channel == "joined" and state is False:
                return "не в канале"
        return None

    async def _channel_state(self, u: UserCtx) -> bool | None:
        if u.platform == "max":   # канал MAX – участники канала через API MAX
            from .platforms import max_steps
            return await max_steps.channel_state(self, u)
        chat_id = self.rt.registry.tenant(u.tenant_id).settings.channel.chat_id
        if not chat_id:
            return None
        entry = self.rt.bot_for_tenant(u.tenant_id)
        if entry is None:
            return None
        try:
            m = await entry.bot.get_chat_member(chat_id, u.uid)
            return getattr(m, "status", None) in _CHANNEL_MEMBER
        except Exception as e:  # noqa: BLE001 – бот не админ / канала нет
            log.warning("[%s] getChatMember(%s, %s) не удался: %s", u.slug, chat_id, u.uid, e)
            return None

    # ── отправка ─────────────────────────────────────────────────────────
    def render_cfg(self, tenant, g: CompiledFunnel) -> RenderCfg:
        room = g.funnel.webinar_room
        return RenderCfg(tenant.slug, tenant.settings,
                         stream_url_override=(room.stream_url if room and room.stream_url else ""))

    def _content(self, u: UserCtx, step: Step):
        """(text, buttons, claim) с учётом формулировок когорты (variants)."""
        tg = step.content.tg
        text, buttons, claim = tg.text, list(tg.buttons), tg.claim_button_text
        if step.content.variants:
            ctx = {k: (u.participant.get(k) or "") for k in
                   ("utm_source", "utm_medium", "utm_campaign", "trigger")}
            for v in step.content.variants:
                if cohort_matches(v.when, ctx, u.platform):
                    text = v.text if v.text is not None else text
                    buttons = list(v.buttons) if v.buttons is not None else buttons
                    claim = v.claim_button_text if v.claim_button_text is not None else claim
                    break
        return text, buttons, claim

    def render(self, u: UserCtx, g: CompiledFunnel, step: Step, *, text_override: str | None = None,
               contact_kb: bool = False):
        tenant = self.rt.registry.tenant(u.tenant_id)
        cfg = self.render_cfg(tenant, g)
        text, buttons, claim = self._content(u, step)
        if text_override is not None:
            text = text_override
        wat = u.position.get("webinar_at")
        branch = branch_by_id(g.router, u.position.get("branch"))
        wdt = wat.astimezone(MSK) if wat else None
        step_dt = shift_for_step(wdt, step.schedule, branch)
        main_dt = shift_for_step(wdt, step.schedule, branch, main=True)
        p = u.participant
        utm = {k: p.get(k) for k in ("utm_source", "utm_medium", "utm_campaign", "utm_content",
                                     "utm_term")}
        kw = dict(webinar_dt=step_dt, webinar_main_dt=main_dt, tg_id=u.uid, utm=utm, step_key=step.key)
        rendered = render_text(text or "", cfg, p.get("first_name"), "tg", **kw)
        if contact_kb:
            kb = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=step.contact.contact_button, request_contact=True)]],
                resize_keyboard=True, one_time_keyboard=True)
            return rendered, kb
        rows = []
        if claim and g.edge(step.id, EdgeKind.callback) is not None:
            rows.append([InlineKeyboardButton(
                text=render_text(claim, cfg, p.get("first_name"), "tg", **kw),
                callback_data=claim_callback_data(step))])
        for b in buttons:
            label = render_text(b.text, cfg, p.get("first_name"), "tg", **kw)
            if b.url:
                url = render_text(b.url, cfg, None, "tg", **kw).strip()
                if not _URL_OK.match(url) or (b.web_app and not url.startswith("https://")):
                    log.warning("[%s] шаг %s: кнопка «%s» без рабочей ссылки (%r) – не показываем",
                                u.slug, step.key, b.text, url)
                    continue
                if b.web_app:
                    rows.append([InlineKeyboardButton(text=label, web_app=WebAppInfo(url=url))])
                else:
                    rows.append([InlineKeyboardButton(text=label, url=url)])
            elif b.callback_data:
                rows.append([InlineKeyboardButton(text=label, callback_data=b.callback_data[:64])])
        return rendered, (InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)

    async def _send(self, u: UserCtx, g: CompiledFunnel, step: Step, job, out: Outcome, *,
                    text_override: str | None = None, contact_kb: bool = False) -> bool:
        """Отправка с идемпотентностью по задаче. → True, если человек заблокировал бота."""
        if u.platform == "max":   # MAX: рендер и отправка – runtime/platforms/max_steps.py
            from .platforms import max_steps
            return await max_steps.send_step(self, u, g, step, job, out,
                                             text_override=text_override, contact_kb=contact_kb)
        rt = self.rt
        tenant = rt.registry.tenant(u.tenant_id)
        async with rt.pool.acquire() as conn:
            prev = await repo.sent_for_job(conn, job["id"])
        if prev is not None:
            # задача выполняется повторно (процесс упал после отправки): не шлём второй раз
            log.warning("[%s] %s: задача %s повторно – отправка %s уже была (%s), не дублируем",
                        u.slug, u.uid, job["id"], step.key, prev["status"])
            out.act("resend_skipped", step, payload=prev["status"])
            return False
        entry = rt.bot_for_tenant(u.tenant_id)
        if entry is None:
            out.act("send_error", step, payload="у запуска нет бота")
            log.error("[%s] у запуска нет подключённого бота – шаг %s не отправлен", u.slug, step.key)
            return False
        text, kb = self.render(u, g, step, text_override=text_override, contact_kb=contact_kb)
        # ссылки → прокси движка /go (учёт кликов для гейтов); ссылки не включены – как было
        text, kb = await wrap_message(rt, tenant, u.platform, u.uid, step, text, kb)
        pin_first = False
        async with rt.pool.acquire() as conn:
            if step.pin.pin:
                pin_first = not await repo.was_sent_before(
                    conn, tenant_id=u.tenant_id, platform=u.platform, uid=u.uid,
                    funnel_id=g.funnel_id, step_id=step.id)
            sent_id = await repo.sent_begin(conn, tenant_id=u.tenant_id, platform=u.platform,
                                            uid=u.uid, funnel_id=g.funnel_id, version=g.version,
                                            step=step, job_id=job["id"], now=u.now)
        res = await rt.sender.send(entry.bot, entry.tg_bot_id, u.uid, step, text, kb,
                                   static_launch=tenant.settings.static_launch
                                   or (g.funnel.source.launch if g.funnel.source else ""))
        async with rt.pool.acquire() as conn:
            await repo.sent_finish(conn, sent_id, ok=res.ok, message_ids=res.message_ids,
                                   error=res.error or res.note, now=rt.clock.now())
        if res.blocked:
            log.info("[%s] %s заблокировал бота – задачи человека отменены", u.slug, u.uid)
            out.act("blocked", step, payload=res.error)
            out.participant.update(blocked=True, blocked_at=u.now)
            out.cancel_user_everywhere = "человек заблокировал бота"
            return True
        if not res.ok:
            log.warning("[%s] %s: шаг %s не отправлен (%s) – цепочка продолжается",
                        u.slug, u.uid, step.key, res.error)
            out.act("send_error", step, payload=res.error)
        elif not res.message_ids:
            empty_step(out, u, step, res.note)
        else:
            log.info("[%s] %s: ушёл шаг %s (%s) %s", u.slug, u.uid, step.key,
                     ",".join(map(str, res.message_ids)) or "-", res.note or "")
            if pin_first and res.first_message is not None:
                try:
                    await entry.bot.pin_chat_message(u.uid, res.first_message.message_id,
                                                     disable_notification=True)
                except Exception as e:  # noqa: BLE001 – не закрепилось – шаг всё равно состоялся
                    log.debug("pin failed: %s", e)
        return False


def empty_step(out: Outcome, u: "UserCtx", step: Step, note: str | None) -> None:
    """Шагу нечего отправлять (нет ни текста, ни картинки, ни ролика): человеку не ушло
    ничего. Не тихий «step», а заметная запись – цепочка при этом идёт дальше."""
    why = note or "нечего отправлять"
    log.warning("[%s] %s %s: шаг %s пустой – человеку ничего не ушло (%s), цепочка идёт дальше",
                u.slug, u.platform, u.uid, step.key, why)
    out.act("empty_step", step, payload=f"шаг пустой – ничего не отправлено ({why})")


class LeaseLost(RuntimeError):
    """Задачу, пока мы её делали, забрал другой процесс (аренда истекла)."""

