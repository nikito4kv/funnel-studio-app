"""Раздел «Боты» с подключённым движком: бот движка виден в списке, «где работает» ведёт к
воронкам Студии, токен бота движка (env:…) Студия не трогает."""
from __future__ import annotations

import json
import warnings

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from rt_helpers import TEST_DSN, mk_funnel
from studio.bots.fake import TEST_TOKEN, FakeBotApi
from studio.server import create_app


@pytest.mark.asyncio
async def test_engine_bot_listed_and_linked_to_funnels(env, tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_SECRET_KEY", "s" * 40)
    await env.tenant("t1")
    await env.tenant("t2")
    async with env.pool.acquire() as c:
        await c.execute("UPDATE bots SET username='e2e_studio_TEST_bot' WHERE tenant_id=1")
        await c.execute("UPDATE bots SET username='other_TEST_bot' WHERE tenant_id=2")
        refs_before = [r["secret_ref"] for r in await c.fetch("SELECT secret_ref FROM bots ORDER BY id")]
    fake = FakeBotApi()
    app = create_app(data_dir=tmp_path / "studio", dist_dir=tmp_path / "no_dist", runtime_dsn=TEST_DSN)
    app.state.bot_api_transport = fake.transport()
    c = TestClient(app)
    f = mk_funnel("f2", [{"key": "a", "text": "Привет"}], [])
    assert c.post("/api/funnels", json={"launch": "t1", "name": "f2", "id": "f2"}).status_code == 200
    assert c.put("/api/funnels/t1__f2", json=f.model_dump(mode="json", by_alias=True)).status_code == 200
    c.put("/api/funnels/t1__f2/engine", json={"tenant": "t1"})

    lst = c.get("/api/bots").json()
    assert lst["engine"] == {"configured": True, "error": None}
    other = {o["username"]: o for o in lst["others"]}
    assert other["other_TEST_bot"]["test"] is True
    assert other["other_TEST_bot"]["where"][0] == {"kind": "studio", "slug": "t2", "title": "t2",
                                                   "funnels": [], "entry_funnel": None}

    card = c.post("/api/bots", json={"token": TEST_TOKEN}).json()
    assert card["where"][0]["kind"] == "studio" and card["where"][0]["slug"] == "t1"
    assert card["where"][0]["funnels"] == [{"name": "t1__f2", "label": "t1 / f2"}]
    lst = c.get("/api/bots").json()
    assert "e2e_studio_TEST_bot" not in {o["username"] for o in lst["others"]}   # уже свой – не дублируется
    assert TEST_TOKEN not in json.dumps(lst)
    async with env.pool.acquire() as cn:
        assert [r["secret_ref"] for r in await cn.fetch("SELECT secret_ref FROM bots ORDER BY id")] == refs_before
    assert fake.forbidden == []
