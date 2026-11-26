"""Совпадает ли копия движка в Студии с bot-engine – по набору полей моделей.

Студия читает yaml bot-engine своей копией модели движка (`loader.py` рядом). Сервер
Студии при каждой выкатке тянет свежий bot-engine, а копия обновляется руками
(`scripts/vendor_engine.py`). Если в bot-engine у шага появилось поле, которого копия
не знает, оракул выгрузки не прочитает исходный yaml (`extra="forbid"`) – так
24.09.2026 «Запустить в боте» отдавал HTTP 500.

Сравнение – по полям pydantic-моделей (`FunnelStep`, `FunnelConfig`, …), разбором
исходника (ast), без импорта кода bot-engine: логика движка тянет базу и aiogram.

    uv run python -m studio.engine_ref.drift [--engine <loader.py bot-engine>] [--studio <loader.py Студии>]

Код выхода 1 – копии разошлись (для скриптов выкладки).
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDIO_LOADER = HERE / "loader.py"
ENGINE_LOADER_REL = "common/launch_engine/funnel/loader.py"


def model_fields(source: str) -> dict[str, set[str]]:
    """Исходник модуля → {класс: поля}. Поле – аннотированное имя в теле класса."""
    out: dict[str, set[str]] = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.ClassDef):
            continue
        fields = {st.target.id for st in node.body
                  if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name)
                  and not st.target.id.startswith("_") and st.target.id != "model_config"}
        if fields:
            out[node.name] = fields
    return out


def vendored_commit(source: str) -> str:
    m = re.search(r"^# Коммит bot-engine: ([0-9a-f]{7,40})", source, re.M)
    return m.group(1) if m else ""


def git_head(root: Path) -> str:
    """Коммит рабочей копии bot-engine без git (в контейнере Студии его нет)."""
    gd = root / ".git"
    try:
        if gd.is_file():   # worktree: "gitdir: <путь>"
            gd = Path(gd.read_text(encoding="utf-8").split(":", 1)[1].strip())
        head = (gd / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head
        ref = head.split(":", 1)[1].strip()
        p = gd / ref
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
        packed = gd / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0]
    except OSError:
        pass
    mark = root / ".commit"      # чистая выгрузка (git archive) – коммит рядом
    if mark.is_file():
        return mark.read_text(encoding="utf-8").strip()
    return ""


def compare(studio_src: str, engine_src: str) -> dict:
    """→ {same, new: {класс: [поля]}, gone: {класс: [поля]}}.

    new – поля есть в bot-engine, а копия Студии их не знает (исходный yaml с ними не
    прочитается); gone – в Студии есть, в bot-engine уже нет (выгрузка с ними не прочитается).
    """
    a, b = model_fields(studio_src), model_fields(engine_src)
    new: dict[str, list[str]] = {}
    gone: dict[str, list[str]] = {}
    for cls in sorted(set(a) | set(b)):
        plus = sorted(b.get(cls, set()) - a.get(cls, set()))
        minus = sorted(a.get(cls, set()) - b.get(cls, set()))
        if plus:
            new[cls] = plus
        if minus:
            gone[cls] = minus
    return {"same": not new and not gone, "new": new, "gone": gone}


def _flat(d: dict[str, list[str]]) -> list[str]:
    return [f if cls == "FunnelStep" else f"{cls}.{f}" for cls, fs in d.items() for f in fs]


def describe(res: dict) -> str:
    """Одна-две фразы для человека."""
    if res.get("same") is None:
        return res.get("error") or "сверить копию движка с bot-engine не удалось"
    if res["same"]:
        return "Копия движка в Студии совпадает с bot-engine"
    parts = []
    if res["new"]:
        parts.append("копия движка в Студии старее bot-engine (новые поля: " + ", ".join(_flat(res["new"])) + ")")
    if res["gone"]:
        parts.append("в bot-engine больше нет полей: " + ", ".join(_flat(res["gone"])))
    return "; ".join(parts)[:1].upper() + "; ".join(parts)[1:]


def engine_drift(repo_root: Path, studio_loader: Path = STUDIO_LOADER) -> dict:
    """Сверка для /api/health и проверки публикации. Никогда не бросает."""
    studio_src = studio_loader.read_text(encoding="utf-8")
    out: dict = {"same": None, "new": {}, "gone": {}, "studio_commit": vendored_commit(studio_src),
                 "engine_commit": git_head(repo_root), "error": None}
    p = repo_root / ENGINE_LOADER_REL
    try:
        engine_src = p.read_text(encoding="utf-8")
    except OSError:
        out["error"] = (f"сверить копию движка с bot-engine нельзя: в {repo_root} нет {ENGINE_LOADER_REL}")
        out["text"] = out["error"]
        return out
    try:
        out.update(compare(studio_src, engine_src))
    except SyntaxError as e:
        out["error"] = f"сверить копию движка с bot-engine нельзя: {ENGINE_LOADER_REL} не разбирается ({e})"
    out["text"] = describe(out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="сравнить поля модели движка Студии и bot-engine")
    ap.add_argument("--engine", help=f"{ENGINE_LOADER_REL} bot-engine ('-' – stdin); по умолчанию из BOT_REPO_PATH")
    ap.add_argument("--studio", help="loader.py Студии ('-' – stdin); по умолчанию studio/engine_ref/loader.py")
    a = ap.parse_args(argv)

    def read(v: str | None, default: Path) -> str:
        if v == "-":
            return sys.stdin.buffer.read().decode("utf-8")
        return Path(v or default).read_text(encoding="utf-8")

    from ..paths import bot_repo_path
    studio_src = read(a.studio, STUDIO_LOADER)
    engine_src = read(a.engine, bot_repo_path() / ENGINE_LOADER_REL)
    res = compare(studio_src, engine_src)
    text = describe(res)
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    return 0 if res["same"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
