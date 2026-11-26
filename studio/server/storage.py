"""Файловое хранилище графов (этап 1).

    data/funnels/<запуск>__<воронка>.json          текущий граф
    data/funnels/.history/<имя>.<метка времени>.json   предыдущие версии (страховка)
    data/media/<sha256>.<ext>                       загруженные файлы

Версии и БД – этап 2. Здесь только «не потерять прошлое сохранение».
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..model.funnel import Funnel

NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+__[A-Za-z0-9_\-]+$")
PART_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class StorageError(ValueError):
    pass


def make_name(launch: str, funnel: str) -> str:
    for part, what in ((launch, "запуск"), (funnel, "воронка")):
        if not PART_RE.match(part or ""):
            raise StorageError(f"{what}: только латиница, цифры, «_» и «-» (получено «{part}»)")
    return f"{launch}__{funnel}"


class Storage:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.funnels = self.root / "funnels"
        self.history = self.funnels / ".history"
        self.media = self.root / "media"
        for d in (self.funnels, self.history, self.media):
            d.mkdir(parents=True, exist_ok=True)

    # ── графы ──
    def path(self, name: str) -> Path:
        if not NAME_RE.match(name):
            raise StorageError(f"неверное имя воронки «{name}»: ожидаем <запуск>__<воронка>")
        return self.funnels / f"{name}.json"

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.funnels.glob("*.json")):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            src = raw.get("source") or {}
            out.append({
                "name": p.stem,
                "id": raw.get("id", ""),
                "title": raw.get("title", ""),
                "launch": src.get("launch", p.stem.split("__")[0]),
                "source_path": src.get("path", ""),
                "steps": len(raw.get("steps", [])),
                "edges": len(raw.get("edges", [])),
                "updated_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(),
            })
        return out

    def load(self, name: str) -> Funnel:
        p = self.path(name)
        if not p.exists():
            raise FileNotFoundError(name)
        return Funnel.model_validate_json(p.read_text(encoding="utf-8"))

    def load_raw(self, name: str) -> dict:
        p = self.path(name)
        if not p.exists():
            raise FileNotFoundError(name)
        return json.loads(p.read_text(encoding="utf-8"))

    def save(self, name: str, f: Funnel) -> Path:
        p = self.path(name)
        if p.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            (self.history / f"{name}.{stamp}.json").write_bytes(p.read_bytes())
        text = json.dumps(f.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=1)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(text + "\n", encoding="utf-8", newline="\n")
        tmp.replace(p)
        return p

    def history_of(self, name: str) -> list[Path]:
        return sorted(self.history.glob(f"{name}.*.json"))
