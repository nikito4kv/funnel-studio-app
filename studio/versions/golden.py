"""Эталон фраз, общий для Python и TypeScript.

    uv run python -m studio.versions.golden      # пересобрать эталон

Для образцов воронок (web/src/model/__fixtures__) пишет фразы времени, условий и
стрелок, как их строит `phrases.py`. vitest сверяет с эталоном web/src/phrases.ts,
pytest – `phrases.py`: если одна сторона поменяла слова, а другая нет, упадёт тест.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .phrases import EDGE_KIND_LABEL, STEP_TYPE_LABEL, duration, golden_for, minutes

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "web" / "src" / "model" / "__fixtures__"
GOLDEN = ROOT / "web" / "src" / "versions" / "__fixtures__" / "phrases_golden.json"
FIXTURE_FILES = ["webinar__funnel.json", "leadmagnet__funnel.json", "autowebinar__funnel_nextday.json",
                 "autowebinar__funnel.json"]
SAMPLES_SEC = [0, 1, 45, 60, 90, 180, 300, 3600, 3660, 7200, 86400, 90000, 172800 + 3600 + 5]
SAMPLES_MIN = [0, 5, -5, 30, -60, 60, 75, -75, 120, 150, 1440]


def build() -> dict:
    out = {"duration": {str(s): duration(s) for s in SAMPLES_SEC},
           "minutes": {str(m): minutes(m) for m in SAMPLES_MIN},
           "step_type_label": STEP_TYPE_LABEL, "edge_kind_label": EDGE_KIND_LABEL, "funnels": {}}
    for f in FIXTURE_FILES:
        g = json.loads((FIXTURES / f).read_text(encoding="utf-8"))
        out["funnels"][f] = golden_for(g)
    return out


def text() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=1) + "\n"


def main() -> int:
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(text(), encoding="utf-8", newline="\n")
    print(f"записано: {GOLDEN}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
