"""Состояние движка: очередь, позиции, версии – для `runtime status` и /status."""
from __future__ import annotations

import json

import asyncpg

from .clock import Clock, msk


async def collect(pool: asyncpg.Pool, tenant_slug: str | None = None, *, clock: Clock | None = None) -> dict:
    now = (clock or Clock()).now()
    async with pool.acquire() as conn:
        tenants = await conn.fetch(
            "SELECT id, slug, title, status FROM tenants WHERE ($1::text IS NULL OR slug=$1) ORDER BY id",
            tenant_slug)
        out = {"now": msk(now), "tenants": []}
        for t in tenants:
            tid = t["id"]
            versions = await conn.fetch(
                """SELECT funnel_id, version, published_at, published_by, comment, mode FROM funnel_versions
                   WHERE tenant_id=$1 ORDER BY funnel_id, version DESC""", tid)
            jobs = await conn.fetch(
                "SELECT status, count(*) n FROM jobs WHERE tenant_id=$1 GROUP BY status", tid)
            pend = await conn.fetch(
                """SELECT funnel_id, version, step_key, kind, count(*) n, min(run_at) nxt
                   FROM jobs WHERE tenant_id=$1 AND status='pending'
                   GROUP BY 1,2,3,4 ORDER BY min(run_at)""", tid)
            pos = await conn.fetch(
                """SELECT funnel_id, version, status, coalesce(step_key, '—') step_key,
                          (awaiting->>'kind') aw, count(*) n
                   FROM positions WHERE tenant_id=$1 GROUP BY 1,2,3,4,5 ORDER BY 1,3,4""", tid)
            orphan = await conn.fetchval(
                """SELECT count(*) FROM positions p WHERE tenant_id=$1 AND status='active'
                     AND awaiting IS NULL AND NOT EXISTS (
                       SELECT 1 FROM jobs j WHERE j.status IN ('pending','running')
                         AND j.tenant_id=p.tenant_id AND j.platform=p.platform AND j.uid=p.uid
                         AND j.funnel_id=p.funnel_id)""", tid)
            failed = await conn.fetch(
                """SELECT id, uid, step_key, reason, finished_at FROM jobs WHERE tenant_id=$1
                   AND status='failed' ORDER BY finished_at DESC NULLS LAST LIMIT 5""", tid)
            parts = await conn.fetchrow(
                """SELECT count(*) n, count(*) FILTER (WHERE blocked) blocked,
                          count(*) FILTER (WHERE phone IS NOT NULL) phones FROM participants WHERE tenant_id=$1""",
                tid)
            sent = await conn.fetchrow(
                """SELECT count(*) FILTER (WHERE status='ok') ok, count(*) FILTER (WHERE status='error') err
                   FROM sent_messages WHERE tenant_id=$1""", tid)
            bots = await conn.fetch("SELECT secret_ref, username, tg_bot_id FROM bots WHERE tenant_id=$1 "
                                    "AND platform <> 'max'", tid)
            max_bots = await conn.fetch(
                """SELECT b.secret_ref, b.username, b.platform_bot_id, bt.prefix, bt.is_default,
                          (SELECT count(*) FROM bot_tenants x WHERE x.bot_id=b.id) AS shared
                   FROM bot_tenants bt JOIN bots b ON b.id=bt.bot_id WHERE bt.tenant_id=$1
                   ORDER BY b.id""", tid)
            out["tenants"].append({
                "slug": t["slug"], "title": t["title"], "status": t["status"],
                "bots": [dict(b) for b in bots],
                "max_bots": [dict(b) for b in max_bots],
                "versions": [{"funnel": v["funnel_id"], "version": v["version"],
                              "published": msk(v["published_at"]), "by": v["published_by"],
                              "comment": v["comment"], "mode": v["mode"]} for v in versions],
                "jobs": {r["status"]: r["n"] for r in jobs},
                "pending": [{"funnel": r["funnel_id"], "version": r["version"], "step": r["step_key"],
                             "kind": r["kind"], "count": r["n"], "next": msk(r["nxt"])} for r in pend],
                "positions": [{"funnel": r["funnel_id"], "version": r["version"], "status": r["status"],
                               "step": r["step_key"], "awaiting": r["aw"], "count": r["n"]} for r in pos],
                "orphans": orphan,
                "failed": [{"job": r["id"], "uid": r["uid"], "step": r["step_key"], "reason": r["reason"],
                            "at": msk(r["finished_at"])} for r in failed],
                "participants": dict(parts), "sent": dict(sent),
            })
    return out


def render(st: dict) -> str:
    lines = [f"Движок воронок – {st['now']}"]
    if not st["tenants"]:
        lines.append("запусков нет")
    for t in st["tenants"]:
        lines.append("")
        lines.append(f"■ {t['slug']} «{t['title']}» [{t['status']}]")
        for b in t["bots"]:
            lines.append(f"  бот: {b['secret_ref']}  @{b['username'] or '?'}  id {b['tg_bot_id'] or '?'}")
        for b in t.get("max_bots", []):
            lines.append(f"  MAX-бот: {b['secret_ref']}  @{b['username'] or '?'}  id {b['platform_bot_id'] or '?'}"
                         f"  метка «{b['prefix']}»" + ("  по умолчанию" if b["is_default"] else "")
                         + (f"  (общий на {b['shared']} запуска)" if b["shared"] > 1 else ""))
        cur = {}
        for v in t["versions"]:
            cur.setdefault(v["funnel"], v)
        for f, v in cur.items():
            lines.append(f"  воронка {f}: v{v['version']} от {v['published']} ({v['mode']})"
                         + (f" – {v['comment']}" if v["comment"] else ""))
        p = t["participants"]
        lines.append(f"  люди: {p['n']} (телефон у {p['phones']}, заблокировали {p['blocked']}); "
                     f"сообщений ушло {t['sent']['ok']}, ошибок {t['sent']['err']}")
        j = t["jobs"]
        lines.append("  задачи: " + ", ".join(f"{k} {v}" for k, v in sorted(j.items())) if j else "  задач нет")
        if t["pending"]:
            lines.append("  ожидают:")
            for r in t["pending"]:
                lines.append(f"    {r['step']:<18} ×{r['count']:<4} {r['kind']:<5} v{r['version']}  "
                             f"ближайшая {r['next']}")
        if t["positions"]:
            lines.append("  позиции:")
            for r in t["positions"]:
                aw = f", ждём {r['awaiting']}" if r["awaiting"] else ""
                lines.append(f"    {r['status']:<8} на {r['step']:<18} ×{r['count']} v{r['version']}{aw}")
        if t["orphans"]:
            lines.append(f"  ⚠ идут без задач и без ожидания: {t['orphans']} (оборванные цепочки?)")
        for r in t["failed"]:
            lines.append(f"  ✖ задача {r['job']} ({r['uid']}, {r['step']}) {r['at']}: {r['reason']}")
    return "\n".join(lines)


async def main_status(tenant: str | None, *, as_json: bool = False) -> int:
    from .config import RuntimeConfig
    from .db import create_pool, migrate
    pool = await create_pool(RuntimeConfig().dsn, max_size=2)
    try:
        await migrate(pool)
        st = await collect(pool, tenant)
        print(json.dumps(st, ensure_ascii=False, indent=1, default=str) if as_json else render(st))
        return 0
    finally:
        await pool.close()
