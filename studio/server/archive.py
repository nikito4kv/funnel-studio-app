"""Медиа при выгрузке: какие загруженные в Студию файлы нужны воронке и архив «yaml + файлы».

Шаг ссылается на загруженный файл как `./static/<sha256>.<ext>` – движок ищет его в
`launches/<запуск>/static/`. Пока публикация не передаёт файлы сама (этапы 2–3), выгрузка
показывает список «эти файлы положить в static запуска» и отдаёт zip:

    <воронка>.yaml
    static/<sha256>.<ext>      – только файлы, загруженные в Студию
    КАК_ВЫЛОЖИТЬ.txt           – куда что положить

Файлы, которые уже лежат в static запуска в bot-engine, в архив не кладём – они на месте.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from ..model.funnel import Funnel

PREFIX = "./static/"


def _step_refs(f: Funnel) -> list[tuple[str, str]]:
    """(ref, key шага) для всех медиа-полей всех шагов."""
    out: list[tuple[str, str]] = []
    for s in f.steps:
        m = s.media
        refs = [m.image, m.video_note, m.video_fallback, m.video, m.animation, m.rich, *(m.images or [])]
        out.extend((r, s.key) for r in refs if isinstance(r, str) and r.startswith(PREFIX))
    return out


def media_refs(f: Funnel, media_dir: Path) -> list[dict]:
    """Загруженные в Студию файлы, на которые ссылается воронка (по одному на файл)."""
    by_name: dict[str, dict] = {}
    for ref, key in _step_refs(f):
        name = ref[len(PREFIX):]
        if not name or "/" in name or "\\" in name or name.startswith("."):
            continue
        p = media_dir / name
        if not p.is_file():
            continue
        item = by_name.setdefault(name, {"ref": ref, "name": name, "size": p.stat().st_size, "steps": []})
        if key not in item["steps"]:
            item["steps"].append(key)
    return sorted(by_name.values(), key=lambda x: x["name"])


def yaml_name(f: Funnel) -> str:
    if f.source and f.source.path:
        return Path(f.source.path).name
    return f"{f.id or 'funnel'}.yaml"


def build_archive(f: Funnel, yaml_text: str, media_dir: Path) -> bytes:
    items = media_refs(f, media_dir)
    launch = (f.source.launch if f.source else "") or "<запуск>"
    name = yaml_name(f)
    lines = [
        f"Выгрузка воронки «{f.id}» из Студии воронок.",
        "",
        f"1. {name} → launches/{launch}/{name}",
    ]
    if items:
        lines.append(f"2. Файлы из папки static/ → launches/{launch}/static/ (имена не менять –")
        lines.append("   на них ссылаются шаги воронки):")
        for it in items:
            lines.append(f"   • {it['name']} ({it['size'] // 1024} КБ) – шаги: {', '.join(it['steps'])}")
    else:
        lines.append("2. Загруженных в Студию файлов нет – картинки и видео уже лежат в static запуска.")
    lines += ["", "Дальше – предполёт и деплой запуска как обычно (scripts/deploy_launch.sh)."]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, yaml_text)
        for it in items:
            z.write(media_dir / it["name"], f"static/{it['name']}")
        z.writestr("КАК_ВЫЛОЖИТЬ.txt", "\n".join(lines) + "\n")
    return buf.getvalue()
