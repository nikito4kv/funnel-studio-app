"""Воркер очереди шагов.

Цикл: взять просроченные задачи `SELECT … FOR UPDATE SKIP LOCKED`, отдать их
исполнителю под семафором запуска (по умолчанию 8 шагов одного запуска
одновременно) и общим семафором по числу соединений пула. Пачка из 600
таймеров, проснувшихся в одну минуту, стоит в очереди семафора, а не
выбирает пул до дна (авария 22.09.2026).

Аренда (lease): взятая задача помечается running до `lease_until`; пока процесс
жив, аренда продлевается. Упал процесс посреди шага – через lease_seconds
задачу возьмёт другой (или тот же после рестарта) и выполнит заново;
отправленное сообщение второй раз не уйдёт (sent_messages по задаче).
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import timedelta
from typing import TYPE_CHECKING

from . import repo
from .executor import LeaseLost

if TYPE_CHECKING:
    from .service import Runtime

log = logging.getLogger("runtime.worker")

MAX_ATTEMPTS = 8
IDLE_MAX_SLEEP_S = 5.0


class Worker:
    def __init__(self, rt: "Runtime", name: str | None = None):
        self.rt = rt
        self.name = name or f"{socket.gethostname()}:{os.getpid()}:{id(self) & 0xffff:x}"
        self.held: set[int] = set()
        self._tasks: set[asyncio.Task] = set()
        self.stats = {"done": 0, "failed": 0, "retried": 0, "max_parallel": 0}
        self._running = 0

    # ── одна пачка (тесты и цикл) ────────────────────────────────────────
    async def claim(self) -> list:
        rt = self.rt
        cap = rt.cfg.max_held - len(self.held)
        if cap <= 0:
            return []
        now = rt.clock.now()
        async with rt.pool.acquire() as conn:
            rows = await repo.claim_due(conn, now=now, limit=cap,
                                        lease_until=now + timedelta(seconds=rt.cfg.lease_seconds),
                                        worker=self.name)
        for r in rows:
            self.held.add(r["id"])
        return rows

    async def tick(self) -> int:
        """Берёт всё просроченное (до max_held) и дожидается выполнения. → сколько взято."""
        rows = await self.claim()
        if rows:
            await asyncio.gather(*(self._run(r) for r in rows))
        return len(rows)

    async def _run(self, job) -> None:
        rt = self.rt
        try:
            async with rt.tenant_sem(job["tenant_id"]):
                async with rt.global_sem:
                    self._running += 1
                    self.stats["max_parallel"] = max(self.stats["max_parallel"], self._running)
                    try:
                        status = await rt.executor.execute_job(job)
                    finally:
                        self._running -= 1
            self.stats["done" if status == "done" else "failed"] += 1
        except LeaseLost:
            log.warning("задачу %s забрал другой процесс (аренда истекла) – итог не пишем", job["id"])
        except Exception as e:  # noqa: BLE001 – инфраструктура (база и т.п.): повторим позже
            await self._retry(job, e)
        finally:
            self.held.discard(job["id"])

    async def _retry(self, job, err: Exception) -> None:
        rt = self.rt
        now = rt.clock.now()
        attempts = job["attempts"]
        final = attempts >= MAX_ATTEMPTS
        delay = min(15 * 2 ** max(0, attempts - 1), 600)
        log.exception("задача %s (%s, шаг %s) упала на попытке %s: %s – %s", job["id"],
                      job["uid"], job["step_key"], attempts, err,
                      "больше не повторяем" if final else f"повтор через {delay} с")
        try:
            async with rt.pool.acquire() as conn:
                await conn.execute(
                    """UPDATE jobs SET status=$2, run_at=$3, reason=$4, lease_until=NULL,
                              updated_at=$5, finished_at=CASE WHEN $2='failed' THEN $5 END
                       WHERE id=$1 AND status='running' AND locked_by=$6""",
                    job["id"], "failed" if final else "pending", now + timedelta(seconds=delay),
                    f"{type(err).__name__}: {err}"[:500], now, self.name)
            self.stats["failed" if final else "retried"] += 1
        except Exception:  # noqa: BLE001 – база недоступна: задачу вернёт истёкшая аренда
            log.exception("не смогли вернуть задачу %s в очередь – её вернёт истёкшая аренда",
                          job["id"])

    # ── боевой цикл ──────────────────────────────────────────────────────
    async def _heartbeat(self, stop: asyncio.Event) -> None:
        period = max(1.0, self.rt.cfg.lease_seconds / 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=period)
            except asyncio.TimeoutError:
                pass
            if not self.held:
                continue
            try:
                async with self.rt.pool.acquire() as conn:
                    await repo.renew_leases(
                        conn, list(self.held), worker=self.name,
                        lease_until=self.rt.clock.now() + timedelta(seconds=self.rt.cfg.lease_seconds))
            except Exception:  # noqa: BLE001
                log.exception("продление аренды не удалось")

    async def run_forever(self, stop: asyncio.Event) -> None:
        log.info("воркер %s запущен", self.name)
        hb = asyncio.create_task(self._heartbeat(stop))
        try:
            while not stop.is_set():
                self.rt._wake.clear()
                try:
                    rows = await self.claim()
                except Exception:  # noqa: BLE001
                    log.exception("выборка задач не удалась – пауза")
                    rows = []
                    await asyncio.sleep(2)
                for r in rows:
                    t = asyncio.create_task(self._run(r))
                    self._tasks.add(t)
                    t.add_done_callback(self._tasks.discard)
                if rows and len(self.held) < self.rt.cfg.max_held:
                    continue
                sleep = IDLE_MAX_SLEEP_S
                try:
                    async with self.rt.pool.acquire() as conn:
                        nxt = await repo.next_run_at(conn)
                    if nxt is not None:
                        sleep = max(0.05, min(IDLE_MAX_SLEEP_S,
                                              (nxt - self.rt.clock.now()).total_seconds()))
                except Exception:  # noqa: BLE001
                    pass
                waits = [asyncio.create_task(self.rt._wake.wait()), asyncio.create_task(stop.wait())]
                done, pending = await asyncio.wait(waits, timeout=sleep,
                                                   return_when=asyncio.FIRST_COMPLETED)
                for p in pending:
                    p.cancel()
        finally:
            if self._tasks:
                log.info("останавливаемся: ждём %s шагов в работе", len(self._tasks))
                await asyncio.gather(*self._tasks, return_exceptions=True)
            stop.set()
            await hb
            log.info("воркер %s остановлен", self.name)
