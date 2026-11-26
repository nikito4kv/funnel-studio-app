"""Симулятор расписания и тест-прогон – адреса API (этап 2B).

  POST /api/funnels/{имя}/simulate         – лента событий одного человека
  POST /api/funnels/{имя}/simulate/matrix  – «все ветки»: типовые моменты регистрации
  GET  /api/testrun                         – настроен ли тестовый бот
  POST /api/funnels/{имя}/testrun          – отправить воронку себе через тестового бота

В теле можно передать `graph` – тогда считается то, что сейчас на холсте (с
несохранёнными правками); без него – сохранённая воронка.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError

from ..convert.to_engine import ExportError
from ..model.funnel import Funnel
from ..sim.models import Persona, SimOptions
from ..sim.run import matrix, simulate_graph, today_msk
from ..sim import testrun


class SimulateReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    graph: dict[str, Any] | None = None
    registered_at: str
    platform: str = "tg"
    persona: Persona = Persona()
    dnd: bool | None = None


class MatrixReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    graph: dict[str, Any] | None = None
    date: str | None = None               # "YYYY-MM-DD", по умолчанию сегодня МСК
    platform: str = "tg"
    persona: Persona = Persona()
    dnd: bool | None = None


class TestRunReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    graph: dict[str, Any] | None = None
    registered_at: str | None = None
    persona: Persona = Persona()
    pause_seconds: float = 1.5
    limit: int = 60


def create_router(store, repo_root: Path, validate: Callable[[Any], Funnel],
                  bad: Callable[..., None]) -> APIRouter:
    r = APIRouter()

    def funnel_for(name: str, graph: dict | None) -> Funnel:
        if graph is not None:
            return validate(graph)
        try:
            return store.load(name)
        except FileNotFoundError:
            bad(404, f"воронки «{name}» нет")
        except Exception as e:  # noqa: BLE001
            bad(400, str(e))
        raise AssertionError  # pragma: no cover

    def run(fn):
        try:
            return fn()
        except ExportError as e:
            bad(422, f"воронку нельзя выгрузить для прогона: {e}")
        except (ValidationError, ValueError) as e:
            bad(422, f"движок не принимает воронку: {type(e).__name__}: {e}")

    @r.post("/api/funnels/{name}/simulate")
    def simulate(name: str, req: SimulateReq):
        f = funnel_for(name, req.graph)
        try:
            opts = SimOptions(registered_at=req.registered_at, platform=req.platform,  # type: ignore[arg-type]
                              persona=req.persona, dnd=req.dnd)
        except ValidationError as e:
            bad(422, f"неверные параметры прогона: {e.errors()[0]['msg']}")
        return run(lambda: simulate_graph(f, opts, repo_root))

    @r.post("/api/funnels/{name}/simulate/matrix")
    def simulate_matrix(name: str, req: MatrixReq):
        f = funnel_for(name, req.graph)
        try:
            base = date.fromisoformat(req.date) if req.date else today_msk()
        except ValueError:
            bad(422, f"неверная дата «{req.date}» – нужна ГГГГ-ММ-ДД")
        if req.platform not in ("tg", "max"):
            bad(422, "платформа – tg или max")
        return run(lambda: matrix(f, base, req.platform, req.persona, repo_root, req.dnd))

    @r.get("/api/testrun")
    def testrun_status():
        return testrun.status()

    @r.post("/api/funnels/{name}/testrun")
    async def testrun_send(name: str, req: TestRunReq):
        f = funnel_for(name, req.graph)
        cfg_status = testrun.status()
        if not cfg_status["ready"]:
            bad(400, cfg_status["hint"])
        try:
            return await testrun.run(f, repo_root, req.registered_at, req.persona,
                                     pause_seconds=req.pause_seconds, limit=req.limit,
                                     media_dir=store.media)
        except testrun.TestRunError as e:
            bad(502, str(e))

    return r


__all__ = ["create_router", "HTTPException"]
