"""Копия движка Студии разошлась с bot-engine – Студия не падает, а говорит об этом словами.

24.09.2026: в bot-engine у шага появились `skip_if_registered_msk` и `min_delay_seconds`,
сервер Студии подтянул свежий bot-engine, а копия модели движка в Студии была старой.
Оракул выгрузки не прочитал исходный yaml (`extra="forbid"`), исключение не поймали –
«Запустить в боте…» показывал красное «HTTP 500».
"""
from __future__ import annotations

import shutil
import uuid
import warnings
from pathlib import Path

import pytest
import yaml

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from studio.engine_ref import drift
from studio.paths import bot_repo_path
from studio.server import create_app

WEBINAR = "launches/webinar/funnel.yaml"
NEW_FIELD = "brand_new_engine_field"
LOADER = Path(drift.__file__).resolve().parent / "loader.py"


def _repo_with_new_field(root: Path) -> Path:
    """Мини-bot-engine: воронка webinar + loader.py, у шага которого есть поле, неизвестное копии Студии."""
    src = bot_repo_path() / WEBINAR
    dst = root / WEBINAR
    dst.parent.mkdir(parents=True)
    shutil.copy2(src, dst)
    body = LOADER.read_text(encoding="utf-8")
    anchor = "    skip_if_past: bool = True\n"
    assert anchor in body
    body = body.replace(anchor, anchor + f"    {NEW_FIELD}: int = 0\n", 1)
    lp = root / drift.ENGINE_LOADER_REL
    lp.parent.mkdir(parents=True)
    lp.write_text(body, encoding="utf-8")
    (root / ".commit").write_text("feedface\n", encoding="utf-8")
    return dst


def _add_field_to_yaml(p: Path) -> None:
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data["steps"][0][NEW_FIELD] = 5
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


# ── сравнение полей ───────────────────────────────────────────────────────────

def test_compare_finds_new_and_gone_fields():
    old = LOADER.read_text(encoding="utf-8")
    new = old.replace("    skip_if_past: bool = True\n", f"    skip_if_past: bool = True\n    {NEW_FIELD}: int = 0\n", 1)
    new = new.replace("    max_video_note_caption: bool = True\n", "", 1)
    res = drift.compare(old, new)
    assert res["same"] is False
    assert res["new"] == {"FunnelStep": [NEW_FIELD]}
    assert res["gone"] == {"FunnelConfig": ["max_video_note_caption"]}
    text = drift.describe(res)
    assert text.startswith("Копия движка в Студии старее bot-engine (новые поля: " + NEW_FIELD)
    assert "FunnelConfig.max_video_note_caption" in text
    assert drift.compare(old, old)["same"] is True


def test_vendored_copy_matches_current_repo_bot():
    """Копия в репозитории совпадает с bot-engine, против которого гоняются тесты (BOT_REPO_PATH)."""
    d = drift.engine_drift(bot_repo_path())
    if d["error"]:
        pytest.skip(d["error"])
    assert d["same"], d["text"]


def test_git_head_without_git(tmp_path):
    gd = tmp_path / ".git"
    (gd / "refs" / "heads").mkdir(parents=True)
    (gd / "HEAD").write_text("ref: refs/heads/feat/x\n", encoding="utf-8")
    (gd / "packed-refs").write_text("# pack\nabc123 refs/heads/feat/x\n", encoding="utf-8")
    assert drift.git_head(tmp_path) == "abc123"
    (gd / "refs" / "heads" / "feat").mkdir()
    (gd / "refs" / "heads" / "feat" / "x").write_text("def456\n", encoding="utf-8")
    assert drift.git_head(tmp_path) == "def456"


def test_cli_exit_code(tmp_path):
    same = drift.main(["--engine", str(LOADER), "--studio", str(LOADER)])
    assert same == 0
    p = tmp_path / "l.py"
    p.write_text(LOADER.read_text(encoding="utf-8").replace(
        "    skip_if_past: bool = True\n", f"    skip_if_past: bool = True\n    {NEW_FIELD}: int = 0\n", 1),
        encoding="utf-8")
    assert drift.main(["--engine", str(p), "--studio", str(LOADER)]) == 1


# ── сервер: никаких 500 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("dsn", ["", "postgresql://fs:x@127.0.0.1:1/nowhere"])
def test_publish_check_with_unreadable_original(tmp_path, dsn):
    root = tmp_path / "repo"
    yml = _repo_with_new_field(root)
    c = TestClient(create_app(data_dir=tmp_path / "data", repo_root=root, dist_dir=tmp_path / "no_dist",
                              runtime_dsn=dsn))
    r = c.post("/api/import", json={"path": WEBINAR})
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    _add_field_to_yaml(yml)          # в bot-engine появилось новое поле – копия Студии его не знает

    r = c.post(f"/api/funnels/{name}/publish/check", json={"validation_errors": 0})
    assert r.status_code == 200, r.text
    chk = r.json()
    assert chk["blockers"] == [], "сверка с оригиналом – справочная, публикацию не блокирует"
    cmp = chk["export"]["compare"]
    assert cmp["same"] is None and NEW_FIELD in cmp["error"]
    assert "старее bot-engine" in cmp["error"] and "не влияет" in cmp["error"]
    assert any(NEW_FIELD in w for w in chk["repo_warnings"])
    assert chk["engine_drift"]["same"] is False and NEW_FIELD in chk["engine_drift"]["text"]
    assert chk["engine_drift"]["engine_commit"] == "feedface"

    # «Выгрузить yaml» тоже без 500
    r = c.post(f"/api/funnels/{name}/export")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["compare"]["error"]

    h = c.get("/api/health").json()
    assert h["ok"] is True and h["engine_copy_matches_repo_bot"] is False
    assert h["engine_copy"]["new"] == {"FunnelStep": [NEW_FIELD]}


def test_repo_export_problem_does_not_block_bot_publish(tmp_path):
    """С движком Студии бот работает по графу: невыгружаемость для bot-engine – предупреждение."""
    root = tmp_path / "repo"
    _repo_with_new_field(root)
    for dsn, blocked in (("", True), ("postgresql://fs:x@127.0.0.1:1/nowhere", False)):
        c = TestClient(create_app(data_dir=tmp_path / f"data{int(blocked)}", repo_root=root,
                                  dist_dir=tmp_path / "no_dist", runtime_dsn=dsn))
        name = c.post("/api/import", json={"path": WEBINAR}).json()["name"]
        g = c.get(f"/api/funnels/{name}").json()
        with_done = {e["from_step"] for e in g["edges"] if e["kind"] == "done"}
        first = next(s for s in g["steps"] if s["id"] in with_done)
        # разные задержки у on_done и перехода в воронку – yaml bot-engine такого не выразит
        g["edges"].append({"id": str(uuid.uuid4()), "from_step": first["id"], "kind": "chain_to_funnel",
                           "to_step": None, "to_funnel": "alt", "delay_seconds": 99, "typing_before": False})
        assert c.put(f"/api/funnels/{name}", json=g).status_code == 200
        chk = c.post(f"/api/funnels/{name}/publish/check").json()
        kinds = [b["kind"] for b in chk["blockers"]]
        if blocked:
            assert kinds == ["engine"] and chk["repo_warnings"] == []
        else:
            assert kinds == [] and any("не влияет" in w for w in chk["repo_warnings"])


def test_unexpected_error_is_text_not_bare_500(tmp_path, monkeypatch):
    import studio.server.versions as sv

    def boom(*a, **k):
        raise RuntimeError("что-то сломалось внутри")

    monkeypatch.setattr(sv, "check_publish", boom)
    c = TestClient(create_app(data_dir=tmp_path / "data", dist_dir=tmp_path / "no_dist", runtime_dsn=""),
                   raise_server_exceptions=False)
    name = c.post("/api/import", json={"path": WEBINAR}).json()["name"]
    r = c.post(f"/api/funnels/{name}/publish/check", json={})
    assert r.status_code == 500
    msg = r.json()["message"]
    assert msg.startswith("Внутренняя ошибка Студии: RuntimeError: что-то сломалось внутри")
