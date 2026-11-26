"""Этап 2A: хранилище версий, миграция, публикация, архив, откат, дифф, блокеры."""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid
import warnings

import pytest
import yaml

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from studio.engine_ref.loader import load_funnel
from studio.server import create_app
from studio.versions import golden
from studio.versions.diff import diff_graphs
from studio.versions.publish import normalize
from studio.versions.store import VersionedStorage

WEBINAR = "launches/webinar/funnel.yaml"


@pytest.fixture()
def client(tmp_path):
    app = create_app(data_dir=tmp_path / "data", dist_dir=tmp_path / "no_dist")
    return TestClient(app)


def _import(c, path=WEBINAR) -> str:
    r = c.post("/api/import", json={"path": path})
    assert r.status_code == 200, r.text
    return r.json()["name"]


def _step(g: dict, key: str) -> dict:
    return next(s for s in g["steps"] if s["key"] == key)


def _edge(g: dict, frm: str, kind: str, to: str | None = None) -> dict:
    ids = {s["key"]: s["id"] for s in g["steps"]}
    return next(e for e in g["edges"] if e["from_step"] == ids[frm] and e["kind"] == kind
                and (to is None or e["to_step"] == ids[to]))


def _texts(d: dict) -> list[str]:
    return [c["text"] for c in d["changes"]]


# ── эталон фраз общий с TS ────────────────────────────────────────────────────

def test_phrases_golden_is_up_to_date():
    assert golden.GOLDEN.read_text(encoding="utf-8") == golden.text(), \
        "фразы Python разошлись с эталоном: uv run python -m studio.versions.golden и npm test"


# ── хранилище и миграция ──────────────────────────────────────────────────────

def test_migration_of_json_files(tmp_path):
    data = tmp_path / "data"
    (data / "funnels").mkdir(parents=True)
    src = json.loads((golden.FIXTURES / "webinar__funnel.json").read_text(encoding="utf-8"))
    f = data / "funnels" / "webinar__funnel.json"
    f.write_text(json.dumps(src, ensure_ascii=False), encoding="utf-8")
    (data / "funnels" / "broken__x.json").write_text("{не json", encoding="utf-8")

    st = VersionedStorage(data)
    assert st.migrated == ["webinar__funnel"]
    assert (data / "studio.db").exists() and f.exists(), "база создана, старый файл не удалён"
    assert st.load_raw("webinar__funnel") == src
    assert [x["name"] for x in st.list()] == ["webinar__funnel"]
    assert st.versions.draft_row("webinar__funnel")["migrated_from"] == "funnels/webinar__funnel.json"

    # правка в базе не затирается файлом при следующем старте
    g = st.load("webinar__funnel")
    g.title = "из базы"
    st.versions.put_draft("webinar__funnel", g)
    st2 = VersionedStorage(data)
    assert st2.migrated == []
    assert st2.load_raw("webinar__funnel")["title"] == "из базы"


def test_versions_are_immutable(client, tmp_path):
    name = _import(client)
    assert client.post(f"/api/funnels/{name}/publish", json={"comment": "первая"}).status_code == 200
    db = sqlite3.connect(tmp_path / "data" / "studio.db")
    with pytest.raises(sqlite3.IntegrityError, match="неизменяемы"):
        db.execute("UPDATE versions SET graph = '{}' WHERE number = 1")
    with pytest.raises(sqlite3.IntegrityError, match="не удаляются"):
        db.execute("DELETE FROM versions")
    db.execute("UPDATE versions SET status = 'archived'")       # статус менять можно
    with pytest.raises(sqlite3.IntegrityError, match="архивная"):
        db.execute("UPDATE versions SET status = 'published'")


# ── публикация, архив, откат ─────────────────────────────────────────────────

def test_publish_archive_rollback_flow(client, tmp_path):
    name = _import(client)
    v = client.get(f"/api/funnels/{name}/versions").json()
    assert v["published"] is None and v["next"] == 1 and v["versions"] == []

    chk = client.post(f"/api/funnels/{name}/publish/check", json={}).json()
    assert chk["blockers"] == [] and chk["number"] == 1
    assert chk["export"]["engine"]["ok"] and chk["export"]["compare"]["same"]
    r = client.post(f"/api/funnels/{name}/publish", json={"comment": "как в bot-engine", "number": 1})
    assert r.status_code == 200, r.text
    assert r.json()["number"] == 1

    exp = tmp_path / "data" / "exports" / "webinar" / "funnel" / "v1"
    assert load_funnel(exp / "funnel.yaml").steps[0].key == "s_confirm"
    rep = json.loads((exp / "check.json").read_text(encoding="utf-8"))
    assert rep["export"]["engine"]["ok"] and rep["comment"] == "как в bot-engine"

    # без изменений публиковать нечего
    assert [b["kind"] for b in client.post(f"/api/funnels/{name}/publish/check").json()["blockers"]] == ["nothing"]

    g = client.get(f"/api/funnels/{name}").json()
    _edge(g, "s_welcome", "also_fire", "s_remind1")["delay_seconds"] = 300
    assert client.put(f"/api/funnels/{name}", json=g).status_code == 200
    d = client.get(f"/api/funnels/{name}/diff").json()
    assert _texts(d) == ["Шаг «s_remind1»: время «параллельно · через 3 мин» → «параллельно · через 5 мин» "
                         "(стрелка из «s_welcome»)"]
    r = client.post(f"/api/funnels/{name}/publish", json={"comment": "дожим позже", "number": 2})
    assert r.status_code == 200 and r.json()["archived"] == 1

    v = client.get(f"/api/funnels/{name}/versions").json()
    assert v["published"] == 2
    assert [(x["number"], x["status"], x["parent"], x["comment"]) for x in v["versions"]] == [
        (2, "published", 1, "дожим позже"), (1, "archived", None, "как в bot-engine")]
    assert v["versions"][0]["author"] == "я" and v["versions"][0]["report"]["summary"]["total"] == 1

    # дифф между двумя версиями
    d12 = client.get(f"/api/funnels/{name}/diff", params={"a": 1, "b": 2}).json()
    assert d12["summary"]["total"] == 1

    # откат: черновик := v1, заметка черновика остаётся
    g = client.get(f"/api/funnels/{name}").json()
    g["notes"].append({"id": str(uuid.uuid4()), "kind": "note", "text": "не терять", "step_id": g["steps"][3]["id"]})
    client.put(f"/api/funnels/{name}", json=g)
    r = client.post(f"/api/funnels/{name}/rollback", json={"version": 1})
    assert r.status_code == 200
    back = client.get(f"/api/funnels/{name}").json()
    v1 = client.get(f"/api/funnels/{name}/versions/1").json()
    assert _edge(back, "s_welcome", "also_fire", "s_remind1")["delay_seconds"] == 180
    assert back["steps"] == v1["steps"] and back["edges"] == v1["edges"]
    assert [n["text"] for n in back["notes"]] == ["не терять"]
    assert client.get(f"/api/funnels/{name}/versions").json()["published"] == 2, "откат не публикует"
    assert client.get(f"/api/funnels/{name}/versions").json()["draft"]["base_version"] == 1

    r = client.post(f"/api/funnels/{name}/publish", json={"comment": "вернули"})
    assert r.status_code == 200 and r.json()["number"] == 3
    v = client.get(f"/api/funnels/{name}/versions").json()["versions"]
    assert [(x["number"], x["status"], x["parent"]) for x in v] == [
        (3, "published", 1), (2, "archived", 1), (1, "archived", None)]


def test_publish_number_race(client):
    name = _import(client)
    r = client.post(f"/api/funnels/{name}/publish", json={"comment": "", "number": 5})
    assert r.status_code == 409 and "появилась" in r.json()["message"]


def test_blocker_removed_without_tombstone(client):
    name = _import(client)
    client.post(f"/api/funnels/{name}/publish", json={"comment": "v1"})
    g = client.get(f"/api/funnels/{name}").json()
    gone = _step(g, "s_survey_nudge")          # в него не ведут стрелки – редактор дал бы удалить
    g["steps"] = [s for s in g["steps"] if s["id"] != gone["id"]]
    client.put(f"/api/funnels/{name}", json=g)
    chk = client.post(f"/api/funnels/{name}/publish/check").json()
    bl = [b for b in chk["blockers"] if b["kind"] == "removed"]
    assert len(bl) == 1 and bl[0]["step_key"] == "s_survey_nudge" and bl[0]["step"]["id"] == gone["id"]
    assert "и удалён" in bl[0]["text"]
    r = client.post(f"/api/funnels/{name}/publish", json={"comment": "x"})
    assert r.status_code == 409 and r.json()["blockers"][0]["kind"] == "removed"

    # надгробие снимает блокер
    tomb = {**gone, "type": "jump", "content": {"tg": {"text": "", "buttons": [], "claim_button_text": None},
                                               "max": {"text": None, "buttons": None, "claim_button_text": None},
                                               "variants": []}}
    g["steps"].append(tomb)
    g["edges"].append({"id": str(uuid.uuid4()), "from_step": gone["id"], "kind": "jump",
                       "to_step": _step(g, "s_thanks")["id"], "to_funnel": None,
                       "delay_seconds": None, "typing_before": False})
    assert client.put(f"/api/funnels/{name}", json=g).status_code == 200
    chk = client.post(f"/api/funnels/{name}/publish/check").json()
    assert chk["blockers"] == []
    assert "Шаг «s_survey_nudge» удалён – люди с него переходят на «s_thanks»" in _texts(chk["diff"])


def test_blocker_validation_and_engine(client):
    name = _import(client)
    chk = client.post(f"/api/funnels/{name}/publish/check", json={"validation_errors": 2}).json()
    assert [b["kind"] for b in chk["blockers"]] == ["validation"]
    assert client.post(f"/api/funnels/{name}/publish", json={"validation_errors": 2}).status_code == 409

    # разные задержки у on_done и перехода в воронку – экспорт откажется выгружать
    g = client.get(f"/api/funnels/{name}").json()
    greet = _step(g, "s_greet")
    g["edges"].append({"id": str(uuid.uuid4()), "from_step": greet["id"], "kind": "chain_to_funnel",
                       "to_step": None, "to_funnel": "alt", "delay_seconds": 99, "typing_before": False})
    assert client.put(f"/api/funnels/{name}", json=g).status_code == 200
    chk = client.post(f"/api/funnels/{name}/publish/check").json()
    assert [b["kind"] for b in chk["blockers"]] == ["engine"]


# ── дифф на реальной воронке ─────────────────────────────────────────────────

def _webinar() -> dict:
    return normalize(json.loads((golden.FIXTURES / "webinar__funnel.json").read_text(encoding="utf-8")))


def test_diff_same_is_empty():
    g = _webinar()
    d = diff_graphs(g, copy.deepcopy(g))
    assert d["summary"]["total"] == 0 and d["steps"] == {} and d["ghosts"] == []


def test_diff_lines_on_webinar():
    old = _webinar()
    new = copy.deepcopy(old)
    ids = {s["key"]: s["id"] for s in new["steps"]}
    # текст TG, время-стрелка, условие, кнопка, медиа, переименование, удаление, новая стрелка, новый шаг
    s01 = _step(new, "s_welcome")
    s01["content"]["tg"]["text"] = s01["content"]["tg"]["text"].replace("17", "18", 1)
    _edge(new, "s_greet", "done", "s_welcome")["delay_seconds"] = 300
    _step(new, "s_offer")["conditions"]["not_paid"] = False
    _step(new, "s_remind1")["content"]["tg"]["buttons"][0]["text"] = "Забрать"
    _step(new, "s_remind2")["media"]["image"] = "./static/new.jpg"
    _step(new, "s_q3")["key"] = "z_q3_last"
    new["steps"] = [s for s in new["steps"] if s["key"] != "s_survey_nudge"]
    new["edges"].append({"id": str(uuid.uuid4()), "from_step": ids["s_welcome"], "kind": "also_fire",
                         "to_step": ids["s_thanks"], "to_funnel": None, "delay_seconds": 600,
                         "typing_before": False})
    added = copy.deepcopy(_step(new, "s_thanks"))
    added.update(id=str(uuid.uuid4()), key="z_06")
    new["steps"].append(added)
    new["max_video_note_caption"] = not old["max_video_note_caption"]

    d = diff_graphs(old, new)
    t = _texts(d)
    assert "Шаг «s_greet»: время «через 5 сек» → «через 5 мин»" not in t
    assert "Шаг «s_welcome»: время «через 5 сек» → «через 5 мин»" in t
    assert "Шаг «s_survey_nudge» удалён" in t
    assert "Добавлена стрелка s_welcome → s_thanks (параллельно · через 10 мин)" in t
    assert "Шаг «s_q3» переименован в «z_q3_last»" in t
    assert "Шаг «s_offer»: условие убрано: 💳 не оплатил ВИП" in t
    assert "Добавлен шаг «z_06» – 💬 Текст" in t
    assert any(x.startswith("Шаг «s_welcome»: текст TG: «") for x in t)
    assert any(x.startswith("Шаг «s_remind1»: кнопки TG: кнопка «") and x.endswith("» → «Забрать»") for x in t)
    assert "Шаг «s_remind2»: картинка: " + old_image(old, "s_remind2") + " → new.jpg" in t
    assert any(x.startswith("подпись к кружку в MAX:") for x in t)
    assert d["steps"][ids["s_welcome"]] == "changed" and d["steps"][ids["s_q3"]] == "renamed"
    assert d["steps"][added["id"]] == "added" and d["steps"][ids["s_survey_nudge"]] == "removed"
    assert [g_["key"] for g_ in d["ghosts"]] == ["s_survey_nudge"]
    assert d["ghosts"][0]["position"] == old["layout"]["positions"].get(ids["s_survey_nudge"])
    # группы в порядке: настройки, добавленные, удалённые, переименованные, изменённые, стрелки
    groups = [c["group"] for c in d["changes"]]
    assert groups == sorted(groups, key=["settings", "added", "removed", "renamed", "changed", "edges"].index)
    # предупреждения публикации: переименование и время
    w = [x["text"] for x in d["warnings"]]
    assert any("s_q3" in x and "таймеры привязаны к имени" in x for x in w)
    assert any("через 5 мин" in x and "по старому времени" in x for x in w)


def old_image(g: dict, key: str) -> str:
    ref = _step(g, key)["media"]["image"]
    return ref.rsplit("/", 1)[-1] if ref else "–"


def test_diff_schedule_phrase_and_retarget():
    old = normalize(json.loads((golden.FIXTURES / "autowebinar__funnel.json").read_text(encoding="utf-8")))
    new = copy.deepcopy(old)
    s = next(x for x in new["steps"] if x["schedule"]["absolute_time_msk"])
    before = s["schedule"]["absolute_time_msk"]
    s["schedule"]["absolute_time_msk"] = "07:45"
    e = next(x for x in new["edges"] if x["kind"] == "done")
    others = [x["id"] for x in new["steps"] if x["id"] not in (e["from_step"], e["to_step"])]
    e["to_step"] = others[0]
    d = diff_graphs(old, new)
    t = _texts(d)
    assert any(x.startswith(f"Шаг «{s['key']}»: время «в {before},") and "→ «в 07:45," in x for x in t), t
    assert any("теперь ведёт в" in x for x in t)


def test_diff_max_states():
    old = _webinar()
    new = copy.deepcopy(old)
    s = _step(new, "s_welcome")
    s["content"]["max"]["text"] = ""
    s["content"]["max"]["buttons"] = [{"text": "Канал MAX", "url": "https://max.ru/x", "callback_data": None,
                                       "style": None, "web_app": False}]
    t = _texts(diff_graphs(old, new))
    assert "Шаг «s_welcome»: текст MAX: как в TG → скрыт" in t
    assert "Шаг «s_welcome»: кнопки MAX: как в TG → свои" in t


def test_layout_and_notes_are_not_changes():
    old = _webinar()
    new = copy.deepcopy(old)
    for p in new["layout"]["positions"].values():
        p["x"] += 100
    new["notes"].append({"id": str(uuid.uuid4()), "kind": "note", "text": "x", "color": "#fff6b6",
                         "step_id": None, "lane_id": None, "position": {"x": 0, "y": 0}, "author": "",
                         "created_at": "", "number": None, "action": None, "status": None})
    new["lanes"] = []
    assert diff_graphs(old, new)["summary"]["total"] == 0


def test_live_diff_endpoint(client):
    name = _import(client)
    g = client.get(f"/api/funnels/{name}").json()
    # опубликованной нет – всё «добавлено»
    d = client.post(f"/api/funnels/{name}/diff", json={"graph": g}).json()
    assert d["against"] is None and d["summary"]["added"] == 14
    client.post(f"/api/funnels/{name}/publish", json={"comment": ""})
    _step(g, "s_offer")["content"]["tg"]["text"] += " Ждём!"
    d = client.post(f"/api/funnels/{name}/diff", json={"graph": g}).json()
    assert d["against"] == 1 and d["summary"]["total"] == 1
    assert d["changes"][0]["step_key"] == "s_offer" and "Ждём!" in d["changes"][0]["text"]
    # и граф, который ещё не проходит проверку модели, дифф всё равно считает
    g["steps"][0]["schedule"]["day_offset_by_branch"] = {"M": 1}
    assert client.post(f"/api/funnels/{name}/diff", json={"graph": g}).status_code == 200


def test_export_copies_uploaded_media(client, tmp_path):
    import io

    from PIL import Image
    name = _import(client)
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(buf, "PNG")
    info = client.post("/api/media", files={"file": ("x.png", buf.getvalue(), "image/png")}).json()
    g = client.get(f"/api/funnels/{name}").json()
    _step(g, "s_welcome")["media"]["image"] = info["ref"]
    client.put(f"/api/funnels/{name}", json=g)
    assert client.post(f"/api/funnels/{name}/publish", json={"comment": ""}).status_code == 200
    out = tmp_path / "data" / "exports" / "webinar" / "funnel" / "v1"
    assert (out / "static" / info["name"]).is_file()
    assert info["ref"] in (out / "funnel.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load((out / "funnel.yaml").read_text(encoding="utf-8"))
