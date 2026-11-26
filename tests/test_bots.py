"""Раздел «Боты»: хранилище токенов, клиент Bot API (только методы профиля), API сервера.

Настоящий Telegram не трогается: сервер Студии ходит в фальшивый Bot API (`studio.bots.fake`).
"""
from __future__ import annotations

import io
import json
import logging
import os
import stat
import sys
import warnings

import httpx
import pytest
from PIL import Image

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from studio.bots.fake import COMBAT_TOKEN, TEST_TOKEN, FakeBotApi
from studio.bots.telegram import ALLOWED, FORBIDDEN_EXAMPLES, BotApi, BotApiError, ForbiddenMethod, scrub
from studio.bots.vault import BotVault, VaultError
from studio.bots.where import repo_launches
from studio.paths import bot_repo_path
from studio.server import create_app

KEY = "k" * 40


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("STUDIO_SECRET_KEY", KEY)


@pytest.fixture()
def fake():
    return FakeBotApi()


@pytest.fixture()
def client(tmp_path, fake):
    app = create_app(data_dir=tmp_path / "data", dist_dir=tmp_path / "nodist", runtime_dsn="")
    app.state.bot_api_transport = fake.transport()
    c = TestClient(app)
    c.data = tmp_path / "data"
    return c


def _add(c, token=TEST_TOKEN):
    r = c.post("/api/bots", json={"token": token})
    assert r.status_code == 200, r.text
    return r.json()


def _all_text(c) -> str:
    """Всё, что лежит в папке данных Студии, – одной строкой (ищем там токен)."""
    out = []
    for p in c.data.rglob("*"):
        if p.is_file():
            out.append(p.read_bytes().decode("latin-1"))
    return "\n".join(out)


# ── клиент Bot API ──

def test_client_refuses_dangerous_methods(fake):
    api = BotApi(TEST_TOKEN, transport=fake.transport())
    for m in FORBIDDEN_EXAMPLES:
        with pytest.raises(ForbiddenMethod):
            api.call(m)
    assert fake.calls == []          # до сети не дошло
    assert not ({"getUpdates", "setWebhook", "deleteWebhook", "logOut", "close", "sendMessage"} & ALLOWED)
    assert api.me()["username"] == "e2e_studio_TEST_bot"


def test_client_errors_are_human_and_tokenless(fake):
    with pytest.raises(BotApiError) as e:
        BotApi("не токен")
    assert "@BotFather" in str(e.value)
    bad = "3333333333:AAunknownUNKNOWNunknownUNKNOWNxx_03"
    with pytest.raises(BotApiError) as e:
        BotApi(bad, transport=fake.transport()).me()
    assert "не знает такой токен" in str(e.value) and bad not in str(e.value)

    def boom(req):
        raise httpx.ConnectError(f"cannot connect to {req.url}")
    with pytest.raises(BotApiError) as e:
        BotApi(TEST_TOKEN, transport=httpx.MockTransport(boom)).me()
    assert TEST_TOKEN not in str(e.value) and e.value.__cause__ is None
    assert "<токен>" in scrub(f"https://api.telegram.org/bot{TEST_TOKEN}/getMe")
    assert TEST_TOKEN not in repr(BotApi(TEST_TOKEN))


def test_client_429_retry_after(fake):
    fake.fail_next["setMyName"] = (429, "Too Many Requests: retry after 40", 40)
    with pytest.raises(BotApiError) as e:
        BotApi(TEST_TOKEN, transport=fake.transport()).set_name("x")
    assert "подождать 40 с" in str(e.value)


# ── хранилище токенов ──

def test_vault_encrypts_and_600(tmp_path):
    v = BotVault(tmp_path)
    rec = v.put({"id": 1, "username": "x_bot", "first_name": "X"}, TEST_TOKEN)
    assert rec["last4"] == TEST_TOKEN[-4:] and "token" not in rec
    raw = (tmp_path / "bots.json").read_text(encoding="utf-8")
    assert TEST_TOKEN not in raw and TEST_TOKEN.split(":")[1] not in raw
    assert v.token(1) == TEST_TOKEN
    assert all("token" not in b for b in v.list())
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(tmp_path / "bots.json").st_mode) == 0o600


def test_vault_wrong_key(tmp_path, monkeypatch):
    v = BotVault(tmp_path)
    v.put({"id": 1, "username": "x_bot", "first_name": "X"}, TEST_TOKEN)
    monkeypatch.setenv("STUDIO_SECRET_KEY", "z" * 40)
    assert v.key_state()["ok"] is False and "сменился" in v.key_state()["error"]
    with pytest.raises(VaultError):
        v.token(1)


def test_vault_key_file_when_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO_SECRET_KEY")
    monkeypatch.setenv("STUDIO_SECRET_KEY_FILE", str(tmp_path / "keys" / "k.key"))
    v = BotVault(tmp_path / "data")
    v.put({"id": 7, "username": "y_bot", "first_name": "Y"}, TEST_TOKEN)
    assert (tmp_path / "keys" / "k.key").is_file()
    assert v.key_state() == {"ok": True, "source": "file", "error": None}
    assert v.token(7) == TEST_TOKEN


# ── где работает (bot-engine) ──

def test_repo_launches_bots():
    ls = {x["slug"]: x for x in repo_launches(bot_repo_path())}
    assert ls["webinar"]["tg"]["username"] == "northwind_workshop_bot"
    assert ls["webinar"]["max"]["shared"] == "MAX_BOT_TOKEN"
    assert ls["webinar"]["max"]["username"] == "northwind_demo_max_bot"


# ── API сервера ──

def test_add_bot_card_and_token_never_leaks(client, fake, caplog):
    caplog.set_level(logging.DEBUG)
    card = _add(client)
    assert card["username"] == "e2e_studio_TEST_bot" and card["last4"] == TEST_TOKEN[-4:]
    assert card["careful"] is False and card["fresh"] is True
    assert TEST_TOKEN not in json.dumps(card)
    lst = client.get("/api/bots").json()
    prof = client.get(f"/api/bots/{card['id']}").json()
    idx = client.get("/api/bots/index").json()
    for body in (lst, prof, idx):
        assert TEST_TOKEN not in json.dumps(body, ensure_ascii=False)
    assert TEST_TOKEN not in caplog.text and TEST_TOKEN.split(":")[1] not in caplog.text
    assert TEST_TOKEN not in _all_text(client)
    # повторное добавление – то же бот, новый токен не плодит записей
    again = _add(client)
    assert again["fresh"] is False and len(client.get("/api/bots").json()["bots"]) == 1
    assert fake.forbidden == []


def test_add_bot_bad_token(client):
    r = client.post("/api/bots", json={"token": "abc"})
    assert r.status_code == 400 and "@BotFather" in r.json()["message"]
    bad = "3333333333:AAunknownUNKNOWNunknownUNKNOWNxx_03"
    r = client.post("/api/bots", json={"token": bad})
    assert r.status_code == 400 and "не знает такой токен" in r.json()["message"] and bad not in r.text
    # лишнее поле / не тот тип – без эха тела в ответе
    r = client.post("/api/bots", json={"token": 123, "x": TEST_TOKEN})
    assert r.status_code == 400 and TEST_TOKEN not in r.text


def test_profile_read_from_telegram(client, fake):
    bid = _add(client)["id"]
    fake.bots[TEST_TOKEN]["description"] = "поменяли в BotFather"
    p = client.get(f"/api/bots/{bid}").json()
    assert p["description"] == "поменяли в BotFather"       # источник правды – Telegram
    assert p["name"] == "Тест Студии" and p["short_description"] == "Для проверок"
    assert p["commands"] == [{"command": "start", "description": "Начать"}]
    assert p["flags"]["can_read_all_group_messages"] is False
    assert p["limits"] == {"name": 64, "description": 512, "short_description": 120}
    a = client.get(f"/api/bots/{bid}/avatar")
    assert a.status_code == 200 and a.headers["content-type"] == "image/jpeg" and a.content[:2] == b"\xff\xd8"
    assert set(fake.methods()) <= ALLOWED and fake.forbidden == []


def test_edit_test_bot_fields(client, fake):
    bid = _add(client)["id"]
    r = client.put(f"/api/bots/{bid}/profile/name", json={"value": "Новое имя"})
    assert r.status_code == 200 and r.json()["was"] == "Тест Студии" and r.json()["now"] == "Новое имя"
    assert client.put(f"/api/bots/{bid}/profile/description", json={"value": "д" * 512}).status_code == 200
    r = client.put(f"/api/bots/{bid}/profile/description", json={"value": "д" * 513})
    assert r.status_code == 400 and "513 из 512" in r.json()["message"]
    assert client.put(f"/api/bots/{bid}/profile/short_description", json={"value": "к" * 121}).status_code == 400
    assert client.put(f"/api/bots/{bid}/profile/name", json={"value": "  "}).status_code == 400
    cmds = [{"command": "/start", "description": "Начать"}, {"command": "help", "description": "Помощь"}]
    r = client.put(f"/api/bots/{bid}/profile/commands", json={"value": cmds})
    assert r.status_code == 200 and fake.bots[TEST_TOKEN]["commands"][0]["command"] == "start"
    r = client.put(f"/api/bots/{bid}/profile/commands", json={"value": [{"command": "Старт", "description": "x"}]})
    assert r.status_code == 400 and "латинские" in r.json()["message"]
    assert client.put(f"/api/bots/{bid}/profile/webhook", json={"value": "x"}).status_code == 404
    assert fake.forbidden == []


def test_edit_combat_bot_needs_confirm(client, fake):
    card = _add(client, COMBAT_TOKEN)
    assert card["careful"] is True
    assert any(w["kind"] == "repo" and w["slug"] == "webinar" for w in card["where"])
    r = client.put(f"/api/bots/{card['id']}/profile/short_description", json={"value": "Новый"})
    assert r.status_code == 409
    body = r.json()
    assert body["need_confirm"] is True and body["was"] == "Эфир 17.09" and body["now"] == "Новый"
    assert fake.bots[COMBAT_TOKEN]["short_description"] == "Эфир 17.09"     # не тронут
    assert "setMyShortDescription" not in fake.methods()
    r = client.put(f"/api/bots/{card['id']}/profile/short_description", json={"value": "Новый", "confirm": True})
    assert r.status_code == 200 and fake.bots[COMBAT_TOKEN]["short_description"] == "Новый"


def _upload(c, size=(900, 500), color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    r = c.post("/api/media", files={"file": ("a.png", buf.getvalue(), "image/png")})
    assert r.status_code == 200, r.text
    return r.json()["name"]


def test_avatar_center_square_jpeg(client, fake):
    bid = _add(client)["id"]
    media = _upload(client)
    client.get(f"/api/bots/{bid}/avatar")                   # кэш старого аватара
    r = client.put(f"/api/bots/{bid}/profile/photo", json={"value": media})
    assert r.status_code == 200, r.text
    sent = fake.bots[TEST_TOKEN]["photos"][0]
    im = Image.open(io.BytesIO(sent))
    assert im.format == "JPEG" and im.size == (500, 500)
    a = client.get(f"/api/bots/{bid}/avatar")                # кэш сброшен – новый аватар
    assert Image.open(io.BytesIO(a.content)).size == (500, 500)
    small = _upload(client, size=(100, 100))
    r = client.put(f"/api/bots/{bid}/profile/photo", json={"value": small})
    assert r.status_code == 400 and "маленькая" in r.json()["message"]
    assert client.put(f"/api/bots/{bid}/profile/photo", json={"value": "../bots.json"}).status_code == 400


def test_description_pic(client):
    media = _upload(client, size=(1000, 1000))
    r = client.get("/api/bots-tools/description-pic", params={"media": media})
    assert r.status_code == 200 and Image.open(io.BytesIO(r.content)).size == (640, 360)
    assert "attachment" in r.headers["content-disposition"]


def test_remove_bot(client, fake):
    bid = _add(client)["id"]
    assert client.delete(f"/api/bots/{bid}").json() == {"ok": True}
    assert client.get("/api/bots").json()["bots"] == []
    assert client.get(f"/api/bots/{bid}").status_code == 404
    assert fake.forbidden == []


def test_list_shows_repo_bots_and_max(client):
    lst = client.get("/api/bots").json()
    assert lst["key"]["ok"] is True and lst["engine"]["configured"] is False
    other = {o["username"]: o for o in lst["others"]}
    assert "northwind_workshop_bot" in other
    assert other["northwind_workshop_bot"]["where"][0]["slug"] == "webinar"
    mx = {g["key"]: g for g in lst["max"]}
    assert "MAX_BOT_TOKEN" in mx and any(x["slug"] == "webinar" for x in mx["MAX_BOT_TOKEN"]["launches"])
