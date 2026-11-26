"""Публикация версий Студии в движок (этап 3, пункт «Опубликовать → runtime»).

Студия знает адрес базы движка из `STUDIO_RUNTIME_DSN`. Нет адреса – публикация
работает как на этапе 2 (только хранилище Студии), а диалог честно пишет
«движок не подключён».

Что здесь:
  * `EngineLink` – разговор с базой движка: список запусков (`tenants`), что
    сейчас опубликовано у воронки в запуске, пробная публикация (`dry_run`) с
    отчётом по живым людям и настоящая публикация – через тот же
    `runtime.publish.publish(...)`, что и CLI;
  * `EngineRecords` – в `data/studio.db`: какой запуск выбран у воронки
    (запоминается) и журнал отправок версий в движок – в какой запуск, каким
    режимом, какой версией движка, удачно или нет. Таблица `versions` Студии
    неизменяема (триггеры), поэтому «ушла ли версия в движок» – отдельный журнал:
    отказ движка не делает версию «опубликованной в движке».
  * `suggest_redirects` – куда по умолчанию вести людей с исчезнувших шагов
    (подставляется в диалог, человек может поправить).

Движок асинхронный (asyncpg), сервер Студии – синхронный: каждая операция
открывает короткий пул на своём цикле событий (`asyncio.run` в потоке запроса).
Публикации редки – так проще и не держим соединения к базе движка.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlsplit

from ..model.funnel import Funnel
from .store import VersionStore, now_iso

ENGINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_targets (
    name        TEXT PRIMARY KEY,          -- воронка Студии <запуск>__<воронка>
    tenant      TEXT NOT NULL,             -- slug запуска в движке
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS engine_publishes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    number          INTEGER NOT NULL,      -- версия Студии
    tenant          TEXT NOT NULL,
    funnel_id       TEXT NOT NULL,
    mode            TEXT NOT NULL,         -- new-only / migrate
    status          TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
    engine_version  INTEGER,               -- номер версии в движке (NULL – не записана)
    author          TEXT NOT NULL DEFAULT '',
    error           TEXT NOT NULL DEFAULT '',
    redirects       TEXT NOT NULL DEFAULT '{}',
    report          TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS engine_publishes_name ON engine_publishes(name, number);
"""

MODES = ("new-only", "migrate")

_CLI_MAP = re.compile(r" \(--map [^)]*\)")
_HUMAN = (
    ("или оставьте шаг надгробием (переход на другой шаг)", "или верните шаг переходом на другой шаг"),
    ("надгробием", "переходом"),
)
_USERNAME = re.compile(r"^\s*username:\s*[\"']?@?([A-Za-z0-9_]+)", re.M)


def human(text: str | None) -> str | None:
    """Текст отказа движка – для диалога: подсказки CLI («--map X=…») заменяем на «карта ниже»,
    служебные слова – человеческими."""
    if not text:
        return text
    text = _CLI_MAP.sub(" (карта – ниже)", text)
    for a, b in _HUMAN:
        text = text.replace(a, b)
    return text


def repo_usernames(repo_root) -> set[str]:
    """Боты боевых запусков bot-engine (`launches/*/launch.yaml`, `username:`) – в нижнем регистре."""
    out: set[str] = set()
    try:
        for p in (repo_root / "launches").glob("*/launch.yaml"):
            try:
                out |= {m.lower() for m in _USERNAME.findall(p.read_text(encoding="utf-8"))}
            except OSError:
                continue
    except (OSError, TypeError):
        pass
    return out


def is_test_bot(username: str | None, secret_ref: str, combat: set[str]) -> bool:
    """Тестовый бот – не из боевых запусков bot-engine и помечен как тестовый (имя или ссылка на токен)."""
    u = (username or "").lower()
    if u and u in combat:
        return False
    return "test" in u or secret_ref.upper().startswith("ENV:STUDIO_TEST")


class EngineUnavailable(RuntimeError):
    """Движок не подключён или база не отвечает."""


def where(dsn: str) -> str:
    """Адрес базы для глаз: хост:порт/база, без пароля."""
    try:
        u = urlsplit(dsn)
        return f"{u.hostname or '?'}:{u.port or 5432}{u.path or ''}"
    except ValueError:
        return "?"


# ── журнал в data/studio.db ─────────────────────────────────────────────────

class EngineRecords:
    def __init__(self, versions: VersionStore):
        self.versions = versions
        with self.db() as c:
            c.executescript(ENGINE_SCHEMA)

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        with self.versions.db() as c:
            yield c

    def target(self, name: str) -> str | None:
        with self.db() as c:
            r = c.execute("SELECT tenant FROM engine_targets WHERE name = ?", (name,)).fetchone()
        return r["tenant"] if r else None

    def set_target(self, name: str, tenant: str) -> None:
        with self.db() as c:
            c.execute("INSERT INTO engine_targets (name, tenant, updated_at) VALUES (?, ?, ?) "
                      "ON CONFLICT(name) DO UPDATE SET tenant = excluded.tenant, updated_at = excluded.updated_at",
                      (name, tenant, now_iso()))

    def add(self, name: str, number: int, *, tenant: str, funnel_id: str, mode: str, ok: bool,
            engine_version: int | None, author: str, error: str = "", redirects: dict | None = None,
            report: dict | None = None) -> dict:
        t = now_iso()
        with self.db() as c:
            cur = c.execute(
                "INSERT INTO engine_publishes (name, number, tenant, funnel_id, mode, status, engine_version, "
                "author, error, redirects, report, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, number, tenant, funnel_id, mode, "ok" if ok else "failed", engine_version, author,
                 error, json.dumps(redirects or {}, ensure_ascii=False),
                 json.dumps(report or {}, ensure_ascii=False), t))
            rid = cur.lastrowid
        return self.row(rid)

    def row(self, rid: int) -> dict:
        with self.db() as c:
            r = c.execute("SELECT * FROM engine_publishes WHERE id = ?", (rid,)).fetchone()
        return _row(r)

    def history(self, name: str) -> list[dict]:
        """Отправки в движок, новые сверху."""
        with self.db() as c:
            rows = c.execute("SELECT * FROM engine_publishes WHERE name = ? ORDER BY id DESC", (name,)).fetchall()
        return [_row(r) for r in rows]

    def by_number(self, name: str) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        for r in reversed(self.history(name)):          # по порядку отправок
            out.setdefault(r["number"], []).append(r)
        return out


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["redirects"] = json.loads(d["redirects"] or "{}")
    rep = json.loads(d["report"] or "{}")
    # в журнал истории – только сводка: полный отчёт длинный
    d["report"] = {k: rep.get(k) for k in ("positions_active", "pending_total", "migrated", "retimed",
                                           "skipped_past", "from_version") if k in rep}
    d["ok"] = d["status"] == "ok"
    return d


# ── куда вести людей с исчезнувших шагов ────────────────────────────────────

def suggest_redirects(removed: list[str], old_graphs: list[dict], new: dict,
                      studio_graphs: list[dict] | None = None) -> dict[str, str]:
    """{id исчезнувшего шага: id шага новой версии}.

    По порядку: 1) в новой версии есть шаг с тем же key – туда; 2) в какой-то
    версии Студии шаг был надгробием – туда, куда вело надгробие; 3) идём по
    стрелкам старого графа («дальше» и переходам) до первого шага, который есть
    в новой версии и не надгробие.
    """
    new_steps = {s["id"]: s for s in new.get("steps", [])}
    live = {sid for sid, s in new_steps.items() if s.get("type") != "jump"}
    by_key = {s["key"]: s["id"] for s in new_steps.values() if s["id"] in live}
    out: dict[str, str] = {}
    for sid in removed:
        old = next((g for g in old_graphs if any(s["id"] == sid for s in g.get("steps", []))), None)
        key = next((s["key"] for s in (old or {}).get("steps", []) if s["id"] == sid), None)
        if key and key in by_key:
            out[sid] = by_key[key]
            continue
        tomb = None
        for g in studio_graphs or []:
            st = next((s for s in g.get("steps", []) if s["id"] == sid), None)
            if st and st.get("type") == "jump":
                tomb = next((e["to_step"] for e in g.get("edges", [])
                             if e["from_step"] == sid and e["kind"] == "jump" and e.get("to_step") in live), None)
                if tomb:
                    break
        if tomb:
            out[sid] = tomb
            continue
        if old is None:
            continue
        edges = old.get("edges", [])
        seen, queue = {sid}, [sid]
        found = None
        while queue and found is None:
            cur = queue.pop(0)
            for e in sorted((e for e in edges if e["from_step"] == cur and e["kind"] in ("done", "jump")
                             and e.get("to_step")), key=lambda e: (e["kind"] != "done", e.get("delay_seconds") or 0)):
                t = e["to_step"]
                if t in seen:
                    continue
                seen.add(t)
                if t in live:
                    found = t
                    break
                queue.append(t)
        if found:
            out[sid] = found
    return out


# ── разговор с базой движка ─────────────────────────────────────────────────

def _run(coro):
    """Асинхронную операцию движка – на своём цикле (вызывается из потока запроса)."""
    return asyncio.run(coro)


class EngineLink:
    def __init__(self, dsn: str | None, repo_root=None):
        self.dsn = (dsn or "").strip() or None
        self.repo_root = repo_root
        self._combat: set[str] | None = None

    def combat(self) -> set[str]:
        if self._combat is None:
            self._combat = repo_usernames(self.repo_root) if self.repo_root is not None else set()
        return self._combat

    @property
    def configured(self) -> bool:
        return self.dsn is not None

    def where(self) -> str:
        return where(self.dsn) if self.dsn else ""

    async def _pool(self):
        if not self.dsn:
            raise EngineUnavailable("движок не подключён: у сервера Студии не задан STUDIO_RUNTIME_DSN")
        import asyncpg

        from runtime.db import _init_conn
        try:
            return await asyncpg.create_pool(self.dsn, min_size=1, max_size=2, init=_init_conn,
                                             command_timeout=30, timeout=5)
        except Exception as e:  # noqa: BLE001 – любая причина = «не отвечает»
            raise EngineUnavailable(f"база движка {self.where()} не отвечает: {type(e).__name__}: {e}") from e

    # ── чтение ──
    def tenants(self) -> list[dict]:
        """Запуски движка: боты (username, тестовый ли), стартовая воронка (куда ведёт /start)."""
        combat = self.combat()

        async def go():
            pool = await self._pool()
            try:
                async with pool.acquire() as c:
                    try:
                        rows = await c.fetch("SELECT id, slug, title, status, settings FROM tenants ORDER BY slug")
                    except Exception as e:  # noqa: BLE001
                        raise EngineUnavailable(f"в базе {self.where()} нет таблиц движка – запустите "
                                                f"движок хотя бы раз: {e}") from e
                    bots = await c.fetch(
                        """SELECT b.tenant_id, b.platform, b.username, b.secret_ref, b.enabled FROM bots b
                           UNION SELECT bt.tenant_id, b.platform, b.username, b.secret_ref, b.enabled
                           FROM bot_tenants bt JOIN bots b ON b.id = bt.bot_id
                           ORDER BY 1, 2""")
                    funnels = await c.fetch("SELECT DISTINCT tenant_id, funnel_id FROM funnel_versions "
                                            "ORDER BY tenant_id, funnel_id")
                out = []
                for r in rows:
                    settings = r["settings"] or {}
                    published = [f["funnel_id"] for f in funnels if f["tenant_id"] == r["id"]]
                    tb = [{"platform": b["platform"], "username": b["username"] or "",
                           "enabled": b["enabled"],
                           "test": is_test_bot(b["username"], b["secret_ref"] or "", combat)}
                          for b in bots if b["tenant_id"] == r["id"]]
                    out.append({
                        "slug": r["slug"], "title": r["title"], "status": r["status"], "bots": tb,
                        # как registry.entry_funnel_id движка: явная настройка или первая по имени
                        "entry_funnel": settings.get("entry_funnel") or (published[0] if published else None),
                        "entry_explicit": bool(settings.get("entry_funnel")),
                        "restart_policy": settings.get("restart_policy") or "engine",
                        "funnels": published,
                    })
                return out
            finally:
                await pool.close()
        return _run(go())

    def set_entry(self, tenant: str, funnel_id: str) -> None:
        """Сделать воронку стартовой: /start бота ведёт в неё (`settings.entry_funnel`).
        Движок перечитывает настройки без рестарта (NOTIFY fs_tenants, как `tenant set`)."""
        async def go():
            pool = await self._pool()
            try:
                async with pool.acquire() as c:
                    r = await c.execute(
                        """UPDATE tenants SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb),
                                  '{entry_funnel}', to_jsonb($2::text)), updated_at = now()
                           WHERE slug = $1""", tenant, funnel_id)
                    if r.endswith(" 0"):
                        raise EngineUnavailable(f"запуска «{tenant}» нет")
                    await c.execute("SELECT pg_notify('fs_tenants', $1)", tenant)
            finally:
                await pool.close()
        _run(go())

    def state(self, tenant: str, funnel_id: str) -> dict:
        """Что сейчас в движке у воронки: версии (новые сверху), сколько людей внутри."""
        async def go():
            pool = await self._pool()
            try:
                async with pool.acquire() as c:
                    tid = await c.fetchval("SELECT id FROM tenants WHERE slug = $1", tenant)
                    if tid is None:
                        return {"tenant_exists": False, "versions": [], "active": 0, "pending": 0}
                    rows = await c.fetch(
                        """SELECT version, source, mode, published_at, published_by, comment
                           FROM funnel_versions WHERE tenant_id=$1 AND funnel_id=$2
                           ORDER BY version DESC LIMIT 10""", tid, funnel_id)
                    active = await c.fetchval("SELECT count(*) FROM positions WHERE tenant_id=$1 AND "
                                              "funnel_id=$2 AND status='active'", tid, funnel_id)
                    pending = await c.fetchval("SELECT count(*) FROM jobs WHERE tenant_id=$1 AND "
                                               "funnel_id=$2 AND status='pending'", tid, funnel_id)
                return {"tenant_exists": True, "active": active, "pending": pending,
                        "versions": [{**dict(r), "published_at": r["published_at"].isoformat()} for r in rows]}
            finally:
                await pool.close()
        return _run(go())

    # ── пробная и настоящая публикация ──
    def check(self, tenant: str, funnel: Funnel, *, mode: str = "new-only",
              redirects: dict[str, str] | None = None, studio_graphs: list[dict] | None = None) -> dict:
        """Пробная публикация (dry_run): отчёт по живым людям, отказ движка текстом, подсказки карты."""
        from runtime.publish import PublishError, publish

        async def go():
            pool = await self._pool()
            try:
                error = None
                try:
                    rep = await publish(pool, tenant, funnel, mode=mode, redirects=redirects or {},
                                        dry_run=True)
                except PublishError as e:
                    rep = e.report
                    error = human("; ".join(rep.errors) if rep and rep.errors else str(e))
                async with pool.acquire() as c:
                    tid = await c.fetchval("SELECT id FROM tenants WHERE slug = $1", tenant)
                    kinds: dict[str, dict[str, int]] = {}
                    graphs: list[dict] = []
                    if tid is not None:
                        for r in await c.fetch(
                                """SELECT awaiting->>'step_id' AS sid, awaiting->>'kind' AS kind, count(*) AS n
                                   FROM positions WHERE tenant_id=$1 AND funnel_id=$2 AND status='active'
                                     AND awaiting IS NOT NULL GROUP BY 1, 2""", tid, funnel.id):
                            if r["sid"]:
                                kinds.setdefault(r["sid"], {})[r["kind"] or "?"] = r["n"]
                        graphs = [r["graph"] for r in await c.fetch(
                            "SELECT graph FROM funnel_versions WHERE tenant_id=$1 AND funnel_id=$2 "
                            "ORDER BY version DESC", tid, funnel.id)]
            finally:
                await pool.close()
            return rep, error, kinds, graphs

        rep, error, kinds, graphs = _run(go())
        new = funnel.model_dump(mode="json", by_alias=True)
        data = rep.to_json() if rep else None
        people = []
        removed: list[str] = []
        if data:
            for ln in data["lines"]:
                ln["awaiting_kinds"] = kinds.get(ln["step_id"], {})
                if ln["status"] == "removed":
                    removed.append(ln["step_id"])
                people.append(ln)
        suggestions = suggest_redirects(removed, graphs, new, studio_graphs)
        return {
            "ok": error is None,
            "error": error,
            "tenant": tenant,
            "funnel_id": funnel.id,
            "mode": mode,
            "report": data,
            "text": human(rep.text()) if rep else (error or ""),
            "suggestions": suggestions,
        }

    def publish(self, tenant: str, funnel: Funnel, *, mode: str, redirects: dict[str, str] | None,
                comment: str, author: str, source: str) -> dict:
        """Настоящая публикация. → отчёт; отказ – PublishError (как у CLI)."""
        from runtime.publish import publish

        async def go():
            pool = await self._pool()
            try:
                rep = await publish(pool, tenant, funnel, source=source, comment=comment, author=author,
                                    mode=mode, redirects=redirects or {})
                return rep
            finally:
                await pool.close()

        rep = _run(go())
        return {"report": rep.to_json(), "text": rep.text(), "version": rep.to_version}


def graph_to_funnel(raw: dict) -> Funnel:
    return Funnel.model_validate(raw)


def clean_redirects(redirects: dict[str, Any] | None) -> dict[str, str]:
    return {str(k): str(v) for k, v in (redirects or {}).items() if k and v}
