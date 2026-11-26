"""Публикация воронки в движок – без сборки образа и без рестарта.

    python -m runtime.publish <запуск> <имя воронки в data/funnels Студии>
        [--comment "…"] [--mode new-only|migrate] [--map старый=новый …]
        [--data <папка данных Студии>] [--author имя] [--dry-run]

Контракт для Студии (кнопка «Опубликовать»): источник графа – объект с
протоколом `PublishedSource`, публикация – `publish(pool, tenant_slug, funnel, …)`.
Источники: `StudioVersionSource` – опубликованная (или любая, `имя@N`) версия из
хранилища версий Студии `data/studio.db` – ею пользуется и кнопка Студии, и CLI
по умолчанию; `StudioStorageSource` – черновик (`data/funnels/<имя>.json`,
CLI `--draft`). Публикация:
  1. валидирует граф моделью Студии;
  2. считает отчёт по живым людям: сколько задач ожидает на каждом шаге,
     какие шаги исчезли (по id), у каких поменялось время;
  3. отказывает, если в режиме migrate исчез шаг, на котором ждут люди, а карты
     перенаправления нет (исследование §6.2, правило 78);
  4. пишет новую неизменяемую версию в funnel_versions и шлёт NOTIFY –
     воркер подменяет граф без рестарта;
  5. режим new-only: уже идущие доезжают по своей версии; migrate: задачи
     переносятся на новую версию, время шагов с часами пересчитывается
     (resolve_step_target + skip_if_past), исчезнувшие – по карте.
Откат = публикация прошлой версии тем же путём (новым номером).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

import asyncpg

from studio.model.emptiness import empty_issues
from studio.model.funnel import Funnel

from . import repo
from .clock import Clock
from .db import CH_VERSIONS, create_pool, migrate
from .gates.publish_check import gate_warnings
from .graph import CompiledFunnel, has_schedule
from .planner import PlanCtx, plan
from .settings import TenantSettings


class PublishedSource(Protocol):
    """Откуда берётся граф для публикации."""

    def load(self, name: str) -> Funnel: ...

    def describe(self, name: str) -> str: ...


class StudioStorageSource:
    """Файлы Студии: data/funnels/<запуск>__<воронка>.json."""

    def __init__(self, data_dir: str | Path):
        from studio.server.storage import Storage
        self.storage = Storage(Path(data_dir))

    def load(self, name: str) -> Funnel:
        return self.storage.load(name)

    def describe(self, name: str) -> str:
        return str(self.storage.path(name))


class StudioVersionSource:
    """Хранилище версий Студии (data/studio.db): движок берёт именно опубликованную версию.

    Имя – `<запуск>__<воронка>` (опубликованная сейчас) или `<запуск>__<воронка>@N`
    (конкретная версия N – так публикуется откат). Черновик отсюда не читается.
    """

    def __init__(self, data_dir_or_store):
        from studio.versions.store import VersionStore
        if isinstance(data_dir_or_store, VersionStore):
            self.versions = data_dir_or_store
        elif hasattr(data_dir_or_store, "versions"):      # VersionedStorage Студии
            self.versions = data_dir_or_store.versions
        else:
            self.versions = VersionStore(Path(data_dir_or_store) / "studio.db")

    def resolve(self, name: str) -> tuple[str, int]:
        base, _, num = name.partition("@")
        if num:
            if not num.isdigit():
                raise ValueError(f"неверный номер версии в «{name}»: ожидаем <имя>@N")
            return base, int(num)
        n = self.versions.published_number(base)
        if n is None:
            raise FileNotFoundError(f"у воронки «{base}» в Студии нет опубликованной версии")
        return base, n

    def load(self, name: str) -> Funnel:
        base, n = self.resolve(name)
        return Funnel.model_validate(self.versions.version_graph(base, n))

    def describe(self, name: str) -> str:
        base, n = self.resolve(name)
        return f"studio:{base}@v{n}"


class PublishError(RuntimeError):
    def __init__(self, message: str, report: "PublishReport | None" = None):
        super().__init__(message)
        self.report = report


@dataclass
class StepLine:
    step_id: str
    key: str
    pending: int
    awaiting: int = 0
    status: str = "same"          # same / changed / time / removed
    redirect: str | None = None   # key нового шага


@dataclass
class PublishReport:
    tenant: str
    funnel_id: str
    from_version: int | None
    to_version: int
    mode: str
    positions_active: int = 0
    pending_total: int = 0
    lines: list[StepLine] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    migrated: int = 0
    retimed: int = 0
    skipped_past: int = 0
    dry_run: bool = False

    def to_json(self) -> dict:
        return {k: (v if k != "lines" else [vars(x) for x in v]) for k, v in vars(self).items()}

    def text(self) -> str:
        mode = {"new-only": "только новым – идущие доезжают по своей версии",
                "migrate": "перевести всех на новую версию"}[self.mode]
        head = (f"Публикация воронки «{self.funnel_id}» в запуск «{self.tenant}»: "
                f"v{self.from_version or '–'} → v{self.to_version} ({mode})")
        out = [head,
               f"Сейчас в воронке: {self.positions_active} человек, ожидающих задач – {self.pending_total}."]
        busy = [ln for ln in self.lines if ln.pending or ln.awaiting]
        if busy:
            out.append("На шагах:")
            for ln in busy:
                what = []
                if ln.pending:
                    what.append(f"у {ln.pending} чел. запланирован этот шаг")
                if ln.awaiting:
                    what.append(f"{ln.awaiting} чел. ждут ответа на нём")
                st = {"same": "без изменений", "changed": "изменён",
                      "time": "изменилось время – пересчитаем" if self.mode == "migrate"
                      else "изменилось время (у идущих – старое)",
                      "removed": "УДАЛЁН"}[ln.status]
                if ln.status == "removed" and ln.redirect:
                    st += f" → перенаправлен на «{ln.redirect}»"
                out.append(f"  • {ln.key}: {', '.join(what)} [{st}]")
        for w in self.warnings:
            out.append(f"⚠ {w}")
        for e in self.errors:
            out.append(f"✖ {e}")
        if self.errors:
            out.append("Публикация отклонена.")
        elif self.dry_run:
            out.append("Пробный прогон (--dry-run): ничего не записано.")
        else:
            tail = f"Опубликовано: v{self.to_version}."
            if self.mode == "migrate":
                tail += (f" Перенесено задач: {self.migrated}, время пересчитано: {self.retimed}, "
                         f"пропущено как прошедшее: {self.skipped_past}.")
            out.append(tail)
        return "\n".join(out)


def _resolve_redirects(redirects: dict[str, str], olds: list[CompiledFunnel],
                       new: CompiledFunnel) -> tuple[dict[uuid.UUID, uuid.UUID], list[str]]:
    """Карта «старый шаг → новый шаг». Ключи и значения – key или id шага."""
    out: dict[uuid.UUID, uuid.UUID] = {}
    errs: list[str] = []
    for src, dst in redirects.items():
        src_ids: set[uuid.UUID] = set()
        for g in olds:
            s = g.by_key.get(src) or g.step(src)
            if s is not None:
                src_ids.add(s.id)
        if not src_ids:
            errs.append(f"карта: шага «{src}» нет ни в одной опубликованной версии")
            continue
        t = new.by_key.get(dst) or new.step(dst)
        if t is None:
            errs.append(f"карта: шага «{dst}» нет в новой версии")
            continue
        for sid in src_ids:
            out[sid] = t.id
    return out, errs


def _schedule_sig(step) -> str:
    return json.dumps(step.schedule.model_dump(mode="json"), sort_keys=True)


async def publish(pool: asyncpg.Pool, tenant_slug: str, funnel: Funnel, *, source: str = "",
                  comment: str = "", author: str = "", mode: str = "new-only",
                  redirects: dict[str, str] | None = None, dry_run: bool = False,
                  clock: Clock | None = None) -> PublishReport:
    if mode not in ("new-only", "migrate"):
        raise PublishError(f"неизвестный режим {mode!r}: new-only или migrate")
    funnel = Funnel.model_validate(funnel.model_dump(mode="json", by_alias=True))  # проверка моделью
    if not funnel.steps:
        raise PublishError("в воронке нет шагов")
    clock = clock or Clock()
    now = clock.now()
    async with pool.acquire() as conn:
        async with conn.transaction():
            t = await conn.fetchrow("SELECT * FROM tenants WHERE slug=$1 FOR UPDATE", tenant_slug)
            if t is None:
                raise PublishError(f"запуска «{tenant_slug}» нет – заведите: python -m runtime tenant add")
            tid = t["id"]
            settings = TenantSettings.model_validate(t["settings"] or {})
            cur = await conn.fetchrow(
                """SELECT version, graph FROM funnel_versions WHERE tenant_id=$1 AND funnel_id=$2
                   ORDER BY version DESC LIMIT 1""", tid, funnel.id)
            from_v = cur["version"] if cur else None
            to_v = (from_v or 0) + 1
            new = CompiledFunnel.build(tid, to_v, funnel)
            old = CompiledFunnel.build(tid, from_v, Funnel.model_validate(cur["graph"])) if cur else None
            rep = PublishReport(tenant=tenant_slug, funnel_id=funnel.id, from_version=from_v,
                                to_version=to_v, mode=mode, dry_run=dry_run)

            # версии, на которых сейчас есть люди
            live_versions = [r["version"] for r in await conn.fetch(
                """SELECT DISTINCT version FROM jobs WHERE tenant_id=$1 AND funnel_id=$2
                     AND status IN ('pending','running')
                   UNION SELECT DISTINCT version FROM positions WHERE tenant_id=$1 AND funnel_id=$2
                     AND status='active'""", tid, funnel.id)]
            olds: dict[int, CompiledFunnel] = {}
            for v in live_versions:
                raw = await conn.fetchval("SELECT graph FROM funnel_versions WHERE tenant_id=$1 "
                                          "AND funnel_id=$2 AND version=$3", tid, funnel.id, v)
                if raw is not None:
                    olds[v] = CompiledFunnel.build(tid, v, Funnel.model_validate(raw))
            if old is not None:
                olds.setdefault(old.version, old)

            rmap, rerrs = _resolve_redirects(redirects or {}, list(olds.values()), new)
            rep.errors += rerrs

            pend = await conn.fetch(
                """SELECT version, step_id, count(*) AS n FROM jobs WHERE tenant_id=$1 AND funnel_id=$2
                     AND status='pending' GROUP BY version, step_id""", tid, funnel.id)
            waits = await conn.fetch(
                """SELECT version, awaiting->>'step_id' AS step_id, count(*) AS n FROM positions
                   WHERE tenant_id=$1 AND funnel_id=$2 AND status='active' AND awaiting IS NOT NULL
                   GROUP BY 1, 2""", tid, funnel.id)
            rep.positions_active = await conn.fetchval(
                "SELECT count(*) FROM positions WHERE tenant_id=$1 AND funnel_id=$2 AND status='active'",
                tid, funnel.id)
            agg: dict[uuid.UUID, StepLine] = {}

            def line(sid: uuid.UUID, ver: int) -> StepLine:
                if sid not in agg:
                    g = olds.get(ver)
                    agg[sid] = StepLine(step_id=str(sid), key=g.key_of(sid) if g else str(sid)[:8],
                                        pending=0)
                return agg[sid]

            for r in pend:
                line(r["step_id"], r["version"]).pending += r["n"]
                rep.pending_total += r["n"]
            for r in waits:
                if r["step_id"]:
                    line(uuid.UUID(r["step_id"]), r["version"]).awaiting += r["n"]
            for sid, ln in agg.items():
                ns = new.step(sid)
                if ns is None:
                    ln.status = "removed"
                    if sid in rmap:
                        ln.redirect = new.key_of(rmap[sid])
                    elif mode == "migrate":
                        rep.errors.append(
                            f"шаг «{ln.key}» удалён, а на нём {_who(ln)} – укажите, куда их "
                            f"перенаправить (--map {ln.key}=<новый шаг>), или оставьте шаг "
                            f"надгробием (переход на другой шаг)")
                    else:
                        rep.warnings.append(
                            f"шаг «{ln.key}» удалён, на нём {_who(ln)} – они доедут по своей старой "
                            f"версии (режим «только новым»)")
                    continue
                olds_step = next((g.step(sid) for g in olds.values() if g.step(sid) is not None), None)
                if olds_step is not None:
                    if _schedule_sig(olds_step) != _schedule_sig(ns):
                        ln.status = "time"
                    elif olds_step.model_dump(mode="json") != ns.model_dump(mode="json"):
                        ln.status = "changed"
            rep.lines = sorted(agg.values(), key=lambda x: (-x.pending, x.key))
            # вход воронки должен существовать
            if funnel.branch_router:
                for b in funnel.branch_router.branches:
                    if new.step(b.entry_step) is None:
                        rep.errors.append(f"вход ветки {b.id} ведёт в шаг, которого нет")

            # условия шагов против источников данных запуска (оплата / Bizon / клики)
            rep.warnings += gate_warnings(funnel, settings)
            # пустые шаги: движок их пройдёт, а человеку не уйдёт ничего (живой тест 24.09)
            rep.warnings += [i.text for i in empty_issues(funnel)]
            if rep.errors:
                raise PublishError("публикация отклонена", rep)
            if dry_run:
                return rep

            await conn.execute(
                """INSERT INTO funnel_versions (tenant_id, funnel_id, version, graph, source, mode,
                                                published_at, published_by, comment, report)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                tid, funnel.id, to_v, funnel.model_dump(mode="json", by_alias=True), source, mode,
                now, author, comment, None)
            if mode == "migrate":
                await _migrate(conn, tid, new, olds, rmap, settings, now, rep)
            await conn.execute("UPDATE funnel_versions SET report=$4 WHERE tenant_id=$1 AND "
                               "funnel_id=$2 AND version=$3", tid, funnel.id, to_v, rep.to_json())
            await conn.execute("SELECT pg_notify($1, $2)", CH_VERSIONS, f"{tid}:{funnel.id}:{to_v}")
    return rep


def _who(ln: StepLine) -> str:
    parts = []
    if ln.pending:
        parts.append(f"у {ln.pending} чел. запланирован")
    if ln.awaiting:
        parts.append(f"{ln.awaiting} чел. ждут ответа")
    return ", ".join(parts)


async def _migrate(conn, tid: int, new: CompiledFunnel, olds: dict[int, CompiledFunnel],
                   rmap: dict[uuid.UUID, uuid.UUID], settings: TenantSettings, now: datetime,
                   rep: PublishReport) -> None:
    """Перевод ожидающих задач и позиций на новую версию (в той же транзакции)."""
    # порядок блокировок как у воркера и событий: позиции → задачи
    await conn.execute("SELECT 1 FROM positions WHERE tenant_id=$1 AND funnel_id=$2 FOR UPDATE",
                       tid, new.funnel_id)
    jobs = await conn.fetch(
        """SELECT * FROM jobs WHERE tenant_id=$1 AND funnel_id=$2 AND status='pending'
           ORDER BY id FOR UPDATE""", tid, new.funnel_id)
    people: dict[tuple[str, int], tuple] = {}
    for j in jobs:
        key = (j["platform"], j["uid"])
        if key not in people:
            part = await repo.get_participant(conn, tid, j["platform"], j["uid"])
            pos = await repo.get_position(conn, tid, j["platform"], j["uid"], new.funnel_id)
            people[key] = (part, pos)
        part, pos = people[key]
        target = j["step_id"] if new.step(j["step_id"]) is not None else rmap.get(j["step_id"])
        await conn.execute(
            """UPDATE jobs SET status='cancelled', reason=$2, updated_at=$3, finished_at=$3
               WHERE id=$1""", j["id"], f"перенесена в v{new.version}", now)
        if target is None or part is None or pos is None:
            continue
        remaining = max(0, int((j["run_at"] - now).total_seconds()))
        ctx = PlanCtx(now=now, webinar_at=pos["webinar_at"], entered_at=part["created_at"],
                      branch_id=pos["branch"], dnd=settings.dnd)
        tstep = new.step(target)
        if j["kind"] == "skip" and target == j["step_id"]:
            specs = [repo.JobSpec(step_id=target, step_key=tstep.key, kind="skip", run_at=j["run_at"],
                                  reason=j["reason"])]
        else:
            specs = plan(new, target, remaining, ctx, kind="also" if j["kind"] == "also" else "fire",
                         reason=j["reason"] or "перенос в новую версию")
            if has_schedule(tstep):
                if any(n[0] == "skipped_past" for n in ctx.notes):
                    rep.skipped_past += 1
                elif specs and specs[0].run_at != j["run_at"]:
                    rep.retimed += 1
        await repo.enqueue(conn, tenant_id=tid, platform=j["platform"], uid=j["uid"],
                           funnel_id=new.funnel_id, version=new.version, specs=specs,
                           idem_prefix=f"mig{new.version}:{j['id']}", now=now)
        rep.migrated += 1
    # позиции: версия и то, чего человек ждёт
    for p in await conn.fetch(
            """SELECT * FROM positions WHERE tenant_id=$1 AND funnel_id=$2 AND status='active'
               FOR UPDATE""", tid, new.funnel_id):
        aw = p["awaiting"]
        if aw and aw.get("step_id"):
            sid = uuid.UUID(aw["step_id"])
            if new.step(sid) is None:
                sid = rmap.get(sid)
                aw = None if sid is None else {**aw, "step_id": str(sid), "version": new.version}
            else:
                aw = {**aw, "version": new.version}
        await conn.execute(
            """UPDATE positions SET version=$5, awaiting=$6, updated_at=$7
               WHERE tenant_id=$1 AND platform=$2 AND uid=$3 AND funnel_id=$4""",
            tid, p["platform"], p["uid"], new.funnel_id, new.version, aw, now)


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_map(items: list[str]) -> dict[str, str]:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--map ожидает старый=новый, получено {it!r}")
        a, b = it.split("=", 1)
        out[a.strip()] = b.strip()
    return out


async def _main(argv: list[str] | None = None) -> int:
    from .config import RuntimeConfig
    ap = argparse.ArgumentParser(prog="python -m runtime.publish",
                                 description="Опубликовать воронку Студии в движок (без рестарта)")
    ap.add_argument("tenant", help="slug запуска (python -m runtime tenant list)")
    ap.add_argument("name", help="имя воронки Студии, например webinar__funnel (или webinar__funnel@3 – версия 3)")
    ap.add_argument("--comment", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--mode", choices=["new-only", "migrate"], default="new-only")
    ap.add_argument("--map", action="append", default=[], metavar="СТАРЫЙ=НОВЫЙ",
                    help="куда перенаправить людей с удалённого шага (key или id)")
    ap.add_argument("--data", default=None, help="папка данных Студии (по умолчанию STUDIO_DATA)")
    ap.add_argument("--dry-run", action="store_true", help="только отчёт, ничего не писать")
    ap.add_argument("--draft", action="store_true",
                    help="взять черновик (data/funnels/<имя>.json), а не опубликованную версию Студии")
    a = ap.parse_args(argv)
    cfg = RuntimeConfig()
    data = a.data or cfg.data_dir
    src: PublishedSource
    if a.draft:
        src = StudioStorageSource(data)
    else:
        src = StudioVersionSource(data)
        try:
            src.resolve(a.name)
        except FileNotFoundError:
            if "@" in a.name:
                raise
            # в Студии версии нет – как раньше, черновик, но говорим об этом вслух
            print(f"⚠ у «{a.name}» нет опубликованной версии в Студии – беру черновик "
                  f"(data/funnels/{a.name}.json). Лучше опубликовать в Студии.", file=sys.stderr)
            src = StudioStorageSource(data)
    try:
        funnel = src.load(a.name)
    except (FileNotFoundError, ValueError) as e:
        print(f"нет воронки {a.name}: {e}", file=sys.stderr)
        return 2
    pool = await create_pool(cfg.dsn, max_size=2)
    try:
        await migrate(pool)
        rep = await publish(pool, a.tenant, funnel, source=src.describe(a.name), comment=a.comment,
                            author=a.author, mode=a.mode, redirects=_parse_map(a.map),
                            dry_run=a.dry_run)
        print(rep.text())
        return 0
    except PublishError as e:
        print(e.report.text() if e.report else f"✖ {e}", file=sys.stderr)
        return 1
    finally:
        await pool.close()


def main() -> int:
    from .cli import setup_console
    setup_console()
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())

