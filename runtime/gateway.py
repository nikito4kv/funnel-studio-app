"""События от людей: /start, контакт, нажатие кнопки, текст, блокировка бота.

Здесь только решения «что поставить в очередь»; сами шаги выполняет воркер.
Все изменения по событию применяются одной транзакцией (repo.apply_outcome).
Ключ идемпотентности – id апдейта Telegram: повторная доставка того же
апдейта задач не задвоит.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from studio.model.funnel import EdgeKind, StepType

from . import repo, studio_test
from .engine_lib.branch import compose_webinar_dt, pick_branch_for_now
from .engine_lib.deep_link import parse_deep_link
from .engine_lib.phone import _normalize_phone, phone_from_text
from .executor import DEDUP_WINDOW_S, RESEND_ENTRY
from .graph import CompiledFunnel, cohort_matches
from .planner import PlanCtx, plan
from .repo import JobSpec, Outcome
from .steps import survey

if TYPE_CHECKING:
    from .service import BotEntry, Runtime

log = logging.getLogger("runtime.gateway")

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
DEFAULT_EMAIL_RETRY = "Похоже, в адресе ошибка 🙈 Пришлите почту одним сообщением, например name@mail.ru"


@dataclass
class TgUser:
    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None


def platform_of(entry) -> str:
    """Платформа события: у BotEntry (Telegram) её нет – это "tg"; MAX-маршрут
    (runtime/platforms/max_hub.MaxRoute) несёт "max"."""
    return getattr(entry, "platform", "tg")


class Gateway:
    def __init__(self, rt: "Runtime"):
        self.rt = rt

    # ── /start ───────────────────────────────────────────────────────────
    async def on_start(self, entry: "BotEntry", user: TgUser, payload: str, update_key: str) -> str:
        """→ что сделали (для логов и тестов): started / ignored / no_funnel."""
        rt = self.rt
        tenant = rt.registry.tenant(entry.tenant_id)
        fid = rt.registry.entry_funnel_id(tenant)
        # «Проверить в боте» из Студии: в указанную воронку, с начала (runtime/studio_test.py)
        test_run = False
        if studio_test.is_test_payload(payload):
            want = studio_test.match(payload, rt.registry.funnel_ids(tenant.id))
            if want is not None:
                fid, test_run = want, True
            else:
                log.info("[%s] %s: проверочный вход %r – такой воронки в запуске нет, обычный /start",
                         tenant.slug, user.id, payload)
        g = await rt.registry.current(tenant.id, fid) if fid else None
        if g is None:
            log.warning("[%s] /start от %s, но опубликованной воронки нет", tenant.slug, user.id)
            return "no_funnel"
        now = rt.clock.now()
        # служебный payload – не рекламный заход: UTM и метку входа из него не берём
        parsed = parse_deep_link("" if test_run else payload)
        fresh = test_run or tenant.settings.restart_policy == "restart"
        platform = platform_of(entry)
        async with rt.pool.acquire() as conn:
            async with conn.transaction():
                existed = await conn.fetchval(
                    "SELECT 1 FROM participants WHERE tenant_id=$1 AND platform=$2 AND uid=$3 FOR UPDATE",
                    tenant.id, platform, user.id)
                part = await conn.fetchrow(
                    """INSERT INTO participants (tenant_id, platform, uid, first_name, last_name,
                            username, utm_source, utm_medium, utm_campaign, utm_content, utm_term,
                            trigger, referrer_code, first_payload, created_at, updated_at)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$15)
                       ON CONFLICT (tenant_id, platform, uid) DO UPDATE SET
                            first_name=COALESCE(EXCLUDED.first_name, participants.first_name),
                            last_name=COALESCE(EXCLUDED.last_name, participants.last_name),
                            username=COALESCE(EXCLUDED.username, participants.username),
                            blocked=false, blocked_at=NULL, updated_at=EXCLUDED.updated_at
                       RETURNING *""",
                    tenant.id, platform, user.id, user.first_name, user.last_name, user.username,
                    parsed.utm_source, parsed.utm_medium, parsed.utm_campaign, parsed.utm_content,
                    parsed.utm_term, parsed.trigger, parsed.referrer_code, payload or None, now)
                pos = await conn.fetchrow(
                    """SELECT * FROM positions WHERE tenant_id=$1 AND platform=$2 AND uid=$3
                       AND funnel_id=$4 FOR UPDATE""", tenant.id, platform, user.id, g.funnel_id)
                out = Outcome()
                out.act("start" if not existed else "start_re_entry", payload=payload or "")
                if fresh:
                    # вход с чистого листа: анти-дубль 90 с считает шаги только после этой отметки
                    out.act("start_fresh", payload="проверка из Студии" if test_run
                            else "политика restart: всегда с начала")

                # выбор ветки: эфир человека (как _resolve_entry_step движка)
                branch_id = pos["branch"] if pos else None
                webinar_at = pos["webinar_at"] if pos else None
                entry_id = None
                if g.router is not None:
                    stale = webinar_at is None or webinar_at <= now or not branch_id
                    if stale:
                        picked = pick_branch_for_now(g.router, now)
                        if picked:
                            br, _w, wdate = picked
                            branch_id = br.id
                            webinar_at = compose_webinar_dt(wdate, br.webinar_time)
                            entry_id = br.entry_step
                        else:
                            log.warning("[%s] branch_router не подобрал ветку на %s", tenant.slug, now)
                    else:
                        entry_id = next((b.entry_step for b in g.router.branches if b.id == branch_id),
                                        None)
                if entry_id is None and g.first_step is not None:
                    entry_id = g.first_step.id
                # свой первый шаг у когорты трафика (entry_variants)
                cctx = {k: (part[k] or "") for k in ("utm_source", "utm_medium", "utm_campaign", "trigger")}
                for ev in g.funnel.entry_variants:
                    if cohort_matches(ev.when, cctx, platform) and g.step(ev.entry_step) is not None:
                        entry_id = ev.entry_step
                        break
                if entry_id is None:
                    log.warning("[%s] у воронки %s нет первого шага", tenant.slug, g.funnel_id)
                    return "no_funnel"

                # повторный /start – политика запуска (CLAUDE.md bot-engine §23)
                if pos is not None and pos["status"] == "active" and test_run:
                    out.cancel_all_pending = "проверка из Студии – воронка с начала"
                elif pos is not None and pos["status"] == "active":
                    pending = await repo.has_pending(conn, tenant_id=tenant.id, platform=platform,
                                                     uid=user.id, funnel_id=g.funnel_id)
                    policy = tenant.settings.restart_policy
                    ignore = False
                    if policy == "keep":
                        ignore = pending or pos["awaiting"] is not None
                    elif policy == "engine":
                        recent = await repo.recently_fired(
                            conn, tenant_id=tenant.id, platform=platform, uid=user.id,
                            funnel_id=g.funnel_id, step_id=entry_id,
                            since=now - timedelta(seconds=DEDUP_WINDOW_S))
                        ignore = recent and pending
                    if ignore:
                        why = ("вход был меньше 90 с назад и следующие шаги уже запланированы"
                               if policy == "engine" else "у человека есть запланированные шаги")
                        out.act("start_ignored",
                                payload=f"повторный /start не перезапускает воронку (политика {policy}): {why}")
                        log.info("[%s] %s: повторный /start – воронка уже идёт, таймеры не трогаем",
                                 tenant.slug, user.id)
                        entry = g.step(entry_id)
                        if g.funnel.resend_entry_on_repeat and entry is not None:
                            # боты-выдачи: тишина на /start выглядит как сломанный бот –
                            # первое сообщение ещё раз, запланированное не трогаем
                            out.specs.append(JobSpec(step_id=entry.id, step_key=entry.key, kind="fire",
                                                     run_at=now, reason=RESEND_ENTRY))
                            await repo.apply_outcome(conn, tenant_id=tenant.id, platform=platform,
                                                     uid=user.id, funnel_id=g.funnel_id,
                                                     version=pos["version"], out=out, now=now,
                                                     idem_prefix=f"start:{update_key}")
                            rt.wake()
                            return "resent"
                        await repo.insert_actions(conn, tenant_id=tenant.id, platform=platform,
                                                  uid=user.id, funnel_id=g.funnel_id,
                                                  version=pos["version"], actions=out.actions, now=now)
                        return "ignored"
                    out.cancel_all_pending = "повторный /start – воронка с начала"

                await conn.execute(
                    """INSERT INTO positions (tenant_id, platform, uid, funnel_id, version, branch,
                                              webinar_at, status, awaiting, entered_at, updated_at)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,'active',NULL,$8,$8)
                       ON CONFLICT (tenant_id, platform, uid, funnel_id) DO UPDATE SET
                            version=EXCLUDED.version, branch=EXCLUDED.branch,
                            webinar_at=EXCLUDED.webinar_at, status='active', awaiting=NULL,
                            entered_at=EXCLUDED.entered_at, updated_at=EXCLUDED.updated_at""",
                    tenant.id, platform, user.id, g.funnel_id, g.version, branch_id, webinar_at, now)
                if pos is not None and pos["status"] != "active":
                    out.cancel_all_pending = "повторный /start – воронка с начала"
                ctx = PlanCtx(now=now, webinar_at=webinar_at, entered_at=part["created_at"],
                              branch_id=branch_id, dnd=tenant.settings.dnd)
                step = g.step(entry_id)
                out.specs += plan(g, entry_id, (step.schedule.after_start_seconds if step else 0) or 0,
                                  ctx, reason="/start")
                await repo.apply_outcome(conn, tenant_id=tenant.id, platform=platform, uid=user.id,
                                         funnel_id=g.funnel_id, version=g.version, out=out, now=now,
                                         idem_prefix=f"start:{update_key}")
        log.info("[%s] /start %s (%s%s) payload=%r → %s, v%s", tenant.slug, user.id,
                 "новый" if not existed else "повторный", ", проверка из Студии" if test_run else "",
                 payload, g.key_of(entry_id), g.version)
        rt.wake()
        return "started"

    # ── общее для событий внутри воронки ─────────────────────────────────
    async def _ctx(self, conn, entry: "BotEntry", uid: int, *, step_id: uuid.UUID | None = None):
        tenant = self.rt.registry.tenant(entry.tenant_id)
        fid = self.rt.registry.entry_funnel_id(tenant)
        if not fid:
            return tenant, None, None, None
        pf = platform_of(entry)
        part = await conn.fetchrow(
            "SELECT * FROM participants WHERE tenant_id=$1 AND platform=$3 AND uid=$2 FOR UPDATE",
            tenant.id, uid, pf)
        # Человек мог перейти в другую воронку (chain_to_funnel): событие относится
        # к той, где ждут его телефон/почту или где живёт нажатая кнопка.
        # С одной воронкой выбор всегда – воронка /start, как раньше.
        for r in await conn.fetch(
                """SELECT funnel_id, version, awaiting, status FROM positions
                   WHERE tenant_id=$1 AND platform=$4 AND uid=$2 AND funnel_id <> $3
                   ORDER BY updated_at DESC""", tenant.id, uid, fid, pf):
            if step_id is not None:
                g2 = await self.rt.registry.get(tenant.id, r["funnel_id"], r["version"])
                cur = await self.rt.registry.current(tenant.id, r["funnel_id"])
                if (g2 and g2.step(step_id)) or (cur and cur.step(step_id)):
                    fid = r["funnel_id"]
                    break
            elif r["status"] == "active" and (r["awaiting"] or {}).get("kind") in ("contact", "email"):
                fid = r["funnel_id"]
                break
        pos = await conn.fetchrow(
            """SELECT * FROM positions WHERE tenant_id=$1 AND platform=$4 AND uid=$2
               AND funnel_id=$3 FOR UPDATE""", tenant.id, uid, fid, pf)
        g = None
        if pos is not None:
            g = await self.rt.registry.get(tenant.id, fid, pos["version"])
        return tenant, g, part, pos

    def _plan_ctx(self, tenant, part, pos) -> PlanCtx:
        return PlanCtx(now=self.rt.clock.now(), webinar_at=pos["webinar_at"],
                       entered_at=part["created_at"], branch_id=pos["branch"], dnd=tenant.settings.dnd)

    async def _apply(self, conn, tenant, uid, g: CompiledFunnel, out: Outcome, update_key: str,
                     platform: str = "tg"):
        await repo.apply_outcome(conn, tenant_id=tenant.id, platform=platform, uid=uid,
                                 funnel_id=g.funnel_id, version=g.version, out=out,
                                 now=self.rt.clock.now(), idem_prefix=f"ev:{update_key}")

    # ── контакт ──────────────────────────────────────────────────────────
    async def on_contact(self, entry: "BotEntry", uid: int, phone: str, update_key: str, *,
                         source: str = "contact") -> str:
        rt = self.rt
        norm = _normalize_phone(phone) or phone
        async with rt.pool.acquire() as conn:
            async with conn.transaction():
                tenant, g, part, pos = await self._ctx(conn, entry, uid)
                if part is None:
                    return "unknown_user"
                out = Outcome()
                out.participant["phone"] = norm
                out.act("contact", payload=source)
                if g is None or pos is None:
                    await repo.update_participant(conn, tenant.id, platform_of(entry), uid, out.participant,
                                                  rt.clock.now())
                    return "saved"
                aw = pos["awaiting"] or {}
                step = None
                if aw.get("kind") == "contact":
                    step = g.step(aw.get("step_id"))
                # Ведём по ветке «дал телефон» только если человек его сейчас ждёт
                # (метка awaiting). Повторно присланный контакт просто сохраняется –
                # иначе цепочка после запроса телефона запустилась бы второй раз.
                if step is not None and step.type == StepType.phone_request:
                    out.awaiting = None
                    out.cancel_slots.append((step.id, "skip"))
                    e = g.edge(step.id, EdgeKind.contact)
                    if e is not None:
                        out.specs += plan(g, e.to_step, e.delay_seconds or 0,
                                          self._plan_ctx(tenant, part, pos),
                                          reason=f"дал телефон на {step.key}")
                    log.info("[%s] %s поделился телефоном на %s", tenant.slug, uid, step.key)
                await self._apply(conn, tenant, uid, g, out, update_key, platform_of(entry))
        rt.wake()
        return "ok"

    # ── кнопка-клейм ─────────────────────────────────────────────────────
    async def on_callback(self, entry: "BotEntry", uid: int, data: str, update_key: str) -> str:
        """→ текст всплывающей подсказки (или пусто)."""
        rt = self.rt
        if survey.is_survey_callback(data):
            return await survey.on_callback(rt, entry, uid, data, update_key,
                                            platform=platform_of(entry))
        if not data.startswith("c:"):
            return ""
        try:
            sid = uuid.UUID(hex=data[2:])
        except ValueError:
            return ""
        async with rt.pool.acquire() as conn:
            async with conn.transaction():
                tenant, g, part, pos = await self._ctx(conn, entry, uid, step_id=sid)
                if g is None or part is None:
                    return ""
                step = g.step(sid)
                if step is None:
                    cur = await rt.registry.current(tenant.id, g.funnel_id)
                    if cur is not None and cur.step(sid) is not None:
                        g, step = cur, cur.step(sid)
                if step is None:
                    return "Эта кнопка устарела"
                out = Outcome()
                out.act("callback", step)
                cb = g.edge(step.id, EdgeKind.callback)
                if cb is None:
                    await self._apply(conn, tenant, uid, g, out, update_key, platform_of(entry))
                    return step.claim.ack_text or ""
                done = g.edge(step.id, EdgeKind.done)
                if done is not None and not step.claim.keeps_chain:
                    out.cancel_slots.append((done.to_step, "fire"))
                # движок зовёт fire_step напрямую – сразу, без пересчёта времени
                tgt = g.step(cb.to_step)
                out.specs.append(JobSpec(step_id=cb.to_step, step_key=tgt.key if tgt else "",
                                         kind="fire", run_at=rt.clock.now(),
                                         reason=f"нажал кнопку в {step.key}"))
                if pos is not None and pos["status"] != "active":
                    out.position_status = None
                    await conn.execute(
                        """UPDATE positions SET status='active' WHERE tenant_id=$1 AND platform=$4
                           AND uid=$2 AND funnel_id=$3 AND status='done'""", tenant.id, uid, g.funnel_id,
                        platform_of(entry))
                await self._apply(conn, tenant, uid, g, out, update_key, platform_of(entry))
        rt.wake()
        return ""

    # ── текст: почта, телефон текстом, прочее ────────────────────────────
    async def on_text(self, entry: "BotEntry", uid: int, text: str, update_key: str) -> str | None:
        """→ ответ человеку (или None)."""
        rt = self.rt
        text = (text or "").strip()
        async with rt.pool.acquire() as conn:
            async with conn.transaction():
                tenant, g, part, pos = await self._ctx(conn, entry, uid)
                if part is None:
                    return None
                aw = (pos["awaiting"] if pos else None) or {}
                if aw.get("kind") == "contact" and phone_from_text(text):
                    pass  # ниже, вне транзакции
                elif aw.get("kind") == "email" and g is not None:
                    step = g.step(aw.get("step_id"))
                    out = Outcome()
                    if not EMAIL_RE.match(text):
                        out.act("email_invalid", step, payload=text[:200])
                        await self._apply(conn, tenant, uid, g, out, update_key, platform_of(entry))
                        return (step.email.retry_text if step else "") or DEFAULT_EMAIL_RETRY
                    out.participant["email"] = text.lower()
                    out.awaiting = None
                    out.act("email", step, payload=text.lower())
                    if step is not None:
                        out.cancel_slots.append((step.id, "skip"))
                        e = g.edge(step.id, EdgeKind.email)
                        if e is not None:
                            out.specs += plan(g, e.to_step, e.delay_seconds or 0,
                                              self._plan_ctx(tenant, part, pos),
                                              reason=f"прислал почту на {step.key}")
                    await self._apply(conn, tenant, uid, g, out, update_key, platform_of(entry))
                    rt.wake()
                    return None
                else:
                    # свободный ответ на вопрос опроса (survey.free)
                    if await survey.on_free_text(rt, conn, tenant, uid, text, update_key,
                                                 platform=platform_of(entry)):
                        rt.wake()
                        return None
                    fields = {}
                    if EMAIL_RE.match(text) and not part["email"]:
                        fields["email"] = text.lower()   # запоздавшая почта – сохранить
                    await repo.update_participant(conn, tenant.id, platform_of(entry), uid, fields,
                                                  rt.clock.now())
                    await repo.insert_actions(conn, tenant_id=tenant.id, platform=platform_of(entry),
                                              uid=uid,
                                              funnel_id=g.funnel_id if g else None,
                                              version=pos["version"] if pos else None,
                                              actions=[repo.ActionRec("message", payload=text[:1000])],
                                              now=rt.clock.now())
                    return None
        # номер, присланный текстом, пока ждём телефон (часть людей не видит кнопку)
        await self.on_contact(entry, uid, phone_from_text(text), update_key, source="text")
        return None

    # ── блокировка ───────────────────────────────────────────────────────
    async def on_block(self, entry: "BotEntry", uid: int, blocked: bool) -> None:
        rt = self.rt
        now = rt.clock.now()
        async with rt.pool.acquire() as conn:
            async with conn.transaction():
                tenant = rt.registry.tenant(entry.tenant_id)
                pf = platform_of(entry)
                if await conn.fetchval("SELECT 1 FROM participants WHERE tenant_id=$1 AND platform=$3 "
                                       "AND uid=$2 FOR UPDATE", tenant.id, uid, pf) is None:
                    return
                await repo.update_participant(conn, tenant.id, pf, uid,
                                              {"blocked": blocked, "blocked_at": now if blocked else None},
                                              now)
                if blocked:
                    await conn.execute(
                        """UPDATE positions SET status='stopped', awaiting=NULL, updated_at=$3
                           WHERE tenant_id=$1 AND platform=$4 AND uid=$2""", tenant.id, uid, now, pf)
                    n = await repo.cancel_user(conn, tenant_id=tenant.id, platform=pf, uid=uid,
                                               funnel_id=None, reason="человек заблокировал бота", now=now)
                    log.info("[%s] %s заблокировал бота – отменено задач: %s", tenant.slug, uid, n)
                await repo.insert_actions(conn, tenant_id=tenant.id, platform=pf, uid=uid,
                                          funnel_id=None, version=None,
                                          actions=[repo.ActionRec("blocked" if blocked else "unblocked")],
                                          now=now)
