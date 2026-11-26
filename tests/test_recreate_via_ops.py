"""Воронку можно собрать операциями редактора с нуля – и движок видит её так же, как оригинал.

Цепочка целиком, на всех 73 воронках:
  1. импорт yaml → граф (JSON) во временную папку;
  2. vitest (web/src/model/recreate.test.ts) по каждому графу строит последовательность
     операций редактора, собирает из пустого графа новую воронку (новые id), сверяет её
     с импортом по смыслу и кладёт результат в папку OUT;
  3. здесь: воссозданный граф → yaml → load_funnel движка == load_funnel(оригинал).

Нужны Node.js и `npm install` в web/ – иначе тест пропускается.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from studio.cli import graph_json
from studio.convert import export_yaml, import_file
from studio.engine_ref.loader import load_funnel
from studio.model.funnel import Funnel
from studio.paths import funnel_files, bot_repo_path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
FILES = funnel_files()


def _name(p: Path) -> str:
    return f"{p.parent.name}__{p.stem}.json"


@pytest.fixture(scope="module")
def recreated(tmp_path_factory) -> Path:
    npx = shutil.which("npx")
    if not npx or not (WEB / "node_modules" / "vitest").exists():
        pytest.skip("нет Node.js или web/node_modules – npm install в web/")
    tmp = tmp_path_factory.mktemp("recreate")
    src, out = tmp / "in", tmp / "out"
    src.mkdir()
    for p in FILES:
        (src / _name(p)).write_text(graph_json(import_file(p, repo_root=bot_repo_path())),
                                    encoding="utf-8")
    env = {**os.environ, "RECREATE_IN": str(src), "RECREATE_OUT": str(out)}
    r = subprocess.run([npx, "vitest", "run", "src/model/recreate.test.ts"], cwd=WEB, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600, shell=os.name == "nt")
    assert r.returncode == 0, f"vitest упал:\n{r.stdout[-4000:]}\n{r.stderr[-2000:]}"
    return out


@pytest.mark.parametrize("path", FILES, ids=[f"{p.parent.name}/{p.name}" for p in FILES])
def test_recreated_yaml_loads_same(recreated: Path, path: Path, tmp_path: Path):
    graph = Funnel.model_validate_json((recreated / _name(path)).read_text(encoding="utf-8"))
    # у воссозданных шагов новые id (uuid4), не те, что выдал импорт
    assert all(s.id.version == 4 for s in graph.steps)
    out = tmp_path / path.name
    out.write_text(export_yaml(graph), encoding="utf-8")
    assert load_funnel(out).model_dump() == load_funnel(path).model_dump()


def test_all_recreated(recreated: Path):
    assert sorted(x.name for x in recreated.glob("*.json")) == sorted(_name(p) for p in FILES)
    # json пригоден для сервера как есть
    for f in recreated.glob("*.json"):
        json.loads(f.read_text(encoding="utf-8"))
