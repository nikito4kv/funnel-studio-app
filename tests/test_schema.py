"""JSON Schema модели и JSON-форма графа (её будет читать браузер)."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from studio.cli import DEFAULT_SCHEMA, funnel_schema, graph_json
from studio.convert import export_yaml, import_file
from studio.model.funnel import Funnel
from studio.paths import funnel_files

FILES = funnel_files()


def test_schema_file_is_fresh():
    on_disk = json.loads(Path(DEFAULT_SCHEMA).read_text(encoding="utf-8"))
    assert on_disk == funnel_schema(), "схема устарела: uv run python -m studio.cli schema"


@pytest.mark.parametrize("path", FILES, ids=[f"{p.parent.name}/{p.name}" for p in FILES])
def test_graph_json_valid_and_lossless(path: Path):
    graph = import_file(path)
    text = graph_json(graph)
    jsonschema.validate(json.loads(text), funnel_schema())
    back = Funnel.model_validate_json(text)
    assert back == graph
    assert export_yaml(back) == export_yaml(graph)
