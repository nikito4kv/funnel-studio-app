"""Фальшивый Bot API для тестов и e2e раздела «Боты» – настоящий Telegram в тестах не трогаем.

  * для pytest – `FakeBotApi().transport()` (httpx.MockTransport) → `app.state.bot_api_transport`;
  * для e2e – `python -m studio.bots.fake --port 8799` (HTTP-сервер), сервер Студии с
    `STUDIO_TG_API=http://127.0.0.1:8799`. Служебные адреса: `GET /__calls` – журнал вызовов,
    `POST /__reset`.

Помнит профиль каждого бота (имя, описания, команды, аватар) и записывает ВСЕ вызовы. Метод не
из списка профиля (getUpdates, setWebhook, sendMessage…) отвечает ошибкой и попадает в
`forbidden` – тесты проверяют, что там пусто.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

from .telegram import ALLOWED

# два бота: тестовый и «боевой» (его username есть в launches/webinar/launch.yaml of the demo bot repository)
TEST_TOKEN = "1111111111:AAtestTESTtestTESTtestTESTtest_0001"
COMBAT_TOKEN = "2222222222:AAcombatCOMBATcombatCOMBATcomb_0002"


def _png(color=(47, 98, 224), size=(320, 320)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def default_bots() -> dict[str, dict[str, Any]]:
    return {
        TEST_TOKEN: {"id": 1111111111, "username": "e2e_studio_TEST_bot", "first_name": "Тест Студии",
                     "name": "Тест Студии", "description": "Тестовый бот Студии", "short_description": "Для проверок",
                     "commands": [{"command": "start", "description": "Начать"}], "photos": [_png()]},
        COMBAT_TOKEN: {"id": 2222222222, "username": "northwind_workshop_bot", "first_name": "Таблицы без рутины",
                       "name": "Таблицы без рутины", "description": "Практикум «Таблицы без рутины»",
                       "short_description": "Эфир 17.09", "commands": [], "photos": []},
    }


class FakeBotApi:
    def __init__(self, bots: dict[str, dict[str, Any]] | None = None):
        self.bots = bots if bots is not None else default_bots()
        self.calls: list[dict[str, Any]] = []
        self.forbidden: list[str] = []
        self.files: dict[str, bytes] = {}
        self.fail_next: dict[str, tuple[int, str, int | None]] = {}
        self._lock = threading.Lock()

    # ── разбор запроса ──
    @staticmethod
    def _multipart(ctype: str, body: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        fields: dict[str, Any] = {}
        files: dict[str, bytes] = {}
        if not m:
            return fields, files
        for part in body.split(b"--" + m.group(1).encode()):
            if b"\r\n\r\n" not in part:
                continue
            head, data = part.split(b"\r\n\r\n", 1)
            data = data[:-2] if data.endswith(b"\r\n") else data
            h = head.decode("utf-8", "replace")
            name = re.search(r'name="([^"]+)"', h)
            if not name:
                continue
            if "filename=" in h:
                files[name.group(1)] = data
            else:
                v = data.decode("utf-8")
                try:
                    fields[name.group(1)] = json.loads(v)
                except ValueError:
                    fields[name.group(1)] = v
        return fields, files

    def handle(self, path: str, ctype: str, body: bytes) -> tuple[int, dict[str, Any] | bytes]:
        m = re.match(r"^/file/bot([^/]+)/(.+)$", path)
        if m:
            data = self.files.get(m.group(2))
            if m.group(1) not in self.bots or data is None:
                return 404, {"ok": False, "error_code": 404, "description": "Not Found"}
            return 200, data
        m = re.match(r"^/bot([^/]+)/([A-Za-z]+)$", path)
        if not m:
            return 404, {"ok": False, "error_code": 404, "description": "Not Found"}
        token, method = m.group(1), m.group(2)
        if ctype.startswith("multipart/"):
            params, files = self._multipart(ctype, body)
        else:
            params, files = (json.loads(body or b"{}") if body else {}), {}
        with self._lock:
            self.calls.append({"method": method, "bot": token.split(":")[0], "params": params})
            if method not in ALLOWED:
                self.forbidden.append(method)
                return 400, {"ok": False, "error_code": 400,
                             "description": f"фальшивый Bot API: {method} Студии запрещён"}
            bot = self.bots.get(token)
            if bot is None:
                return 401, {"ok": False, "error_code": 401, "description": "Unauthorized"}
            if method in self.fail_next:
                code, desc, retry = self.fail_next.pop(method)
                out = {"ok": False, "error_code": code, "description": desc}
                if retry:
                    out["parameters"] = {"retry_after": retry}
                return code, out
            try:
                return 200, {"ok": True, "result": self._run(bot, method, params, files)}
            except ValueError:
                return 400, {"ok": False, "error_code": 400, "description": "Bad Request: PHOTO_INVALID"}

    def _run(self, bot: dict[str, Any], method: str, p: dict[str, Any], files: dict[str, bytes]) -> Any:
        if method == "getMe":
            return {"id": bot["id"], "is_bot": True, "first_name": bot["first_name"], "username": bot["username"],
                    "can_join_groups": True, "can_read_all_group_messages": False,
                    "supports_inline_queries": False, "has_main_web_app": False}
        if method == "getMyName":
            return {"name": bot["name"]}
        if method == "getMyDescription":
            return {"description": bot["description"]}
        if method == "getMyShortDescription":
            return {"short_description": bot["short_description"]}
        if method == "getMyCommands":
            return bot["commands"]
        if method == "getUserProfilePhotos":
            sets = [[{"file_id": f"{bot['id']}_{i}_{s}", "file_unique_id": f"u{i}{s}", "width": s, "height": s}
                     for s in (160, 640)] for i, _ in enumerate(bot["photos"][:1])]
            return {"total_count": len(bot["photos"]), "photos": sets}
        if method == "getFile":
            fid = str(p.get("file_id", ""))
            idx = int(fid.split("_")[1]) if fid.count("_") >= 2 else 0
            path = f"photos/{fid}.jpg"
            self.files[path] = bot["photos"][idx]
            return {"file_id": fid, "file_path": path}
        if method == "setMyName":
            bot["name"] = p.get("name", "")
            bot["first_name"] = bot["name"] or bot["first_name"]
            return True
        if method == "setMyDescription":
            bot["description"] = p.get("description", "")
            return True
        if method == "setMyShortDescription":
            bot["short_description"] = p.get("short_description", "")
            return True
        if method == "setMyCommands":
            bot["commands"] = p.get("commands", [])
            return True
        if method == "setMyProfilePhoto":
            photo = p.get("photo") or {}
            att = str(photo.get("photo", "")).removeprefix("attach://")
            data = files.get(att)
            if photo.get("type") != "static" or not data or data[:2] != b"\xff\xd8":
                raise ValueError("photo")
            bot["photos"].insert(0, data)
            return True
        return True

    # ── транспорт для httpx (pytest) ──
    def transport(self) -> httpx.MockTransport:
        def h(req: httpx.Request) -> httpx.Response:
            code, out = self.handle(req.url.path, req.headers.get("content-type", ""), req.content)
            if isinstance(out, bytes):
                return httpx.Response(code, content=out, headers={"content-type": "image/jpeg"})
            return httpx.Response(code, json=out)
        return httpx.MockTransport(h)

    def methods(self) -> list[str]:
        return [c["method"] for c in self.calls]


def serve(port: int) -> None:
    fake = FakeBotApi()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):   # тихо
            pass

        def _send(self, code: int, out: dict | bytes):
            data = out if isinstance(out, bytes) else json.dumps(out, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "image/jpeg" if isinstance(out, bytes) else "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/__calls":
                return self._send(200, {"calls": fake.calls, "forbidden": fake.forbidden,
                                        "bots": {t[:4]: {k: v for k, v in b.items() if k != "photos"}
                                                 | {"photos": len(b["photos"])} for t, b in fake.bots.items()}})
            self._send(*fake.handle(self.path, "", b""))

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path == "/__reset":
                nonlocal fake
                fake = FakeBotApi()
                return self._send(200, {"ok": True})
            self._send(*fake.handle(self.path, self.headers.get("Content-Type", ""), body))

    print(f"фальшивый Bot API: http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="python -m studio.bots.fake")
    ap.add_argument("--port", type=int, default=8799)
    serve(ap.parse_args().port)
