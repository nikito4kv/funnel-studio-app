"""SQL движка: задачи, позиции, участники, отправки, события, кэш медиа.

Функции принимают соединение asyncpg (часто – внутри транзакции), чтобы
«пометить задачу выполненной + поставить следующие + сдвинуть позицию»
происходило одним коммитом: либо всё, либо ничего (исследование §6.1 –
раньше следующий шаг планировался В КОНЦЕ работы шага, и авария между
отправкой и планированием убивала весь остаток цепочки).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg

from .db import CH_JOBS

log = logging.getLogger("runtime.repo")


@dataclass
class JobSpec:
    """Задача, которую нужно поставить в очередь."""
    step_id: uuid.UUID
    step_key: str
    kind: str                 # fire / also / skip
    run_at: datetime
    reason: str = ""
    version: int | None = None   # None – версия родителя


@dataclass
class ActionRec:
    action: str
    step_id: uuid.UUID | None = None
    step_key: str | None = None
    payload: str | None = None
    data: dict | None = None


@dataclass
class Outcome:
    """Итог обработки шага или события – применяется одной транзакцией."""
    specs: list[JobSpec] = field(default_factory=list)
    cancel_slots: list[tuple[uuid.UUID, str]] = field(default_factory=list)  # (шаг, fire|skip)
    cancel_all_pending: str | None = None      # причина – отменить все задачи человека в воронке
    cancel_user_everywhere: str | None = None  # причина – все задачи человека в запуске (блок)
    position_step: tuple[uuid.UUID, str] | None = None
    position_status: str | None = None
    awaiting: Any = "keep"                     # "keep" | None (снять) | dict (поставить)
    participant: dict[str, Any] = field(default_factory=dict)
    actions: list[ActionRec] = field(default_factory=list)
    job_status: str = "done"
    job_reason: str = ""

    def act(self, action: str, step=None, payload: str | None = None, **data) -> None:
        self.actions.append(ActionRec(action=action,
                                      step_id=getattr(step, "id", None),
                                      step_key=getattr(step, "key", None),
                                      payload=payload, data=data or None))


# ── задачи ───────────────────────────────────────────────────────────────────

async def enqueue(conn: asyncpg.Connection, *, tenant_id: int, platform: str, uid: int,
                  funnel_id: str, version: int, specs: list[JobSpec], idem_prefix: str,
                  now: datetime, parent_job_id: int | None = None) -> list[int]:
    """Ставит задачи. Повтор с тем же idem_prefix ничего не дублирует.

    Как replace_existing у APScheduler: ожидающая задача на тот же шаг того же
    человека заменяется новой (у человека один таймер на шаг).
    """
    ids: list[int] = []
    earliest: datetime | None = None
    for i, sp in enumerate(specs):
        idem = f"{idem_prefix}:{i}:{sp.kind}:{sp.step_id}"
        if await conn.fetchval("SELECT 1 FROM jobs WHERE idem_key = $1", idem):
            continue
        slot = "skip" if sp.kind == "skip" else "fire"
        for attempt in range(3):
            try:
                async with conn.transaction():
                    await conn.execute(
                        """UPDATE jobs SET status='cancelled', reason='заменена новой задачей',
                                  updated_at=$7, finished_at=$7
                           WHERE status='pending' AND tenant_id=$1 AND platform=$2 AND uid=$3
                             AND funnel_id=$4 AND step_id=$5 AND slot=$6""",
                        tenant_id, platform, uid, funnel_id, sp.step_id, slot, now)
                    jid = await conn.fetchval(
                        """INSERT INTO jobs (tenant_id, platform, uid, funnel_id, version, step_id,
                                             step_key, kind, run_at, reason, idem_key,
                                             parent_job_id, created_at, updated_at)
                           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$13)
                           RETURNING id""",
                        tenant_id, platform, uid, funnel_id, sp.version or version, sp.step_id,
                        sp.step_key, sp.kind, sp.run_at, sp.reason, idem, parent_job_id, now)
                ids.append(jid)
                earliest = sp.run_at if earliest is None else min(earliest, sp.run_at)
                break
            except asyncpg.UniqueViolationError:
                # параллельная транзакция успела поставить свою ожидающую – заменим её
                if attempt == 2:
                    raise
    if earliest is not None:
        await conn.execute("SELECT pg_notify($1, $2)", CH_JOBS, str(earliest.timestamp()))
    return ids


async def cancel_slot(conn, *, tenant_id, platform, uid, funnel_id, step_id, slot, reason, now) -> int:
    r = await conn.execute(
        """UPDATE jobs SET status='cancelled', reason=$7, updated_at=$8, finished_at=$8
           WHERE status='pending' AND tenant_id=$1 AND platform=$2 AND uid=$3
             AND funnel_id=$4 AND step_id=$5 AND slot=$6""",
        tenant_id, platform, uid, funnel_id, step_id, slot, reason, now)
    return int(r.split()[-1])


async def cancel_user(conn, *, tenant_id, platform, uid, funnel_id: str | None, reason, now,
                      except_job: int | None = None) -> int:
    r = await conn.execute(
        """UPDATE jobs SET status='cancelled', reason=$5, updated_at=$6, finished_at=$6
           WHERE status='pending' AND tenant_id=$1 AND platform=$2 AND uid=$3
             AND ($4::text IS NULL OR funnel_id=$4) AND id <> COALESCE($7, -1)""",
        tenant_id, platform, uid, funnel_id, reason, now, except_job)
    return int(r.split()[-1])


async def has_pending(conn, *, tenant_id, platform, uid, funnel_id, step_id=None,
                      slot: str | None = None, except_job: int | None = None) -> bool:
    return bool(await conn.fetchval(
        """SELECT 1 FROM jobs WHERE status IN ('pending','running') AND tenant_id=$1
             AND platform=$2 AND uid=$3 AND funnel_id=$4
             AND ($5::uuid IS NULL OR step_id=$5) AND ($6::text IS NULL OR slot=$6)
             AND id <> COALESCE($7, -1) LIMIT 1""",
        tenant_id, platform, uid, funnel_id, step_id, slot, except_job))


async def claim_due(conn, *, now: datetime, limit: int, lease_until: datetime,
                    worker: str, tenant_ids: list[int] | None = None) -> list[asyncpg.Record]:
    """Берёт просроченные задачи в работу (SKIP LOCKED: воркеров может быть несколько).

    Задача, чья аренда истекла (процесс упал посреди шага), берётся снова.
    """
    return await conn.fetch(
        """WITH due AS (
               SELECT id FROM jobs
               WHERE ((status='pending' AND run_at <= $1) OR (status='running' AND lease_until < $1))
                 AND ($5::bigint[] IS NULL OR tenant_id = ANY($5))
               ORDER BY run_at, id
               LIMIT $2
               FOR UPDATE SKIP LOCKED)
           UPDATE jobs j SET status='running', attempts=j.attempts+1, lease_until=$3,
                             locked_by=$4, updated_at=$1
           FROM due WHERE j.id = due.id
           RETURNING j.*""",
        now, limit, lease_until, worker, tenant_ids)


async def renew_leases(conn, ids: list[int], *, worker: str, lease_until: datetime) -> None:
    if ids:
        await conn.execute(
            "UPDATE jobs SET lease_until=$3 WHERE id = ANY($1) AND status='running' AND locked_by=$2",
            ids, worker, lease_until)


async def next_run_at(conn) -> datetime | None:
    return await conn.fetchval("SELECT min(run_at) FROM jobs WHERE status='pending'")


# ── участники и позиции ──────────────────────────────────────────────────────

async def get_participant(conn, tenant_id: int, platform: str, uid: int):
    return await conn.fetchrow(
        "SELECT * FROM participants WHERE tenant_id=$1 AND platform=$2 AND uid=$3",
        tenant_id, platform, uid)


async def get_position(conn, tenant_id: int, platform: str, uid: int, funnel_id: str):
    return await conn.fetchrow(
        "SELECT * FROM positions WHERE tenant_id=$1 AND platform=$2 AND uid=$3 AND funnel_id=$4",
        tenant_id, platform, uid, funnel_id)


_PARTICIPANT_COLS = {"first_name", "last_name", "username", "phone", "email", "phone_auto_skipped",
                     "blocked", "blocked_at"}


async def update_participant(conn, tenant_id, platform, uid, fields: dict, now) -> None:
    fields = {k: v for k, v in fields.items() if k in _PARTICIPANT_COLS}
    if not fields:
        return
    sets = ", ".join(f"{k}=${i + 5}" for i, k in enumerate(fields))
    await conn.execute(
        f"UPDATE participants SET {sets}, updated_at=$4 WHERE tenant_id=$1 AND platform=$2 AND uid=$3",
        tenant_id, platform, uid, now, *fields.values())


async def insert_actions(conn, *, tenant_id, platform, uid, funnel_id, version, actions: list[ActionRec],
                         now, job_id=None) -> None:
    if not actions:
        return
    await conn.executemany(
        """INSERT INTO actions (tenant_id, platform, uid, action, funnel_id, version, step_id,
                                step_key, payload, data, job_id, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
        [(tenant_id, platform, uid, a.action[:40], funnel_id, version, a.step_id, a.step_key,
          (a.payload or None) and a.payload[:1000], a.data, job_id, now) for a in actions])


async def apply_outcome(conn, *, tenant_id: int, platform: str, uid: int, funnel_id: str,
                        version: int, out: Outcome, now: datetime, idem_prefix: str,
                        job_id: int | None = None) -> None:
    """Применяет Outcome. Вызывать внутри транзакции."""
    if out.cancel_user_everywhere:
        await conn.execute(
            """UPDATE positions SET status='stopped', awaiting=NULL, updated_at=$4
               WHERE tenant_id=$1 AND platform=$2 AND uid=$3""", tenant_id, platform, uid, now)
        await cancel_user(conn, tenant_id=tenant_id, platform=platform, uid=uid, funnel_id=None,
                          reason=out.cancel_user_everywhere, now=now, except_job=job_id)
    if out.cancel_all_pending:
        await cancel_user(conn, tenant_id=tenant_id, platform=platform, uid=uid,
                          funnel_id=funnel_id, reason=out.cancel_all_pending, now=now,
                          except_job=job_id)
    for step_id, slot in out.cancel_slots:
        await cancel_slot(conn, tenant_id=tenant_id, platform=platform, uid=uid,
                          funnel_id=funnel_id, step_id=step_id, slot=slot,
                          reason="отменена событием", now=now)
    if out.specs:
        await enqueue(conn, tenant_id=tenant_id, platform=platform, uid=uid, funnel_id=funnel_id,
                      version=version, specs=out.specs, idem_prefix=idem_prefix, now=now,
                      parent_job_id=job_id)
    if out.participant:
        await update_participant(conn, tenant_id, platform, uid, out.participant, now)
    # позиция
    sets, args = [], [tenant_id, platform, uid, funnel_id, now]
    if out.position_step is not None:
        args += [out.position_step[0], out.position_step[1], version]
        sets.append(f"step_id=${len(args) - 2}, step_key=${len(args) - 1}, version=${len(args)}")
    if out.awaiting != "keep":
        args.append(out.awaiting)
        sets.append(f"awaiting=${len(args)}")
    if out.position_status:
        args.append(out.position_status)
        sets.append(f"status=${len(args)}")
    if sets:
        await conn.execute(
            f"""UPDATE positions SET {', '.join(sets)}, updated_at=$5
                WHERE tenant_id=$1 AND platform=$2 AND uid=$3 AND funnel_id=$4""", *args)
    # воронка доехала: ни задач, ни ожидания – позиция «done»
    if not out.cancel_user_everywhere and out.position_status is None:
        await conn.execute(
            """UPDATE positions p SET status = CASE
                   WHEN p.awaiting IS NULL AND NOT EXISTS (
                        SELECT 1 FROM jobs j WHERE j.status IN ('pending','running')
                          AND j.tenant_id=p.tenant_id AND j.platform=p.platform AND j.uid=p.uid
                          AND j.funnel_id=p.funnel_id AND j.id <> COALESCE($5, -1))
                   THEN 'done' ELSE 'active' END
               WHERE tenant_id=$1 AND platform=$2 AND uid=$3 AND funnel_id=$4 AND status <> 'stopped'""",
            tenant_id, platform, uid, funnel_id, job_id)
    await insert_actions(conn, tenant_id=tenant_id, platform=platform, uid=uid,
                         funnel_id=funnel_id, version=version, actions=out.actions, now=now,
                         job_id=job_id)


# ── отправки ─────────────────────────────────────────────────────────────────

async def sent_for_job(conn, job_id: int):
    return await conn.fetchrow("SELECT * FROM sent_messages WHERE job_id=$1", job_id)


async def sent_begin(conn, *, tenant_id, platform, uid, funnel_id, version, step, job_id, now) -> int:
    return await conn.fetchval(
        """INSERT INTO sent_messages (tenant_id, platform, uid, funnel_id, version, step_id,
                                      step_key, job_id, status, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'sending',$9) RETURNING id""",
        tenant_id, platform, uid, funnel_id, version, step.id, step.key, job_id, now)


async def sent_finish(conn, sent_id: int, *, ok: bool, message_ids: list[int], error: str | None,
                      now, ext_message_ids: list[str] | None = None) -> None:
    """`ext_message_ids` – id сообщений MAX (строки); у Telegram – None."""
    await conn.execute(
        """UPDATE sent_messages SET status=$2, message_ids=$3, error=$4, sent_at=$5,
                  ext_message_ids=COALESCE($6, ext_message_ids) WHERE id=$1""",
        sent_id, "ok" if ok else "error", message_ids, (error or None) and error[:1000], now,
        ext_message_ids)


async def was_sent_before(conn, *, tenant_id, platform, uid, funnel_id, step_id) -> bool:
    return bool(await conn.fetchval(
        """SELECT 1 FROM sent_messages WHERE tenant_id=$1 AND platform=$2 AND uid=$3
             AND funnel_id=$4 AND step_id=$5 AND status='ok' LIMIT 1""",
        tenant_id, platform, uid, funnel_id, step_id))


async def registered_at(conn, *, tenant_id, platform, uid, funnel_id, step_ids) -> datetime | None:
    """Момент регистрации: последняя удачная отправка входного шага воронки этому человеку
    (reg_window.registered_at_msk движка; повторный /start сдвигает его на себя)."""
    if not step_ids:
        return None
    return await conn.fetchval(
        """SELECT max(sent_at) FROM sent_messages WHERE tenant_id=$1 AND platform=$2 AND uid=$3
             AND funnel_id=$4 AND step_id = ANY($5::uuid[]) AND status='ok'""",
        tenant_id, platform, uid, funnel_id, list(step_ids))


async def recently_fired(conn, *, tenant_id, platform, uid, funnel_id, step_id, since,
                         except_job: int | None = None) -> bool:
    """Шаг выполнялся этому человеку после `since` (анти-дубль 90 с).

    Отметка `start_fresh` (вход «с чистого листа»: «Проверить в боте» из Студии,
    повторный /start при политике `restart`) сдвигает начало окна: всё, что было до
    неё, дублем нового прохода не считается."""
    return bool(await conn.fetchval(
        """SELECT 1 FROM actions WHERE tenant_id=$1 AND platform=$2 AND uid=$3 AND funnel_id=$4
             AND step_id=$5 AND action='step' AND job_id IS DISTINCT FROM $7
             AND created_at > GREATEST($6, COALESCE(
                 (SELECT max(f.created_at) FROM actions f WHERE f.tenant_id=$1 AND f.platform=$2
                    AND f.uid=$3 AND f.funnel_id=$4 AND f.action='start_fresh'), $6))
           LIMIT 1""",
        tenant_id, platform, uid, funnel_id, step_id, since, except_job))


# ── кэш медиа ────────────────────────────────────────────────────────────────

async def media_get(conn, sha: str, bot_id: int, kind: str) -> str | None:
    return await conn.fetchval(
        "SELECT file_id FROM media_cache WHERE sha256=$1 AND bot_id=$2 AND kind=$3", sha, bot_id, kind)


async def media_put(conn, sha: str, bot_id: int, kind: str, file_id: str) -> None:
    await conn.execute(
        """INSERT INTO media_cache (sha256, bot_id, kind, file_id) VALUES ($1,$2,$3,$4)
           ON CONFLICT (sha256, bot_id, kind) DO UPDATE SET file_id=EXCLUDED.file_id""",
        sha, bot_id, kind, file_id)


async def media_drop(conn, sha: str, bot_id: int, kind: str) -> None:
    await conn.execute("DELETE FROM media_cache WHERE sha256=$1 AND bot_id=$2 AND kind=$3",
                       sha, bot_id, kind)
