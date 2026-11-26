"""HTTP-ручки источников в сервисе движка (aiohttp, runner._health_app).

  POST /payments/callback              вебхук кассы (подпись X-Payment-Signature)
  POST /t/<запуск>/payments/callback   то же, но только для одного запуска
  GET  /go/<code>?u=&p=                прокси-ссылка: клик + 302
  GET  /t/<запуск>/govip?u=&pl=&s=     ВИП-прокси: клик + 302 на страницу с id

⚠️ Сервис слушает 127.0.0.1:STUDIO_RUNTIME_PORT (8781): касса и
люди до него дойдут только через публичный адрес (nginx/туннель) – его и
надо указать в `links.base_url` запуска и в настройке вебхука кассы.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

from . import links, payments

if TYPE_CHECKING:
    from ..service import Runtime


def add_routes(app: web.Application, rt: "Runtime") -> None:
    async def callback(req: web.Request) -> web.Response:
        body = await req.read()
        code, data = await payments.receive(rt, body, req.headers.get(payments.SIGNATURE_HEADER, ""),
                                            tenant_slug=req.match_info.get("slug"))
        return web.json_response(data, status=code)

    def _redirect(code: int, target: str) -> web.Response:
        if code == 302:
            return web.Response(status=302, headers={"Location": target, "Cache-Control": "no-store"})
        return web.Response(status=code, text=target, content_type="text/plain", charset="utf-8")

    async def go(req: web.Request) -> web.Response:
        code, target = await links.go(rt, req.match_info["code"], dict(req.query),
                                      req.headers.get("User-Agent"))
        return _redirect(code, target)

    async def govip(req: web.Request) -> web.Response:
        code, target = await links.govip(rt, req.match_info["slug"], dict(req.query),
                                         req.headers.get("User-Agent"))
        return _redirect(code, target)

    app.router.add_post("/payments/callback", callback)
    app.router.add_post("/t/{slug}/payments/callback", callback)
    app.router.add_get("/go/{code}", go)
    app.router.add_get("/t/{slug}/govip", govip)
