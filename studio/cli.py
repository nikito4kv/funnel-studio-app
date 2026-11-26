"""Командная строка Студии.

    uv run python -m studio.cli schema [-o schema/funnel.schema.json]
    uv run python -m studio.cli graph <funnel.yaml> [-o graph.json]     # yaml → граф Студии (JSON)
    uv run python -m studio.cli yaml <graph.json> [-o funnel.yaml]      # граф Студии → yaml движка
    uv run python -m studio.cli fixtures                                # графы-образцы для тестов холста
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .convert import export_yaml, import_file
from .model.funnel import Funnel
from .paths import bot_repo_path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = ROOT / "schema" / "funnel.schema.json"
FIXTURES_DIR = ROOT / "web" / "src" / "model" / "__fixtures__"
# воронки разных архетипов для теста «воссоздать с нуля» (vitest)
FIXTURE_FUNNELS = ["webinar/funnel.yaml", "leadmagnet/funnel.yaml", "autowebinar/funnel_nextday.yaml",
                   "autowebinar/funnel.yaml"]


def funnel_schema() -> dict:
    schema = Funnel.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://funnel-studio.local/schema/funnel.schema.json"
    return schema


def graph_json(f: Funnel) -> str:
    return json.dumps(f.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=1)


def _write(text: str, out: str | None) -> None:
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8", newline="\n")
        print(f"записано: {out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m studio.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("schema", help="JSON Schema модели Студии")
    s.add_argument("-o", "--out", default=str(DEFAULT_SCHEMA))
    g = sub.add_parser("graph", help="yaml воронки → граф Студии (JSON)")
    g.add_argument("path")
    g.add_argument("-o", "--out")
    y = sub.add_parser("yaml", help="граф Студии (JSON) → yaml движка")
    y.add_argument("path")
    y.add_argument("-o", "--out")
    y.add_argument("--compact", action="store_true", help="для extends-воронок – только свои ключи")
    sub.add_parser("fixtures", help="графы-образцы и умолчания для тестов web/")
    a = ap.parse_args(argv)

    if a.cmd == "schema":
        _write(json.dumps(funnel_schema(), ensure_ascii=False, indent=2) + "\n", a.out)
    elif a.cmd == "graph":
        _write(graph_json(import_file(a.path, repo_root=bot_repo_path())) + "\n", a.out)
    elif a.cmd == "yaml":
        f = Funnel.model_validate_json(Path(a.path).read_text(encoding="utf-8"))
        _write(export_yaml(f, compact=a.compact), a.out)
    elif a.cmd == "fixtures":
        write_fixtures()
    return 0


def write_fixtures(out_dir: Path = FIXTURES_DIR) -> None:
    import uuid

    from .model.funnel import Step
    out_dir.mkdir(parents=True, exist_ok=True)
    nl = chr(10)
    for rel in FIXTURE_FUNNELS:
        path = bot_repo_path() / "launches" / rel
        g = import_file(path, repo_root=bot_repo_path())
        name = f"{path.parent.name}__{path.stem}.json"
        _write(graph_json(g) + nl, str(out_dir / name))
    step = Step(id=uuid.UUID(int=0), key="k")
    _write(json.dumps(step.model_dump(mode="json"), ensure_ascii=False, indent=1) + nl,
           str(out_dir / "default_step.json"))
    _write(graph_json(Funnel(id="x")) + nl, str(out_dir / "default_funnel.json"))


if __name__ == "__main__":
    raise SystemExit(main())
