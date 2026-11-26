"""Переход в другую воронку того же запуска (стрелка chain_to_funnel) –
перенос _fire_chain_to_funnel движка bot-engine.

Как в движке:
  * переход важнее on_done: если у шага есть обе стрелки, on_done не идёт;
  * задержка – со стрелки (в движке on_done_after_seconds); «печатает…»
    (`typing_before`) показывается сразу, если задержка больше нуля;
  * целевая воронка стартует у человека с начала (как start_for_tg): ветка
    и эфир по branch_router, свой вход когорты (entry_variants), первый шаг;
    её прежние таймеры у человека отменяются;
  * имя цели: "default" – воронка, куда ведёт /start; "alt" и прочие
    псевдонимы – `funnel_aliases` в настройках запуска; иначе – id воронки;
  * переход в саму себя не выполняется.

Отличия (намеренные):
  * цели нет / переход в себя / сработала защита от цикла – идём по on_done
    (движок молча останавливался: главное правило «ошибка шага → цепочка
    дальше»);
  * защита от цикла A → B → A: тот же переход у человека чаще раза в
    CHAIN_REPEAT_S или больше CHAIN_MAX_PER_HOUR переходов за час –
    не выполняется (actions: chain_loop);
  * ветка / эфир в целевой воронке выбираются в момент перехода, а не
    через задержку (задержки у переходов – секунды).

Итог перехода применяется своей транзакцией; событие chain_in пишется с id
задачи-источника – повтор задачи после падения процесса второй раз воронку
не перезапустит.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from studio.model.funnel import Edge, Step

from .. import repo
from ..engine_lib.branch import compose_webinar_dt, pick_branch_for_now
from ..graph import cohort_matches
from ..planner import PlanCtx, plan
from ..repo import Outcome
from .common import settings_for

if TYPE_CHECKING:
    from ..executor import Executor, UserCtx
    from ..graph import CompiledFunnel

log = logging.getLogger("runtime.steps.chain")

CHAIN_REPEAT_S = 60
CHAIN_MAX_PER_HOUR = 10


def resolve_target(ex: "Executor", tenant_id: int, name: str) -> str | None:
    reg = ex.rt.registry
    tenant = reg.tenant(tenant_id)
    aliases = settings_for(ex, tenant_id).funnel_aliases
    fid = aliases.get(name)
    if fid is None and name == "default":
        fid = reg.entry_funnel_id(tenant)
    fid = fid or name
    return fid if reg.current_version(tenant_id, fid) is not None else None


def entry_for(g2: "CompiledFunnel", pos, part, now, platform: str):
    """(шаг входа, ветка, эфир) – как выбор входа в gateway.on_start."""
    branch_id = pos["branch"] if pos else None
    webinar_at = pos["webinar_at"] if pos else None
    entry_id = None
    if g2.router is not None:
        if webinar_at is None or webinar_at <= now or not branch_id:
            picked = pick_branch_for_now(g2.router, now)
            if picked:
                br, _w, wdate = picked
                branch_id, webinar_at, entry_id = br.id, compose_webinar_dt(wdate, br.webinar_time), \
                    br.entry_step
        else:
            entry_id = next((b.entry_step for b in g2.router.branches if b.id == branch_id), None)
    if entry_id is None and g2.first_step is not None:
        entry_id = g2.first_step.id
    cctx = {k: (part[k] or "") for k in ("utm_source", "utm_medium", "utm_campaign", "trigger")}
    for ev in g2.funnel.entry_variants:
        if cohort_matches(ev.when, cctx, platform) and g2.step(ev.entry_step) is not None:
            entry_id = ev.entry_step
            break
    return entry_id, branch_id, webinar_at


async def run_chain(ex: "Executor", u: "UserCtx", g: "CompiledFunnel", step: Step, edge: Edge,
                    job, out: Outcome) -> bool:
    """Переход по стрелке chain_to_funnel. → True – выполнен (on_done не нужен)."""
    rt = ex.rt
    name = edge.to_funnel or ""
    target = resolve_target(ex, u.tenant_id, name)
    if target is None:
        log.warning("[%s] %s: переход из %s в воронку «%s» – такой опубликованной воронки нет, "
                    "идём по on_done", u.slug, u.uid, step.key, name)
        out.act("chain_failed", step, payload=f"нет воронки «{name}»")
        return False
    if target == g.funnel_id:
        log.warning("[%s] %s: переход из %s в свою же воронку – пропускаем", u.slug, u.uid, step.key)
        out.act("chain_failed", step, payload="переход в свою же воронку")
        return False
    delay = max(0, int(edge.delay_seconds or 0))
    idem = f"chain:j{job['id']}"
    tenant = rt.registry.tenant(u.tenant_id)
    g2 = await rt.registry.current(u.tenant_id, target)
    if g2 is None:
        out.act("chain_failed", step, payload=f"нет версии воронки «{target}»")
        return False
    async with rt.pool.acquire() as conn:
        async with conn.transaction():
            part = await conn.fetchrow(
                "SELECT * FROM participants WHERE tenant_id=$1 AND platform=$2 AND uid=$3 FOR UPDATE",
                u.tenant_id, u.platform, u.uid)
            if await conn.fetchval(
                    """SELECT 1 FROM actions WHERE tenant_id=$1 AND platform=$2 AND uid=$3
                         AND action='chain_in' AND job_id=$4 LIMIT 1""",
                    u.tenant_id, u.platform, u.uid, job["id"]):
                out.act("chain", step, payload=f"{g.funnel_id} → {target} (уже выполнен)")
                return True
            # защита от цикла
            recent = await conn.fetch(
                """SELECT payload, created_at FROM actions WHERE tenant_id=$1 AND platform=$2 AND uid=$3
                     AND action='chain_in' AND created_at > $4""",
                u.tenant_id, u.platform, u.uid, u.now - timedelta(hours=1))
            pair = f"{g.funnel_id} → {target}"
            same = [r for r in recent if r["payload"] == pair
                    and r["created_at"] > u.now - timedelta(seconds=CHAIN_REPEAT_S)]
            if same or len(recent) >= CHAIN_MAX_PER_HOUR:
                why = (f"тот же переход меньше {CHAIN_REPEAT_S} с назад" if same
                       else f"больше {CHAIN_MAX_PER_HOUR} переходов за час")
                log.warning("[%s] %s: переход %s не выполнен – похоже на цикл (%s)",
                            u.slug, u.uid, pair, why)
                out.act("chain_loop", step, payload=f"{pair}: {why}")
                return False
            pos2 = await conn.fetchrow(
                """SELECT * FROM positions WHERE tenant_id=$1 AND platform=$2 AND uid=$3 AND funnel_id=$4
                   FOR UPDATE""", u.tenant_id, u.platform, u.uid, target)
            entry_id, branch_id, webinar_at = entry_for(g2, pos2, part, u.now, u.platform)
            if entry_id is None:
                out.act("chain_failed", step, payload=f"у воронки «{target}» нет первого шага")
                return False
            await conn.execute(
                """INSERT INTO positions (tenant_id, platform, uid, funnel_id, version, branch,
                                          webinar_at, status, awaiting, entered_at, updated_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,'active',NULL,$8,$8)
                   ON CONFLICT (tenant_id, platform, uid, funnel_id) DO UPDATE SET
                        version=EXCLUDED.version, branch=EXCLUDED.branch, step_id=NULL, step_key=NULL,
                        webinar_at=EXCLUDED.webinar_at, status='active', awaiting=NULL,
                        entered_at=EXCLUDED.entered_at, updated_at=EXCLUDED.updated_at""",
                u.tenant_id, u.platform, u.uid, target, g2.version, branch_id, webinar_at, u.now)
            o2 = Outcome()
            o2.cancel_all_pending = f"переход из воронки {g.funnel_id}"
            o2.act("chain_in", payload=pair, from_step=step.key)
            ctx2 = PlanCtx(now=u.now, webinar_at=webinar_at, entered_at=part["created_at"],
                           branch_id=branch_id, dnd=tenant.settings.dnd)
            first = g2.step(entry_id)
            o2.specs += plan(g2, entry_id, delay + ((first.schedule.after_start_seconds if first else 0)
                                                    or 0), ctx2, reason=f"переход из {g.funnel_id}/{step.key}")
            await repo.apply_outcome(conn, tenant_id=u.tenant_id, platform=u.platform, uid=u.uid,
                                     funnel_id=target, version=g2.version, out=o2, now=u.now,
                                     idem_prefix=idem, job_id=job["id"])
    # «печатает…» – только Telegram: в MAX это действие чата (`/chats/{id}/actions`),
    # а id диалога с человеком движок не хранит
    if edge.typing_before and delay > 0 and u.platform != "max":
        entry = rt.bot_for_tenant(u.tenant_id)
        if entry is not None:
            try:
                await entry.bot.send_chat_action(u.uid, action="typing")
            except Exception:  # noqa: BLE001 – не критично
                pass
    log.info("[%s] %s: переход %s через %s с (вход %s)", u.slug, u.uid, pair, delay,
             g2.key_of(entry_id))
    out.act("chain", step, payload=pair, delay=delay)
    return True
