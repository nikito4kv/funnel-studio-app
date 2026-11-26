"""Эндпоинты сервера Студии (этап 1)."""
from __future__ import annotations

import io
import json
import warnings

import pytest
from PIL import Image

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from studio.server import create_app

WEBINAR = "launches/webinar/funnel.yaml"


@pytest.fixture()
def client(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>Студия</title>", encoding="utf-8")
    (dist / "app.js").write_text("console.log(1)", encoding="utf-8")
    app = create_app(data_dir=tmp_path / "data", dist_dir=dist)
    return TestClient(app)


def _import(c, path=WEBINAR):
    r = c.post("/api/import", json={"path": path})
    assert r.status_code == 200, r.text
    return r.json()["name"]


def test_health_and_sources(client):
    assert client.get("/api/health").json()["bot_repo_found"] is True
    src = client.get("/api/sources").json()
    assert len(src) == 5
    row = next(x for x in src if x["path"] == WEBINAR)
    assert row == {"path": WEBINAR, "launch": "webinar", "file": "funnel.yaml",
                   "name": "webinar__funnel", "imported": False}


def test_import_list_get(client):
    name = _import(client)
    assert name == "webinar__funnel"
    assert client.post("/api/import", json={"path": WEBINAR}).json() == {"name": name, "existed": True}
    lst = client.get("/api/funnels").json()
    assert [x["name"] for x in lst] == [name]
    assert lst[0]["steps"] == 14 and lst[0]["source_path"] == WEBINAR
    assert next(x for x in client.get("/api/sources").json() if x["path"] == WEBINAR)["imported"]
    g = client.get(f"/api/funnels/{name}").json()
    assert g["id"] == "webinar_registration"


def test_import_rejects_outside_paths(client):
    assert client.post("/api/import", json={"path": "../etc/passwd"}).status_code == 400
    assert client.post("/api/import", json={"path": "launches/webinar/launch.yaml"}).status_code == 400
    assert client.post("/api/import", json={"path": "launches/nope/funnel.yaml"}).status_code == 404


def test_save_keeps_history_and_validates(client, tmp_path):
    name = _import(client)
    g = client.get(f"/api/funnels/{name}").json()
    g["steps"][0]["title"] = "Контакт"
    r = client.put(f"/api/funnels/{name}", json=g)
    assert r.status_code == 200 and r.json()["history"] == 1
    assert client.get(f"/api/funnels/{name}").json()["steps"][0]["title"] == "Контакт"
    hist = list((tmp_path / "data" / "funnels" / ".history").glob(f"{name}.*.json"))
    assert len(hist) == 1

    bad = json.loads(json.dumps(g))
    bad["steps"][2]["schedule"]["day_offset_by_branch"] = {"M": 1}
    r = client.put(f"/api/funnels/{name}", json=bad)
    assert r.status_code == 422
    body = r.json()
    assert "s_welcome" in body["message"] and "absolute_time_msk" in body["message"]
    assert body["errors"][0]["step_key"] == "s_welcome"
    assert body["errors"][0]["step_id"] == g["steps"][2]["id"]

    bad = json.loads(json.dumps(g))
    bad["steps"][1]["delay"] = 5
    r = client.put(f"/api/funnels/{name}", json=bad)
    assert r.status_code == 422 and "лишнее поле" in r.json()["message"]

    bad = json.loads(json.dumps(g))
    bad["edges"][0]["to_step"] = "00000000-0000-4000-8000-000000000000"
    r = client.put(f"/api/funnels/{name}", json=bad)
    assert r.status_code == 422 and "s_confirm" in r.json()["message"]


def test_create_empty(client):
    r = client.post("/api/funnels", json={"launch": "demo", "name": "funnel", "id": "demo_reg"})
    assert r.json() == {"name": "demo__funnel"}
    assert client.post("/api/funnels", json={"launch": "demo", "name": "funnel"}).status_code == 409
    assert client.post("/api/funnels", json={"launch": "де мо", "name": "f"}).status_code == 400
    g = client.get("/api/funnels/demo__funnel").json()
    assert g["id"] == "demo_reg" and g["steps"] == []


def test_export_roundtrip_same_and_diff(client):
    name = _import(client)
    r = client.post(f"/api/funnels/{name}/export").json()
    assert r["ok"] and r["engine"]["ok"] and r["engine"]["steps"] == 14
    assert r["compare"]["same"] is True and r["compare"]["differences"] == []
    assert r["yaml"].startswith("id: webinar_registration")

    g = client.get(f"/api/funnels/{name}").json()
    g["steps"][2]["content"]["tg"]["text"] = "новый текст"
    g["notes"] = [{"id": "00000000-0000-4000-8000-000000000001", "text": "ЗАМЕТКА-НЕ-В-БОТ",
                   "position": {"x": 1, "y": 1}}]
    r = client.post("/api/export", json={"graph": g}).json()
    assert r["ok"] and r["engine"]["ok"]
    assert "ЗАМЕТКА-НЕ-В-БОТ" not in r["yaml"]
    diffs = r["compare"]["differences"]
    assert [(d["step"], d["field"]) for d in diffs] == [("s_welcome", "text")]
    assert diffs[0]["exported"] == "новый текст"


def test_export_reports_export_error(client):
    name = _import(client)
    g = client.get(f"/api/funnels/{name}").json()
    done = next(e for e in g["edges"] if e["kind"] == "done")
    g["edges"].append({**done, "id": "00000000-0000-4000-8000-000000000009"})
    r = client.post("/api/export", json={"graph": g}).json()
    assert r["ok"] is False and "две стрелки" in r["export_error"]


def test_export_new_funnel_without_original(client):
    client.post("/api/funnels", json={"launch": "demo", "name": "f", "id": "demo"})
    g = client.get("/api/funnels/demo__f").json()
    g["steps"] = [{"id": "00000000-0000-4000-8000-000000000001", "key": "hello",
                   "content": {"tg": {"text": "Привет, {Имя}!"}}}]
    r = client.post("/api/export", json={"graph": g}).json()
    assert r["ok"] and r["engine"]["ok"] and r["compare"] is None
    assert "key: hello" in r["yaml"]


def _jpeg_with_exif(size=(3000, 2000)) -> bytes:
    img = Image.new("RGB", size, (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6            # поворот на 90°
    exif[0x010F] = "Camera"
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif, quality=95)
    return buf.getvalue()


def test_media_upload_image(client, tmp_path):
    r = client.post("/api/media", files={"file": ("photo.JPG", _jpeg_with_exif(), "image/jpeg")})
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["kind"] == "image" and info["ref"] == f"./static/{info['name']}"
    assert info["size"] <= 1024 * 1024
    assert max(info["width"], info["height"]) <= 1920
    assert info["height"] > info["width"]          # поворот из EXIF применён
    saved = tmp_path / "data" / "media" / info["name"]
    img = Image.open(saved)
    assert not img.getexif()                       # EXIF выброшен
    got = client.get(f"/files/webinar/static/{info['name']}")
    assert got.status_code == 200 and got.content == saved.read_bytes()
    # повторная загрузка – тот же файл
    assert client.post("/api/media", files={"file": ("x.jpg", _jpeg_with_exif(), "image/jpeg")}).json()["name"] == info["name"]


def test_media_upload_video_and_errors(client):
    r = client.post("/api/media", files={"file": ("c.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")})
    assert r.json()["kind"] == "video" and r.json()["name"].endswith(".mp4")
    assert client.post("/api/media", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
    assert client.post("/api/media", files={"file": ("x.jpg", b"not an image", "image/jpeg")}).status_code == 400


def test_files_from_repo_static(client):
    r = client.get("/files/webinar/static/welcome.jpg")
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    assert client.get("/files/webinar/static/..%2Flaunch.yaml").status_code in (400, 404)
    assert client.get("/files/webinar/static/../launch.yaml").status_code in (400, 404)
    assert client.get("/files/autowebinar/static/auto-offer.jpg").status_code == 200
    assert client.get("/files/webinar/static/nope.jpg").status_code == 404


def test_files_thumbnail(client, tmp_path):
    """?w= – уменьшенная копия для карточки холста; повтор берётся из кэша; не картинка – оригинал."""
    orig = client.get("/files/webinar/static/welcome.jpg").content
    r = client.get("/files/webinar/static/welcome.jpg?w=320")
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    assert len(r.content) < len(orig)
    assert Image.open(io.BytesIO(r.content)).width <= 320
    cached = list((tmp_path / "data" / "thumbs").glob("*.jpg"))
    assert len(cached) == 1
    assert client.get("/files/webinar/static/welcome.jpg?w=320").content == r.content
    v = client.post("/api/media", files={"file": ("c.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")}).json()
    assert client.get(f"/files/_/static/{v['name']}?w=320").content == b"\x00\x00\x00\x18ftypmp42"


def test_spa_served(client):
    assert "Студия" in client.get("/").text
    assert "Студия" in client.get("/какой-то/путь").text
    assert client.get("/app.js").text == "console.log(1)"
    assert client.get("/api/nope").status_code == 404


def test_old_draft_gets_new_field_defaults(client, tmp_path):
    """Граф, сохранённый до появления поля в модели, отдаётся с умолчаниями (25.09: пустой
    экран на одной из воронок – у старого черновика не было conditions.registered_windows)."""
    name = _import(client)
    p = tmp_path / "data" / "funnels" / f"{name}.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    for s in raw["steps"]:
        s["conditions"].pop("registered_windows", None)
        s["schedule"].pop("min_delay_seconds", None)
    raw.pop("resend_entry_on_repeat", None)
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    g = client.get(f"/api/funnels/{name}").json()
    assert all(s["conditions"]["registered_windows"] == [] for s in g["steps"])
    assert all(s["schedule"]["min_delay_seconds"] == 0 for s in g["steps"])
    assert g["resend_entry_on_repeat"] is False
