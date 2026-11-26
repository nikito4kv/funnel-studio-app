"""Импорт из Miro через сервер Студии: задание, картинки в медиа, повторный импорт с «было / стало»."""
from __future__ import annotations

import copy
import json
import time
import warnings
from pathlib import Path

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from studio.miro import client as miro_client
from studio.miro.fake import FakeMiro
from studio.server import create_app
from studio.server import miro_api

FIX = Path(__file__).parent / "fixtures" / "miro"
LINK = "https://miro.com/app/board/uXjDemoWorkshop=/?moveToWidget=7000000000000000316"


def _snap(name="workshop"):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture()
def fake(monkeypatch):
    f = FakeMiro(_snap(), token="t0k")
    orig = miro_client.MiroClient

    def factory(*a, **kw):
        return orig("t0k", base="http://fake-miro/v2", transport=f.transport())
    monkeypatch.setattr(miro_api, "MiroClient", factory)
    monkeypatch.setenv("MIRO_TOKEN", "t0k")
    return f


@pytest.fixture()
def client(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return TestClient(create_app(data_dir=tmp_path / "data", dist_dir=dist, runtime_dsn=""))


def _wait(c, job):
    for _ in range(200):
        st = c.get(f"/api/miro/jobs/{job}").json()
        if st["state"] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("импорт не закончился")


def _import(c, name="miro"):
    r = c.post("/api/miro/import", json={"link": LINK, "launch": "webinar", "name": name})
    assert r.status_code == 200, r.text
    return _wait(c, r.json()["job"])


def test_status(client, fake):
    assert client.get("/api/miro/status").json()["token"] is True


def test_import_new_funnel(client, fake, tmp_path):
    st = _import(client)
    assert st["state"] == "done", st
    res = st["result"]
    assert res["created"] and res["name"] == "webinar__miro"
    assert res["stats"]["messages"] == 15 and res["stats"]["images"] == 7
    assert any(d["level"] == "check" for d in res["doubts"])
    g = client.get("/api/funnels/webinar__miro").json()
    assert g["miro"]["frame_id"] == "7000000000000000316"
    imgs = [s["media"]["image"] for s in g["steps"] if s["media"]["image"]]
    assert len(imgs) == 7 and all(i.startswith("./static/") for i in imgs)
    # картинки – в медиа Студии и раздаются превью
    name = imgs[0].split("/")[-1]
    assert (tmp_path / "data" / "media" / name).exists()
    assert client.get(f"/files/webinar/static/{name}").status_code == 200
    # снимок фрейма – в кэше
    assert list((tmp_path / "data" / "miro_cache").glob("*.json"))


def test_reimport_diff_then_apply(client, fake):
    _import(client)
    g = client.get("/api/funnels/webinar__miro").json()
    # ручная правка разработчика
    step = next(s for s in g["steps"] if s["key"] == "m04")
    step["content"]["tg"]["text"] += "\nРУЧНАЯ ПРАВКА"
    assert client.put("/api/funnels/webinar__miro", json=g).status_code == 200
    # доска поменялась
    edited = copy.deepcopy(_snap())
    for it in edited["items"]:
        if it["id"] == "7000000000000000008":
            it["data"]["content"] = it["data"]["content"].replace("пока ты читаешь", "пока ты ЧИТАЕШЬ")
    fake.snaps = [edited]
    st = _import(client)
    res = st["result"]
    assert res["created"] is False
    texts = [c["text"] for c in res["diff"]["changes"]]
    assert any("ЧИТАЕШЬ" in t for t in texts), texts
    assert not any("РУЧНАЯ" in t for t in texts), "ручная правка не должна пропасть"
    # до «Применить» черновик прежний
    before = client.get("/api/funnels/webinar__miro").json()
    assert not any("ЧИТАЕШЬ" in s["content"]["tg"]["text"] for s in before["steps"])
    assert client.post(f"/api/miro/jobs/{st['job']}/apply").status_code == 200
    after = client.get("/api/funnels/webinar__miro").json()
    assert any("ЧИТАЕШЬ" in s["content"]["tg"]["text"] for s in after["steps"])
    assert any(s["content"]["tg"]["text"].endswith("РУЧНАЯ ПРАВКА") for s in after["steps"])


def test_import_errors(client, fake):
    r = client.post("/api/miro/import", json={"link": "https://miro.com/app/board/uXjDemoWorkshop=/",
                                             "launch": "webinar", "name": "x"})
    st = _wait(client, r.json()["job"])
    assert st["state"] == "error" and "фрейм" in st["message"]
    # имя занято воронкой не из этого фрейма
    assert client.post("/api/funnels", json={"launch": "webinar", "name": "busy"}).status_code == 200
    st = _import(client, name="busy")
    assert st["state"] == "error" and "другое имя" in st["message"]
