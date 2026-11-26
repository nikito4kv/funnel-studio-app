"""Клиент MAX Bot API (async, aiohttp).

Перенос нужного из bot-engine `common/max/api.py` (коммит f171a355) с правками:
  * base_url задаётся (тесты гоняют клиента против фальшивого MAX по HTTP);
  * загрузка файла байтами – одним вызовом `upload()` → токен вложения
    (кэширует его отправитель, в media_cache по (sha256, бот, вид));
  * 429 – пауза и один повтор, а не сразу ошибка;
  * «вложение ещё обрабатывается» после заливки видео – несколько повторов с
    паузой (MAX отвечает так на свежезалитый ролик).

Факты о протоколе (dev.max.ru/docs-api, сверено в bot-engine 23.04.2026):
  * https://platform-api.max.ru, заголовок Authorization = токен БЕЗ «Bearer»;
  * лимит 30 запросов в секунду на бота;
  * апдейт: {update_type, timestamp (мс), …}; у сообщения sender/recipient/body;
  * у message_callback `callback` и `message` лежат рядом, на верхнем уровне;
  * клавиатура – вложение {type: inline_keyboard, payload: {buttons: [[…]]}}.
Прокси не нужен: MAX российский и доступен напрямую.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

log = logging.getLogger("runtime.max.api")

DEFAULT_BASE_URL = "https://platform-api.max.ru"
UPDATE_TYPES = ["bot_started", "bot_stopped", "message_created", "message_callback",
                "dialog_removed"]


class MaxApiError(Exception):
    def __init__(self, code: int, body: Any = None, url: str = ""):
        self.code = code
        self.body = body
        self.url = url
        super().__init__(f"MAX API {code} @ {url}: {body}")


def is_block_error(e: Exception) -> bool:
    """Человек заблокировал/удалил бота – навсегда (не сеть и не лимит)."""
    if isinstance(e, MaxApiError) and e.code == 403:
        return True
    s = str(e).lower()
    return "chat.denied" in s or "dialog.suspended" in s or "dialog.denied" in s \
        or "user.blocked" in s


def _not_ready(e: MaxApiError) -> bool:
    s = str(e.body).lower()
    return "not.ready" in s or "not.processed" in s or "attachment.not" in s


def extract_upload_token(resp) -> str | None:
    """Ответ загрузки бывает трёх видов: {"token"}, {"photos": {id: {"token"}}},
    {"image"|"video"|…: {"token"}}."""
    if not isinstance(resp, dict):
        return None
    if resp.get("token"):
        return resp["token"]
    for key in ("photos", "videos"):
        node = resp.get(key)
        if isinstance(node, dict):
            for v in node.values():
                if isinstance(v, dict) and v.get("token"):
                    return v["token"]
    for key in ("image", "video", "file", "audio"):
        node = resp.get(key)
        if isinstance(node, dict) and node.get("token"):
            return node["token"]
    return None


class MaxApi:
    def __init__(self, token: str, base_url: str = DEFAULT_BASE_URL, *,
                 not_ready_pause: float = 1.5, not_ready_tries: int = 5):
        if not token:
            raise ValueError("MaxApi: пустой токен")
        self.token = token
        self.base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        self._sem = asyncio.Semaphore(5)
        self.not_ready_pause = not_ready_pause
        self.not_ready_tries = not_ready_tries

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": self.token},
                timeout=aiohttp.ClientTimeout(total=120))
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    # ── запросы ──────────────────────────────────────────────────────────
    async def _request(self, method: str, path: str, *, params: dict | None = None,
                       json_body: dict | None = None, timeout: float | None = None) -> Any:
        url = self.base_url + path
        for attempt in (1, 2):
            session = await self._get_session()
            async with self._sem:
                kw = {}
                if timeout is not None:
                    kw["timeout"] = aiohttp.ClientTimeout(total=timeout)
                async with session.request(method, url, params=params, json=json_body, **kw) as resp:
                    raw = await resp.text()
                    status = resp.status
            if status == 429 and attempt == 1:
                log.warning("MAX просит сбавить темп (429) – пауза 2 с и повтор")
                await asyncio.sleep(2)
                continue
            if status >= 400:
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = raw
                raise MaxApiError(status, body, url)
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except ValueError:
                return {"raw": raw}
        raise MaxApiError(429, "too many requests", url)  # pragma: no cover

    async def get_me(self) -> dict:
        return await self._request("GET", "/me")

    async def send_message(self, *, user_id: int | None = None, chat_id: int | None = None,
                           text: str = "", attachments: list[dict] | None = None,
                           format: str | None = "html", notify: bool = True,
                           disable_link_preview: bool = False) -> dict:
        if (user_id is None) == (chat_id is None):
            raise ValueError("send_message: нужен ровно один из user_id / chat_id")
        params: dict[str, Any] = {"user_id": user_id} if user_id is not None else {"chat_id": chat_id}
        if disable_link_preview:
            params["disable_link_preview"] = "true"
        body: dict[str, Any] = {"notify": notify}
        if text:
            body["text"] = text
        if attachments:
            body["attachments"] = attachments
        if format and text:
            body["format"] = format
        tries = self.not_ready_tries if attachments else 1
        for i in range(tries):
            try:
                return await self._request("POST", "/messages", params=params, json_body=body)
            except MaxApiError as e:
                if i + 1 < tries and _not_ready(e):
                    log.info("MAX: вложение ещё обрабатывается – повтор через %.1f с",
                             self.not_ready_pause)
                    await asyncio.sleep(self.not_ready_pause)
                    continue
                raise
        return {}  # pragma: no cover

    async def edit_message(self, message_id: str, *, text: str | None = None,
                           attachments: list[dict] | None = None, format: str | None = "html") -> dict:
        """PUT /messages?message_id=… – правка своего сообщения (как `edit_message`
        в bot-engine common/max/api.py). ⚠️ `attachments` заменяются ЦЕЛИКОМ: список
        без картинки уберёт картинку, `[]` – все вложения (и клавиатуру);
        None – вложения не трогаем."""
        body: dict[str, Any] = {}
        if text is not None:
            body["text"] = text
            if format:
                body["format"] = format
        if attachments is not None:
            body["attachments"] = attachments
        return await self._request("PUT", "/messages", params={"message_id": message_id},
                                   json_body=body)

    async def answer_callback(self, callback_id: str, notification: str | None = None) -> dict:
        body: dict[str, Any] = {}
        if notification:
            body["notification"] = notification
        return await self._request("POST", "/answers", params={"callback_id": callback_id},
                                   json_body=body)

    async def get_updates(self, marker: int | None = None, timeout: int = 30, limit: int = 100,
                          types: list[str] | None = None) -> dict:
        params: dict[str, Any] = {"timeout": max(0, min(timeout, 90)),
                                  "limit": max(1, min(limit, 1000))}
        if marker is not None:
            params["marker"] = marker
        if types:
            params["types"] = ",".join(types)
        return await self._request("GET", "/updates", params=params, timeout=timeout + 30)

    async def is_chat_member(self, chat_id: int, user_id: int) -> bool | None:
        """Подписан ли человек на канал. None – проверить нельзя (бот не админ и т.п.)."""
        try:
            r = await self._request("GET", f"/chats/{chat_id}/members",
                                    params={"user_ids": str(user_id)})
        except MaxApiError as e:
            log.warning("MAX: участники канала %s недоступны: %s", chat_id, e)
            return None
        members = r.get("members", []) if isinstance(r, dict) else []
        return len(members) > 0

    # ── загрузка файлов ──────────────────────────────────────────────────
    async def upload(self, kind: str, data: bytes, filename: str,
                     content_type: str = "application/octet-stream") -> str:
        """Байты → токен вложения (kind: image / video / file). Два шага:
        POST /uploads?type=… → {url, token?}; POST файла на url → {token…}."""
        if kind not in ("image", "video", "audio", "file"):
            raise ValueError(f"неизвестный вид загрузки: {kind}")
        up = await self._request("POST", "/uploads", params={"type": kind})
        url = up.get("url") if isinstance(up, dict) else None
        if not url:
            raise MaxApiError(500, f"нет адреса загрузки: {up!r}", "/uploads")
        pre_token = up.get("token") if isinstance(up, dict) else None
        session = await self._get_session()
        form = aiohttp.FormData()
        form.add_field("file", data, filename=filename, content_type=content_type)
        async with self._sem:
            async with session.post(url, data=form) as resp:
                raw = await resp.text()
                if resp.status >= 400:
                    raise MaxApiError(resp.status, raw, url)
        try:
            parsed = json.loads(raw) if raw else {}
        except ValueError:
            parsed = {}
        token = extract_upload_token(parsed) or pre_token
        if not token:
            raise MaxApiError(500, f"загрузка не вернула токен: {raw[:200]!r}", url)
        return token

    async def download(self, url: str) -> tuple[bytes, str]:
        """Скачать файл по ссылке (картинка шага задана URL) → (байты, content-type)."""
        # отдельная сессия: токен бота чужому сайту не отдаём
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
            async with s.get(url) as resp:
                if resp.status >= 400:
                    raise MaxApiError(resp.status, "не скачалось", url)
                return await resp.read(), resp.headers.get("Content-Type",
                                                           "application/octet-stream")
