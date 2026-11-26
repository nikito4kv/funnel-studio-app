"""Обновить vendored-копии хелперов времени движка для симулятора.

    uv run python scripts/vendor_sim.py [коммит]   # или BOT_REPO_PATH=<чистая выгрузка без .git>

Берёт из bot-engine (по умолчанию HEAD, из git – незакоммиченные правки не
попадают) два файла и кладёт в studio/sim/engine/:

  common/launch_engine/funnel/branch.py -> branch.py
  common/launch_engine/funnel/dnd.py    -> dnd.py

Логика не меняется. Правятся только импорты, и это видно в шапке:
  * `from common.launch_engine.funnel.loader import …` → vendored loader Студии;
  * `datetime` берётся из studio.sim.clock – это тот же datetime, только
    `datetime.now()` отдаёт время симуляции, а не настенные часы. Иначе ночной
    режим (dnd.py) и выбор ветки считались бы от момента запуска симулятора.

Планировщик (runner.py / max_runner.py) скопировать нельзя – он весь на
базе данных и APScheduler. Его логика перенесена в studio/sim/simulator.py
метод в метод, с номерами строк оригинала в комментариях.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio.paths import bot_repo_path  # noqa: E402

DST = Path(__file__).resolve().parent.parent / "studio" / "sim" / "engine"
FILES = {
    "common/launch_engine/funnel/branch.py": "branch.py",
    "common/launch_engine/funnel/dnd.py": "dnd.py",
}
SUBS = [
    ("from common.launch_engine.funnel.loader import",
     "from studio.engine_ref.loader import"),
    ("from datetime import datetime, time, timedelta\n",
     "from datetime import time, timedelta\n\nfrom studio.sim.clock import datetime\n"),
]

HEADER = """# VENDORED из bot-engine: {src}
# Коммит bot-engine: {commit}
# Логика не менялась. Правка только в импортах (scripts/vendor_sim.py):
#   loader движка -> studio.engine_ref.loader;
#   datetime -> studio.sim.clock.datetime (now() = время симуляции).
# Обновлять: uv run python scripts/vendor_sim.py
"""


def main() -> int:
    # коммит/ветка аргументом – `git show` мимо рабочего дерева; без аргумента – HEAD,
    # а у чистой выгрузки без .git (git archive) – файлы с диска и коммит из `.commit`
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from vendor_engine import Source
    root = bot_repo_path()
    ref = sys.argv[1] if len(sys.argv) > 1 else (None if not (root / ".git").exists() else "HEAD")
    src = Source(root, ref)
    DST.mkdir(parents=True, exist_ok=True)
    for rel, dst in FILES.items():
        body = src.read(rel)
        for old, new in SUBS:
            body = body.replace(old, new)
        (DST / dst).write_text(HEADER.format(src=rel, commit=src.commit) + body, encoding="utf-8",
                               newline="\n")
        print(f"{DST / dst} <- {rel} @ {src.commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
