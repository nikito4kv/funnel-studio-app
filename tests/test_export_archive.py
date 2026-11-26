"""Этап 2C: медиа при выгрузке – список загруженных файлов и архив «yaml + static»."""
from __future__ import annotations

import io
import warnings
import zipfile

import pytest
import yaml
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
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return TestClient(create_app(data_dir=tmp_path / "data", dist_dir=dist))


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(buf, "PNG")
    return buf.getvalue()


def test_export_lists_uploaded_media_and_archive(client):
    name = client.post("/api/import", json={"path": WEBINAR}).json()["name"]
    g = client.get(f"/api/funnels/{name}").json()

    # без загруженных файлов – список пуст, архив всё равно собирается
    res = client.post("/api/export", json={"graph": g}).json()
    assert res["ok"] and res["media"] == []

    up = client.post("/api/media", files={"file": ("pic.png", _png(), "image/png")}).json()
    ref = up["ref"]
    assert ref.startswith("./static/") and ref.endswith(".jpg")
    g["steps"][0]["media"]["image"] = ref
    g["steps"][1]["media"]["image"] = ref            # тот же файл у двух шагов – в списке один раз
    first, second = g["steps"][0]["key"], g["steps"][1]["key"]

    res = client.post("/api/export", json={"graph": g}).json()
    assert res["engine"]["ok"], res
    assert res["media"] == [{"ref": ref, "name": up["name"], "size": up["size"], "steps": [first, second]}]

    r = client.post("/api/export/archive", json={"graph": g})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="webinar__funnel.zip"' in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert names == {"funnel.yaml", f"static/{up['name']}", "КАК_ВЫЛОЖИТЬ.txt"}
    data = yaml.safe_load(z.read("funnel.yaml").decode("utf-8"))
    assert data["steps"][0]["image"] == ref
    assert z.read(f"static/{up['name']}") == (client.app.state.store.media / up["name"]).read_bytes()
    readme = z.read("КАК_ВЫЛОЖИТЬ.txt").decode("utf-8")
    assert "launches/webinar/static/" in readme and up["name"] in readme


def test_archive_skips_launch_static_and_rejects_broken(client):
    name = client.post("/api/import", json={"path": WEBINAR}).json()["name"]
    g = client.get(f"/api/funnels/{name}").json()
    # картинки webinar лежат в static запуска в bot-engine – в архив их не кладём
    r = client.post("/api/export/archive", json={"graph": g})
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert not [n for n in z.namelist() if n.startswith("static/")]
    assert "Загруженных в Студию файлов нет" in z.read("КАК_ВЫЛОЖИТЬ.txt").decode("utf-8")
    # выгрузить нельзя (разные задержки у «по времени» и перехода в воронку) – 422 с текстом
    done = next(e for e in g["edges"] if e["kind"] == "done" and e["delay_seconds"] != 999)
    sid = done["from_step"]
    g["edges"].append({"id": "00000000-0000-4000-8000-00000000c0de", "from_step": sid, "kind": "chain_to_funnel",
                       "to_step": None, "to_funnel": "alt", "delay_seconds": 999, "typing_before": False})
    r = client.post("/api/export/archive", json={"graph": g})
    assert r.status_code == 422
    assert "Выгрузить нельзя" in r.json()["message"]
