"""Фальшивый Miro API для тестов: отвечает по сохранённому снимку фрейма.

    uv run python -m studio.miro.fake tests/fixtures/miro/workshop.json --port 8799
    MIRO_TOKEN=x MIRO_API_BASE=http://127.0.0.1:8799/v2 uv run python -m studio.server …

Отдаёт фрейм, элементы и коннекторы страницами с курсором (как Miro), картинку по
`format=original&redirect=false` – JSON со ссылкой на файл, сам файл – цветной PNG.
`--board-edit <файл.json>` – второй снимок: после первого снятия фрейма сервер начнёт
отдавать его (для проверки повторного импорта).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

PAGE = 20


def _png(seed: str) -> bytes:
    from PIL import Image
    h = hashlib.sha256(seed.encode()).digest()
    img = Image.new("RGB", (320, 180), (h[0], h[1], h[2]))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class FakeMiro:
    def __init__(self, snap: dict, base: str = "http://fake-miro/v2", token: str | None = None):
        self.snaps = [snap]
        self.base = base.rstrip("/")
        self.token = token
        self.frame_fetches = 0

    @property
    def snap(self) -> dict:
        return self.snaps[min(self.frame_fetches, len(self.snaps)) - 1] if self.frame_fetches else self.snaps[0]

    def answer(self, path: str, q: dict[str, str], auth: str) -> tuple[int, str, bytes]:
        if path.startswith("/files/"):
            return 200, "image/png", _png(path)
        if self.token and auth != f"Bearer {self.token}":
            return 401, "application/json", b'{"message":"unauthorized"}'
        s = self.snap
        frame = s["frame"]
        if path.endswith(f"/items/{frame['id']}"):
            self.frame_fetches += 1
            return 200, "application/json", json.dumps(self.snap["frame"]).encode()
        if "/resources/images/" in path:
            img_id = path.rsplit("/", 1)[-1]
            return 200, "application/json", json.dumps(
                {"url": f"{self.base.rsplit('/v2', 1)[0]}/files/{img_id}.png"}).encode()
        if path.endswith("/items") or path.endswith("/connectors"):
            data = s["items"] if path.endswith("/items") else s["connectors"]
            if q.get("parent_item_id") and q["parent_item_id"] != str(frame["id"]):
                data = []
            if q.get("type") == "frame":
                data = [frame]
            start = int(q.get("cursor") or 0)
            body = {"data": data[start:start + PAGE]}
            if start + PAGE < len(data):
                body["cursor"] = str(start + PAGE)
            return 200, "application/json", json.dumps(body).encode()
        return 404, "application/json", b'{"message":"not found"}'

    def transport(self) -> httpx.MockTransport:
        def handler(req: httpx.Request) -> httpx.Response:
            q = {k: v for k, v in req.url.params.items()}
            code, ctype, body = self.answer(req.url.path, q, req.headers.get("authorization", ""))
            return httpx.Response(code, content=body, headers={"content-type": ctype})
        return httpx.MockTransport(handler)


def serve(snap: dict, port: int, edited: dict | None = None) -> None:
    fake = FakeMiro(snap, base=f"http://127.0.0.1:{port}/v2")
    if edited:
        fake.snaps.append(edited)

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            code, ctype, body = fake.answer(u.path, q, self.headers.get("Authorization", ""))
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    print(f"фальшивый Miro: http://127.0.0.1:{port}/v2", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshot")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--board-edit", help="второй снимок – для повторного импорта")
    a = ap.parse_args()
    snap = json.loads(Path(a.snapshot).read_text(encoding="utf-8"))
    edited = json.loads(Path(a.board_edit).read_text(encoding="utf-8")) if a.board_edit else None
    serve(snap, a.port, edited)


if __name__ == "__main__":
    main()
