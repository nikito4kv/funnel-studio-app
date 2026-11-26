"""Круговой тест: yaml движка → граф Студии → yaml → движок.

Оракул – `load_funnel` самого движка (vendored в studio/engine_ref). Круг
считается пройденным, если движок видит воронку после круга РОВНО так же, как
оригинал: `load_funnel(оригинал).model_dump() == load_funnel(после круга).model_dump()`.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import yaml

from studio.convert import export_data, export_yaml, import_data, import_file
from studio.engine_ref.loader import load_funnel
from studio.model.funnel import Note, Position
from studio.paths import funnel_files, bot_repo_path

FILES = funnel_files()
IDS = [f"{p.parent.name}/{p.name}" for p in FILES]


def test_all_funnels_found():
    assert len(FILES) == 5, f"ожидали 5 демо-воронок, нашли {len(FILES)} в {bot_repo_path()}"


def _graph_dump(f):
    # source (откуда импортировали) при повторном импорте из развёрнутого yaml
    # честно другой – сравниваем сам граф
    return f.model_dump(mode="json", exclude={"source"})


@pytest.mark.parametrize("path", FILES, ids=IDS)
def test_roundtrip(path: Path, tmp_path: Path):
    graph = import_file(path, repo_root=bot_repo_path())
    text = export_yaml(graph)
    out = tmp_path / path.name
    out.write_text(text, encoding="utf-8")

    # 1. движок видит то же самое
    assert load_funnel(path).model_dump() == load_funnel(out).model_dump()

    # 2. идемпотентность: круг от результата круга даёт тот же граф (те же id)
    graph2 = import_data(yaml.safe_load(text), graph.source.launch)
    assert _graph_dump(graph2) == _graph_dump(graph)
    assert export_yaml(graph2) == text


@pytest.mark.parametrize("path", [p for p in FILES if "extends" in (yaml.safe_load(p.read_text(encoding="utf-8")) or {})],
                         ids=lambda p: f"{p.parent.name}/{p.name}")
def test_extends_compact(path: Path, tmp_path: Path):
    """Компактная форма (extends + свои ключи) тоже читается движком так же."""
    graph = import_file(path)
    data = export_data(graph, compact=True)
    assert set(data) == {"extends", *graph.source.own_keys}
    data["extends"] = str((path.parent / data["extends"]).resolve())
    out = tmp_path / path.name
    out.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert load_funnel(path).model_dump() == load_funnel(out).model_dump()


def test_notes_never_reach_yaml(tmp_path: Path):
    path = bot_repo_path() / "launches" / "webinar" / "funnel.yaml"
    graph = import_file(path)
    marker = "ЗАМЕТКА-НЕ-ДЛЯ-БОТА-7f3c"
    graph.notes.append(Note(id=uuid.uuid4(), text=marker, step_id=graph.steps[0].id))
    graph.notes.append(Note(id=uuid.uuid4(), kind="revision", number=12, text=marker + " правка",
                            action="replace_text", status="open", position=Position(x=1, y=2)))
    graph.steps[0].title = marker
    text = export_yaml(graph)
    assert marker not in text
    out = tmp_path / "f.yaml"
    out.write_text(text, encoding="utf-8")
    assert load_funnel(out).model_dump() == load_funnel(path).model_dump()


def test_time_strings_stay_strings():
    path = bot_repo_path() / "launches" / "autowebinar" / "funnel.yaml"
    text = export_yaml(import_file(path))
    assert 'webinar_time: "13:00"' in text
