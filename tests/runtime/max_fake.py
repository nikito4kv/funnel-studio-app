"""Фальшивый MAX Bot API для тестов движка: настоящий HTTP-сервер aiohttp на
127.0.0.1 – клиент runtime/platforms/max_api.py гоняется по-настоящему
(заголовок токена, загрузка файла в два шага, клавиатура во вложениях).

Боевые MAX-боты bot-engine тесты не трогают никогда: базовый адрес – этот сервер.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from aiohttp import web

from runtime.platforms.max_api import MaxApi
from runtime.platforms.max_hub import MaxRoute  # noqa: F401 – удобно импортировать из тестов


class FakeMax:
    def __init__(self, clock=None):
        self.clock = clock
        self.sends: list[dict] = []
        self.edits: list[dict] = []
        self.answers: list[dict] = []
        self.uploads: list[dict] = []
        self.updates: list[dict] = []
        self.polls: list[dict] = []
        self.fail_rules: list = []          # fn(call) -> (status, body) | None
        self.members: dict[tuple[int, int], bool] = {}
        self.not_ready_once: set[str] = set()   # токены видео, которые «ещё обрабатываются» один раз
        self._n = 0
        self._runner: web.AppRunner | None = None
        self.base = ""

    # ── сервер ──
    async def start(self) -> "FakeMax":
        app = web.Application(client_max_size=256 * 1024 * 1024)
        app.router.add_get("/me", self._me)
        app.router.add_post("/messages", self._messages)
        app.router.add_put("/messages", self._edit)
        app.router.add_post("/uploads", self._uploads)
        app.router.add_post("/upload/{n}", self._upload)
        app.router.add_post("/answers", self._answers)
        app.router.add_get("/updates", self._updates)
        app.router.add_get("/chats/{chat}/members", self._members)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        return self

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    def api(self, token: str) -> MaxApi:
        return MaxApi(token, self.base, not_ready_pause=0.01)

    def _now(self) -> datetime | None:
        return self.clock.now() if self.clock else None

    def _next(self) -> int:
        self._n += 1
        return self._n

    # ── обработчики ──
    async def _me(self, req: web.Request):
        tok = req.headers.get("Authorization", "")
        return web.json_response({"user_id": 70000 + (abs(hash(tok)) % 1000), "is_bot": True,
                                  "name": "Fake", "username": "fake_max_bot"})

    async def _messages(self, req: web.Request):
        body = await req.json()
        call = {"user_id": int(req.query.get("user_id", 0)), "text": body.get("text", ""),
                "attachments": body.get("attachments") or [], "format": body.get("format"),
                "token": req.headers.get("Authorization"), "at": self._now()}
        for rule in self.fail_rules:
            r = rule(call)
            if r is not None:
                call["error"] = r
                self.sends.append(call)
                return web.json_response(r[1], status=r[0])
        for a in call["attachments"]:
            tok = (a.get("payload") or {}).get("token")
            if a.get("type") == "video" and tok in self.not_ready_once:
                self.not_ready_once.discard(tok)
                return web.json_response({"code": "attachment.not.ready",
                                          "message": "Key: errors.process.attachment.file.not.processed"},
                                         status=400)
        mid = f"mid.{self._next()}"
        call["mid"] = mid
        self.sends.append(call)
        return web.json_response({"message": {"body": {"mid": mid, "text": call["text"]}}})

    async def _edit(self, req: web.Request):
        body = await req.json()
        mid = req.query.get("message_id", "")
        call = {"mid": mid, "text": body.get("text"), "attachments": body.get("attachments"),
                "format": body.get("format"), "at": self._now()}
        for rule in self.fail_rules:
            r = rule({**call, "edit": True, "user_id": self.owner(mid)})
            if r is not None:
                call["error"] = r
                self.edits.append(call)
                return web.json_response(r[1], status=r[0])
        if not any(c.get("mid") == mid for c in self.sends):
            call["error"] = (404, {"code": "not.found", "message": "message not found"})
            self.edits.append(call)
            return web.json_response(call["error"][1], status=404)
        self.edits.append(call)
        return web.json_response({"success": True})

    def owner(self, mid: str) -> int:
        return next((c["user_id"] for c in self.sends if c.get("mid") == mid), 0)

    async def _uploads(self, req: web.Request):
        n = self._next()
        kind = req.query.get("type")
        out = {"url": f"{self.base}/upload/{n}?type={kind}"}
        if kind == "video":
            out["token"] = f"vtok{n}"
        return web.json_response(out)

    async def _upload(self, req: web.Request):
        n = req.match_info["n"]
        kind = req.query.get("type")
        form = await req.post()
        f = next(iter(form.values()))
        data = f.file.read() if hasattr(f, "file") else b""
        self.uploads.append({"kind": kind, "filename": getattr(f, "filename", None), "size": len(data),
                             "token": req.headers.get("Authorization")})
        if kind == "image":
            return web.json_response({"photos": {"p1": {"token": f"itok{n}"}}})
        return web.json_response({"id": int(n)})

    async def _answers(self, req: web.Request):
        body = await req.json()
        self.answers.append({"callback_id": req.query.get("callback_id"), **body})
        return web.json_response({"success": True})

    async def _updates(self, req: web.Request):
        marker = int(req.query.get("marker") or 0)
        self.polls.append({"marker": marker, "types": req.query.get("types")})
        ups = self.updates[marker:]
        return web.json_response({"updates": ups, "marker": len(self.updates)})

    async def _members(self, req: web.Request):
        chat = int(req.match_info["chat"])
        uid = int(req.query.get("user_ids", "0"))
        if not any(c == chat for c, _ in self.members):
            return web.json_response({"code": "chat.denied", "message": "not admin"}, status=403)
        ok = self.members.get((chat, uid), False)
        return web.json_response({"members": [{"user_id": uid}] if ok else []})

    # ── удобства тестов ──
    def of(self, uid: int) -> list[dict]:
        return [c for c in self.sends if c["user_id"] == uid and "error" not in c]

    def edits_of(self, uid: int) -> list[dict]:
        return [e for e in self.edits if self.owner(e["mid"]) == uid and "error" not in e]

    @staticmethod
    def buttons(call: dict) -> list[dict]:
        for a in call["attachments"] or []:
            if a.get("type") == "inline_keyboard":
                return [b for row in a["payload"]["buttons"] for b in row]
        return []

    @staticmethod
    def media(call: dict) -> list[str]:
        return [a["type"] for a in call["attachments"] if a.get("type") != "inline_keyboard"]


# ── апдейты MAX ──────────────────────────────────────────────────────────────

def _ts(clock) -> int:
    return int(clock.now().timestamp() * 1000) if clock else 0


def bot_started(uid: int, payload: str = "", name: str = "Маша", clock=None) -> dict:
    return {"update_type": "bot_started", "timestamp": _ts(clock) or uuid.uuid4().int % 10**12,
            "user": {"user_id": uid, "name": name}, "payload": payload, "chat_id": uid * 10}


def message(uid: int, text: str = "", attachments=None, name: str = "Маша") -> dict:
    return {"update_type": "message_created",
            "message": {"sender": {"user_id": uid, "name": name}, "recipient": {"chat_id": uid * 10},
                        "body": {"mid": f"in.{uuid.uuid4().hex[:10]}", "text": text,
                                 "attachments": attachments or []}}}


def contact(uid: int, phone: str = "+7 999 123-45-67") -> dict:
    vcf = f"BEGIN:VCARD\nVERSION:3.0\nFN:Маша\nTEL;TYPE=cell:{phone}\nEND:VCARD"
    return message(uid, "", [{"type": "contact", "payload": {"vcf_info": vcf, "max_info": None}}])


def callback(uid: int, payload: str) -> dict:
    return {"update_type": "message_callback",
            "callback": {"callback_id": f"cb{uuid.uuid4().hex[:8]}", "payload": payload,
                         "user": {"user_id": uid, "name": "Маша"}},
            "message": {"body": {"mid": "mid.x"}}}


def stopped(uid: int) -> dict:
    return {"update_type": "bot_stopped", "user": {"user_id": uid, "name": "Маша"}}


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, default=str)
