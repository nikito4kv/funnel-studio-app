"""Черновики и версии воронок в SQLite (`data/studio.db`, stdlib sqlite3).

У воронки:
  * один ЧЕРНОВИК – изменяемый, его пишет «Сохранить» редактора;
  * неизменяемые ВЕРСИИ v1, v2, … – номер, статус (published / archived), автор,
    комментарий, время (UTC), родитель (версия, от которой шёл черновик) и
    граф целиком. Изменить у версии можно только статус – остальное запрещают
    триггеры базы.

«Опубликовать» = черновик → новая версия «опубликована», прежняя опубликованная →
«архив». «Откатить к vN» = черновик становится копией vN (заметки черновика
остаются: слой заметок к версии не относится).

`VersionedStorage` – замена файлового `Storage` с тем же интерфейсом: источник
правды для черновика – база, а `data/funnels/<имя>.json` (+ `.history/`)
остаётся копией для глаз и страховкой. При старте все `data/funnels/*.json`,
которых ещё нет в базе, переносятся в черновики; файлы не удаляются.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..model.funnel import Funnel
from ..server.storage import Storage, StorageError

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    name          TEXT PRIMARY KEY,
    graph         TEXT NOT NULL,
    base_version  INTEGER,                 -- от какой версии идёт черновик (NULL – ни от какой)
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    saves         INTEGER NOT NULL DEFAULT 0,
    migrated_from TEXT                     -- файл, из которого черновик перенесён при старте
);
CREATE TABLE IF NOT EXISTS versions (
    name        TEXT NOT NULL,
    number      INTEGER NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('published', 'archived')),
    author      TEXT NOT NULL DEFAULT '',
    comment     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    parent      INTEGER,
    graph       TEXT NOT NULL,
    export_dir  TEXT NOT NULL DEFAULT '',
    report      TEXT NOT NULL DEFAULT '{}', -- сводка диффа и круговой проверки на момент публикации
    PRIMARY KEY (name, number)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_published ON versions(name) WHERE status = 'published';
CREATE TRIGGER IF NOT EXISTS versions_immutable
BEFORE UPDATE OF name, number, author, comment, created_at, parent, graph, export_dir, report ON versions
BEGIN
    SELECT RAISE(ABORT, 'версии неизменяемы: менять можно только статус');
END;
CREATE TRIGGER IF NOT EXISTS versions_status_forward
BEFORE UPDATE OF status ON versions
WHEN OLD.status = 'archived' AND NEW.status = 'published'
BEGIN
    SELECT RAISE(ABORT, 'архивная версия не возвращается в опубликованные – публикуется новая');
END;
CREATE TRIGGER IF NOT EXISTS versions_no_delete
BEFORE DELETE ON versions
BEGIN
    SELECT RAISE(ABORT, 'версии не удаляются');
END;
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dump(f: Funnel | dict) -> str:
    raw = f.model_dump(mode="json", by_alias=True) if isinstance(f, Funnel) else f
    return json.dumps(raw, ensure_ascii=False)


class VersionError(ValueError):
    pass


class VersionStore:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.db() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            with c:
                yield c
        finally:
            c.close()

    # ── черновик ──
    def has_draft(self, name: str) -> bool:
        with self.db() as c:
            return c.execute("SELECT 1 FROM drafts WHERE name = ?", (name,)).fetchone() is not None

    def draft_row(self, name: str) -> sqlite3.Row | None:
        with self.db() as c:
            return c.execute("SELECT * FROM drafts WHERE name = ?", (name,)).fetchone()

    def draft(self, name: str) -> dict:
        row = self.draft_row(name)
        if row is None:
            raise FileNotFoundError(name)
        return json.loads(row["graph"])

    def put_draft(self, name: str, graph: Funnel | dict, *, migrated_from: str | None = None,
                  base_version: int | None | str = "keep") -> None:
        text = dump(graph)
        t = now_iso()
        with self.lock, self.db() as c:
            old = c.execute("SELECT base_version FROM drafts WHERE name = ?", (name,)).fetchone()
            if old is None:
                base = None if base_version == "keep" else base_version
                c.execute("INSERT INTO drafts (name, graph, base_version, created_at, updated_at, saves, "
                          "migrated_from) VALUES (?, ?, ?, ?, ?, 0, ?)", (name, text, base, t, t, migrated_from))
            else:
                base = old["base_version"] if base_version == "keep" else base_version
                c.execute("UPDATE drafts SET graph = ?, base_version = ?, updated_at = ?, saves = saves + 1 "
                          "WHERE name = ?", (text, base, t, name))

    def draft_names(self) -> list[str]:
        with self.db() as c:
            return [r[0] for r in c.execute("SELECT name FROM drafts ORDER BY name")]

    # ── версии ──
    def versions(self, name: str) -> list[dict]:
        with self.db() as c:
            rows = c.execute("SELECT name, number, status, author, comment, created_at, parent, export_dir, "
                             "report FROM versions WHERE name = ? ORDER BY number DESC", (name,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["report"] = json.loads(d["report"] or "{}")
            out.append(d)
        return out

    def version_graph(self, name: str, number: int) -> dict:
        with self.db() as c:
            r = c.execute("SELECT graph FROM versions WHERE name = ? AND number = ?", (name, number)).fetchone()
        if r is None:
            raise FileNotFoundError(f"{name} v{number}")
        return json.loads(r["graph"])

    def published_number(self, name: str) -> int | None:
        with self.db() as c:
            r = c.execute("SELECT number FROM versions WHERE name = ? AND status = 'published'", (name,)).fetchone()
        return r["number"] if r else None

    def next_number(self, name: str) -> int:
        with self.db() as c:
            r = c.execute("SELECT COALESCE(MAX(number), 0) FROM versions WHERE name = ?", (name,)).fetchone()
        return r[0] + 1

    def publish(self, name: str, *, author: str, comment: str, export_dir: str = "",
                report: dict | None = None, number: int | None = None, graph: dict | None = None) -> int:
        """Черновик → новая опубликованная версия; прежняя опубликованная → архив. Атомарно."""
        with self.lock, self.db() as c:
            d = c.execute("SELECT graph, base_version FROM drafts WHERE name = ?", (name,)).fetchone()
            if d is None:
                raise FileNotFoundError(name)
            n = c.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM versions WHERE name = ?", (name,)).fetchone()[0]
            if number is not None and number != n:
                raise VersionError(f"пока готовили публикацию, появилась v{n - 1} – откройте диалог заново")
            text = dump(graph) if graph is not None else d["graph"]
            c.execute("UPDATE versions SET status = 'archived' WHERE name = ? AND status = 'published'", (name,))
            c.execute("INSERT INTO versions (name, number, status, author, comment, created_at, parent, graph, "
                      "export_dir, report) VALUES (?, ?, 'published', ?, ?, ?, ?, ?, ?, ?)",
                      (name, n, author, comment, now_iso(), d["base_version"], text, export_dir,
                       json.dumps(report or {}, ensure_ascii=False)))
            c.execute("UPDATE drafts SET base_version = ? WHERE name = ?", (n, name))
            return n


def keep_notes(version_graph: dict, draft_graph: dict) -> dict:
    """Откат: смысл и раскладка – из версии, слой заметок – из черновика.

    Заметка на карточке, которой в версии нет, становится свободной на прежнем месте."""
    g = dict(version_graph)
    ids = {s["id"] for s in g.get("steps", [])}
    pos = ((draft_graph.get("layout") or {}).get("positions")) or {}
    notes = []
    for n in draft_graph.get("notes", []):
        n = dict(n)
        sid = n.get("step_id")
        if sid and sid not in ids:
            p = pos.get(sid) or {"x": 0, "y": 0}
            n["step_id"] = None
            n["position"] = n.get("position") or {"x": p["x"] + 330, "y": p["y"]}
        notes.append(n)
    g["notes"] = notes
    return g


class VersionedStorage(Storage):
    """Хранилище графов поверх базы версий. Интерфейс – как у файлового `Storage`."""

    def __init__(self, root: Path):
        super().__init__(root)
        self.versions = VersionStore(self.root / "studio.db")
        self.migrated: list[str] = self.migrate_files()

    def migrate_files(self) -> list[str]:
        """data/funnels/*.json → черновики (только те, которых в базе ещё нет)."""
        done = []
        known = set(self.versions.draft_names())
        for p in sorted(self.funnels.glob("*.json")):
            if p.stem in known:
                continue
            try:
                self.path(p.stem)
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (StorageError, OSError, ValueError):
                continue
            self.versions.put_draft(p.stem, raw, migrated_from=str(p.relative_to(self.root).as_posix()))
            done.append(p.stem)
        return done

    def exists(self, name: str) -> bool:
        self.path(name)
        return self.versions.has_draft(name)

    def load_raw(self, name: str) -> dict:
        self.path(name)
        return self.versions.draft(name)

    def load(self, name: str) -> Funnel:
        return Funnel.model_validate(self.load_raw(name))

    def save(self, name: str, f: Funnel, **kw: Any) -> Path:
        p = super().save(name, f)          # копия для глаз + .history
        self.versions.put_draft(name, f, **kw)
        return p

    def list(self) -> list[dict]:
        out = []
        with self.versions.db() as c:
            pub = {r["name"]: r["number"] for r in
                   c.execute("SELECT name, number FROM versions WHERE status = 'published'")}
            rows = c.execute("SELECT name, graph, updated_at, base_version FROM drafts ORDER BY name").fetchall()
        for r in rows:
            try:
                raw = json.loads(r["graph"])
            except ValueError:
                continue
            src = raw.get("source") or {}
            out.append({
                "name": r["name"],
                "id": raw.get("id", ""),
                "title": raw.get("title", ""),
                "launch": src.get("launch", r["name"].split("__")[0]),
                "source_path": src.get("path", ""),
                "steps": len(raw.get("steps", [])),
                "edges": len(raw.get("edges", [])),
                "updated_at": r["updated_at"],
                "published": pub.get(r["name"]),
            })
        return out
