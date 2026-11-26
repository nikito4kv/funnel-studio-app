"""Обновить vendored-копию модели движка из bot-engine.

    uv run python scripts/vendor_engine.py                                   # файлы с диска BOT_REPO_PATH
    uv run python scripts/vendor_engine.py --ref origin/main   # из git, мимо рабочего дерева

Копирует common/launch_engine/funnel/loader.py в studio/engine_ref/loader.py и
ставит в шапку коммит bot-engine. Логику не трогает. После обновления – прогнать
тесты: test_mapping.py покажет новые поля движка, которым нужно место в модели.

Откуда брать bot-engine. Боевой код живёт в ветке `main`, а
рабочее дерево на машине разработчика может стоять на чужой ветке с незакоммиченными
правками. Поэтому:
  * `--ref <ветка/коммит>` – файлы берутся `git show` из репозитория BOT_REPO_PATH;
  * без `--ref` – с диска. Если BOT_REPO_PATH – чистая выгрузка (`git archive`, без
    .git), коммит берётся из файла `.commit` в её корне (или `BOT_REPO_COMMIT`).

С 24.09.2026 модель шага зовёт `reg_window.parse_windows` (проверка окон
`skip_if_registered_msk`). Сам reg_window тянет sqlalchemy (чтение времени
регистрации из базы бота), поэтому в studio/engine_ref/reg_window.py кладётся
только его чистая часть – разбор и проверка окон, ключи входных шагов, – вырезанная
по AST без правок, а импорт в loader.py переводится на неё.
"""
from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio.paths import bot_repo_path  # noqa: E402

SRC_REL = "common/launch_engine/funnel/loader.py"
REG_REL = "common/launch_engine/funnel/reg_window.py"
DST_DIR = Path(__file__).resolve().parent.parent / "studio" / "engine_ref"
DST = DST_DIR / "loader.py"
DST_REG = DST_DIR / "reg_window.py"

SUBS = [
    ("from common.launch_engine.funnel.reg_window import",
     "from studio.engine_ref.reg_window import"),
]
# чистая часть reg_window: то, что нужно модели и движку Студии без базы бота
REG_KEEP = {"parse_windows", "in_windows", "entry_step_keys", "_WINDOW", "_MSK"}
REG_DROP_IMPORTS = {"logging", "sqlalchemy"}

HEADER = """# VENDORED из bot-engine: {src}
# Коммит bot-engine: {commit}{dirty}
# Скопировано как есть, логика не менялась. Нужен как «оракул» кругового теста:
# load_funnel(оригинал) должен совпасть с load_funnel(yaml после круга).
# Зависимости – pydantic и pyyaml; импорт reg_window переведён на studio.engine_ref.reg_window.
# Обновлять: scripts/vendor_engine.py (копирует заново и ставит новый коммит).
"""

REG_HEADER = """# VENDORED из bot-engine: {src} – только чистая часть
# Коммит bot-engine: {commit}{dirty}
# Функции скопированы как есть (вырезаны по AST: {names}).
# Чтение времени регистрации из базы бота (sqlalchemy) не копируется: у движка
# Студии своя база – runtime/gates/evaluate.py. Обновлять: scripts/vendor_engine.py.
"""


class Source:
    """Откуда читать bot-engine: git-ссылка в репозитории или файлы на диске."""

    def __init__(self, root: Path, ref: str | None):
        self.root, self.ref = root, ref
        if ref:
            self.commit = self._git("rev-parse", ref).strip()
        else:
            self.commit = self._disk_commit()

    def _git(self, *args: str) -> str:
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True, encoding="utf-8")

    def _disk_commit(self) -> str:
        if os.environ.get("BOT_REPO_COMMIT"):
            return os.environ["BOT_REPO_COMMIT"].strip()
        mark = self.root / ".commit"
        if mark.is_file():
            return mark.read_text(encoding="utf-8").strip()
        return self._git("rev-parse", "HEAD").strip()

    def read(self, rel: str) -> str:
        if self.ref:
            return self._git("show", f"{self.commit}:{rel}")
        return (self.root / rel).read_text(encoding="utf-8")

    def dirty(self, rel: str) -> str:
        if self.ref or not (self.root / ".git").exists():
            return ""
        out = self._git("status", "--porcelain", "--", rel).strip()
        return " (+ незакоммиченные правки!)" if out else ""


def pure_reg_window(body: str) -> tuple[str, list[str]]:
    """Модуль reg_window → docstring, импорты без базы, константы и чистые функции."""
    tree = ast.parse(body)
    out = ""
    prev = None
    names: list[str] = []

    def put(seg: str, kind: str) -> None:
        nonlocal out, prev
        if prev is not None:
            if kind == "def" or prev == "def":
                out += "\n\n"
            elif kind != prev:
                out += "\n"
        out += seg + "\n"
        prev = kind

    for node in tree.body:
        seg = ast.get_source_segment(body, node)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and node is tree.body[0]:
            put(seg, "doc")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
            if (mod or "").split(".")[0] not in REG_DROP_IMPORTS:
                put(seg, "import")
        elif isinstance(node, ast.Assign):
            tgt = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(t in REG_KEEP for t in tgt):
                put(seg, "const")
        elif isinstance(node, ast.FunctionDef) and node.name in REG_KEEP:
            put(seg, "def")
            names.append(node.name)
    missing = {"parse_windows", "in_windows", "entry_step_keys"} - set(names)
    if missing:
        raise SystemExit(f"в reg_window.py нет функций {sorted(missing)} – разберитесь руками")
    return out, names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", help="ветка/коммит bot-engine (git show мимо рабочего дерева)")
    a = ap.parse_args()
    src = Source(bot_repo_path(), a.ref)
    body = src.read(SRC_REL)
    for old, new in SUBS:
        body = body.replace(old, new)
    DST.write_text(HEADER.format(src=SRC_REL, commit=src.commit, dirty=src.dirty(SRC_REL)) + body,
                   encoding="utf-8", newline="\n")
    print(f"{DST} <- {SRC_REL} @ {src.commit}")
    try:
        reg = src.read(REG_REL)
    except (FileNotFoundError, subprocess.CalledProcessError):
        reg = None
    if reg is not None:
        pure, names = pure_reg_window(reg)
        DST_REG.write_text(REG_HEADER.format(src=REG_REL, commit=src.commit, dirty=src.dirty(REG_REL),
                                             names=", ".join(names)) + pure,
                           encoding="utf-8", newline="\n")
        print(f"{DST_REG} <- {REG_REL} @ {src.commit} ({', '.join(names)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
