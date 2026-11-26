"""Клиент Miro REST API v2 – только чтение.

Что умеет:
  * разобрать ссылку на фрейм (`https://miro.com/app/board/<доска>/?moveToWidget=<id>`);
  * снять фрейм: сам фрейм, элементы внутри (стикеры, тексты, shapes, картинки,
    карточки) и коннекторы, у которых хотя бы один конец внутри фрейма;
  * скачать картинку оригиналом (`format=original`).

Ничего на доске не меняет: только GET. Токен – env `MIRO_TOKEN` (scope
boards:read), адрес API – env `MIRO_API_BASE` (для тестов – фальшивый Miro).
Токен в логи и исключения не попадает.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

DEFAULT_API_BASE = "https://api.miro.com/v2"


class MiroError(Exception):
    """Ошибка Miro словами человека (без токена)."""


@dataclass(frozen=True)
class FrameRef:
    board_id: str
    item_id: str | None = None      # None – фрейм не указан (ссылка на доску)

    def item_url(self, item_id: str | None = None) -> str:
        """Ссылка «открыть в Miro» на элемент доски."""
        b = urllib.parse.quote(self.board_id, safe="")
        iid = item_id or self.item_id
        return f"https://miro.com/app/board/{b}/" + (f"?moveToWidget={iid}" if iid else "")


_BOARD_RE = re.compile(r"/app/board/([^/?#]+)")


def parse_link(link: str, frame_id: str | None = None) -> FrameRef:
    """Ссылка Miro → доска и элемент.

    Понимает `…/app/board/<доска>/?moveToWidget=<id>` (и `focusWidget`,
    `&cot=…` в хвосте), а также пару «<доска> <id фрейма>» / «<доска>:<id>»."""
    s = (link or "").strip()
    if not s:
        raise MiroError("пустая ссылка на Miro")
    m = _BOARD_RE.search(s)
    if m:
        board = urllib.parse.unquote(m.group(1))
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(s).query)
        item = frame_id or (q.get("moveToWidget") or q.get("focusWidget") or [None])[0]
        return FrameRef(board, item)
    parts = re.split(r"[\s:]+", s)
    if len(parts) == 2 and parts[1].isdigit():
        return FrameRef(parts[0], parts[1])
    if re.fullmatch(r"[A-Za-z0-9_\-]+=*", s):
        return FrameRef(s, frame_id)
    raise MiroError("не похоже на ссылку Miro – нужна ссылка вида "
                    "https://miro.com/app/board/…/?moveToWidget=…")


class MiroClient:
    def __init__(self, token: str | None = None, *, base: str | None = None,
                 transport: httpx.BaseTransport | None = None, timeout: float = 60.0):
        self.token = (token if token is not None else os.environ.get("MIRO_TOKEN", "")).strip()
        if not self.token:
            raise MiroError("не задан токен Miro (переменная окружения MIRO_TOKEN)")
        self.base = (base or os.environ.get("MIRO_API_BASE") or DEFAULT_API_BASE).rstrip("/")
        self._transport = transport
        self._http = httpx.Client(
            timeout=timeout, transport=transport, follow_redirects=True,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ── низкий уровень ──
    def _get(self, url: str, params: dict | None = None) -> Any:
        full = url if url.startswith("http") else self.base + url
        for attempt in range(4):
            try:
                r = self._http.get(full, params=params)
            except httpx.HTTPError as e:
                if attempt == 3:
                    raise MiroError(f"Miro не отвечает: {type(e).__name__}") from None
                time.sleep(1 + attempt)
                continue
            if (r.status_code == 429 or r.status_code >= 500) and attempt < 3:
                time.sleep(float(r.headers.get("Retry-After") or 2))
                continue
            if r.status_code == 401:
                raise MiroError("Miro не принял токен (401) – проверьте MIRO_TOKEN")
            if r.status_code == 403:
                raise MiroError("у токена Miro нет доступа к этой доске (403)")
            if r.status_code == 404:
                raise MiroError("в Miro нет такой доски или элемента (404) – проверьте ссылку")
            if r.status_code >= 400:
                raise MiroError(f"Miro ответил ошибкой {r.status_code}")
            return r.json()
        raise MiroError("Miro не отвечает")

    def _paged(self, path: str, params: dict) -> list[dict]:
        out: list[dict] = []
        cursor = None
        for _ in range(1000):
            p = dict(params)
            if cursor:
                p["cursor"] = cursor
            d = self._get(path, p)
            out += d.get("data", [])
            cursor = d.get("cursor")
            if not cursor:
                return out
        return out

    def _board(self, board_id: str) -> str:
        return "/boards/" + urllib.parse.quote(board_id, safe="")

    # ── высокий уровень ──
    def frames(self, board_id: str) -> list[dict]:
        return self._paged(self._board(board_id) + "/items", {"type": "frame", "limit": 50})

    def item(self, board_id: str, item_id: str) -> dict:
        return self._get(self._board(board_id) + f"/items/{item_id}")

    def fetch_frame(self, ref: FrameRef, *, frame_title: str | None = None) -> dict:
        """Сырой снимок фрейма: {board_id, frame, items, connectors, fetched_at}."""
        b = self._board(ref.board_id)
        if ref.item_id:
            frame = self.item(ref.board_id, ref.item_id)
            if frame.get("type") != "frame":
                # ссылка на элемент внутри фрейма – поднимаемся к родителю
                parent = (frame.get("parent") or {}).get("id")
                if not parent:
                    raise MiroError("ссылка ведёт не на фрейм – откройте в Miro сам фрейм "
                                    "и скопируйте ссылку на него")
                frame = self.item(ref.board_id, str(parent))
        else:
            want = (frame_title or "").strip().upper()
            hit = [f for f in self.frames(ref.board_id)
                   if want and want in (f.get("data", {}).get("title") or "").upper()]
            if len(hit) != 1:
                raise MiroError("в ссылке нет фрейма – выделите фрейм в Miro и скопируйте "
                                "ссылку на него («Copy link»)")
            frame = hit[0]
        fid = str(frame["id"])
        items = self._paged(b + "/items", {"parent_item_id": fid, "limit": 50})
        ids = {str(i["id"]) for i in items} | {fid}
        conns = [c for c in self._paged(b + "/connectors", {"limit": 50})
                 if str((c.get("startItem") or {}).get("id")) in ids
                 or str((c.get("endItem") or {}).get("id")) in ids]
        return {
            "board_id": ref.board_id,
            "frame": frame,
            "items": items,
            "connectors": conns,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def image_original(self, image_url: str) -> tuple[bytes, str]:
        """Картинка оригиналом: (байты, content-type).

        `imageUrl` элемента ведёт на ресурс API; с `format=original&redirect=false`
        он отдаёт JSON с временной ссылкой на файл."""
        u = re.sub(r"format=\w+", "format=original", image_url)
        if "format=" not in u:
            u += ("&" if "?" in u else "?") + "format=original"
        u = re.sub(r"redirect=\w+", "redirect=false", u)
        if "redirect=" not in u:
            u += "&redirect=false"
        if not u.startswith("http"):
            u = self.base + u
        elif u.startswith(DEFAULT_API_BASE) and self.base != DEFAULT_API_BASE:
            u = self.base + u[len(DEFAULT_API_BASE):]
        meta = self._get(u)
        real = meta.get("url") if isinstance(meta, dict) else None
        if not real:
            raise MiroError("Miro не отдал ссылку на картинку")
        # временная ссылка на хранилище – без заголовка авторизации
        try:
            with httpx.Client(transport=self._transport, timeout=120, follow_redirects=True) as h:
                r = h.get(real)
        except httpx.HTTPError as e:
            raise MiroError(f"картинка не скачалась: {type(e).__name__}") from None
        if r.status_code >= 400:
            raise MiroError(f"картинка не скачалась: {r.status_code}")
        return r.content, r.headers.get("content-type", "")


def save_snapshot(snap: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")


def load_snapshot(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
