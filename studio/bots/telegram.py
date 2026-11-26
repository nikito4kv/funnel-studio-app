"""Клиент Bot API для раздела «Боты» – ТОЛЬКО методы профиля бота.

В раздел добавляют и боевые боты компании, которых прямо сейчас поллит bot-engine.
Один токен – один поллер: любой `getUpdates`, `setWebhook`, `deleteWebhook`,
`logOut`, `close` из Студии уронил бы боевого бота, а отправка сообщений ушла бы
живым людям. Поэтому клиент знает короткий белый список и на всё остальное
бросает `ForbiddenMethod` ещё до сети:

  чтение (при открытии):  getMe, getMyName, getMyDescription, getMyShortDescription,
                          getMyCommands, getUserProfilePhotos, getFile (+ скачать файл аватара)
  запись (по кнопке):     setMyName, setMyDescription, setMyShortDescription,
                          setMyProfilePhoto, setMyCommands

Токен в сообщения об ошибках и в журнал не попадает: ошибки сети – только имя
исключения, у логгеров httpx/httpcore – фильтр, который вырезает `bot<токен>`
из адресов. Базовый адрес – `STUDIO_TG_API` (тесты и e2e – фальшивый Bot API),
прокси – `STUDIO_TG_PROXY`, как у тест-прогона.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

DEFAULT_API = "https://api.telegram.org"

READ_METHODS = frozenset({
    "getMe", "getMyName", "getMyDescription", "getMyShortDescription", "getMyCommands",
    "getUserProfilePhotos", "getFile",
})
WRITE_METHODS = frozenset({
    "setMyName", "setMyDescription", "setMyShortDescription", "setMyProfilePhoto", "setMyCommands",
})
ALLOWED = READ_METHODS | WRITE_METHODS
# для документации и тестов: что Студия не вызывает никогда
FORBIDDEN_EXAMPLES = ("getUpdates", "setWebhook", "deleteWebhook", "logOut", "close", "sendMessage",
                      "sendPhoto", "removeMyProfilePhoto", "getWebhookInfo")

TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,64}$")
_IN_TEXT = re.compile(r"(bot)?\d{5,15}:[A-Za-z0-9_-]{30,64}")


def scrub(text: str) -> str:
    """Вырезать токены из произвольного текста (адреса, сообщения исключений)."""
    return _IN_TEXT.sub(lambda m: (m.group(1) or "") + "<токен>", text or "")


def mask(token: str | None) -> str:
    """Для глаз: только последние 4 символа – «…a1B2»."""
    return "…" + token[-4:] if token and len(token) >= 8 else "…"


class _ScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        clean = scrub(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        return True


def _install_log_scrub() -> None:
    f = _ScrubFilter()
    for name in ("httpx", "httpcore", "studio.bots", "studio.server"):
        lg = logging.getLogger(name)
        if not any(isinstance(x, _ScrubFilter) for x in lg.filters):
            lg.addFilter(f)
    # httpx пишет каждый запрос с адресом (а в адресе Bot API – токен) на INFO – нам это не нужно
    logging.getLogger("httpx").setLevel(max(logging.getLogger("httpx").level, logging.WARNING))


_install_log_scrub()


class ForbiddenMethod(RuntimeError):
    """Метод не из белого списка профиля – Студия его не вызывает."""


class BotApiError(RuntimeError):
    """Telegram ответил отказом или не ответил. Текст – для человека, без токена."""

    def __init__(self, text: str, *, code: int | None = None, retry_after: int | None = None):
        super().__init__(text)
        self.code = code
        self.retry_after = retry_after


def api_base() -> str:
    return (os.environ.get("STUDIO_TG_API") or DEFAULT_API).rstrip("/")


def human_error(method: str, code: int | None, desc: str, retry_after: int | None) -> str:
    d = (desc or "").strip()
    if code == 401 or (code == 404 and method == "getMe"):
        return ("Telegram не знает такой токен. Возможно, его отозвали в @BotFather – "
                "возьмите актуальный: @BotFather → /mybots → бот → API Token")
    if code == 429:
        wait = f" {retry_after} с" if retry_after else ""
        return (f"Telegram просит подождать{wait}: имя и описания бота нельзя менять слишком часто. "
                "Попробуйте позже")
    if "photo" in d.lower() and method == "setMyProfilePhoto":
        return f"Telegram не принял картинку: {d}"
    return f"Telegram отказал ({method}): {scrub(d) or f'код {code}'}"


class BotApi:
    """Один бот – один клиент. Синхронный: сервер Студии синхронный, вызовы редкие."""

    def __init__(self, token: str, *, base: str | None = None,
                 transport: httpx.BaseTransport | None = None, timeout: float = 20):
        if not TOKEN_RE.match(token or ""):
            raise BotApiError("Это не похоже на токен бота. Он выглядит так: 123456789:AAH…, "
                              "скопируйте его целиком из @BotFather (API Token)")
        self._token = token
        self.base = (base or api_base()).rstrip("/")
        proxy = os.environ.get("STUDIO_TG_PROXY", "").strip() or None
        self._client = httpx.Client(timeout=timeout, transport=transport,
                                    proxy=None if transport is not None else proxy)

    def __repr__(self) -> str:  # токен не печатается даже в отладке
        return f"<BotApi {mask(self._token)}>"

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── низкий уровень ──
    def call(self, method: str, params: dict[str, Any] | None = None,
             files: dict[str, tuple[str, bytes, str]] | None = None) -> Any:
        if method not in ALLOWED:
            raise ForbiddenMethod(f"Студия не вызывает {method}: раздел «Боты» меняет только профиль бота, "
                                  "чтобы не мешать тому, кто его поллит")
        url = f"{self.base}/bot{self._token}/{method}"
        try:
            if files:
                form = {k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                        for k, v in (params or {}).items()}
                r = self._client.post(url, data=form, files=files)
            else:
                r = self._client.post(url, json=params or {})
        except httpx.HTTPError as e:
            raise BotApiError(f"Telegram не отвечает ({type(e).__name__}). Если сервер в РФ – нужен "
                              "прокси STUDIO_TG_PROXY") from None
        try:
            body = r.json()
        except ValueError:
            body = {}
        if not body.get("ok"):
            params_ = body.get("parameters") or {}
            retry = params_.get("retry_after")
            code = body.get("error_code") or r.status_code
            raise BotApiError(human_error(method, code, body.get("description") or "", retry),
                              code=code, retry_after=retry)
        return body.get("result")

    def download(self, file_path: str) -> bytes:
        """Скачать файл по `file_path` из getFile (аватар). Адрес с токеном наружу не отдаётся."""
        if not file_path or ".." in file_path:
            raise BotApiError("неверный путь файла от Telegram")
        try:
            r = self._client.get(f"{self.base}/file/bot{self._token}/{file_path}")
        except httpx.HTTPError as e:
            raise BotApiError(f"Telegram не отдал файл ({type(e).__name__})") from None
        if r.status_code != 200:
            raise BotApiError(f"Telegram не отдал файл (HTTP {r.status_code})")
        return r.content

    # ── профиль ──
    def me(self) -> dict:
        return self.call("getMe")

    def profile(self) -> dict:
        """Всё, что показывает раздел: из Telegram, а не из нашего кэша."""
        me = self.me()
        commands = self.call("getMyCommands") or []
        return {
            "me": me,
            "name": (self.call("getMyName") or {}).get("name", ""),
            "description": (self.call("getMyDescription") or {}).get("description", ""),
            "short_description": (self.call("getMyShortDescription") or {}).get("short_description", ""),
            "commands": [{"command": c.get("command", ""), "description": c.get("description", "")}
                         for c in commands],
        }

    def avatar(self, bot_id: int) -> bytes | None:
        """Аватар бота (самый крупный размер первой фотографии) или None."""
        photos = self.call("getUserProfilePhotos", {"user_id": bot_id, "limit": 1}) or {}
        sets = photos.get("photos") or []
        if not sets or not sets[0]:
            return None
        best = max(sets[0], key=lambda p: (p.get("width") or 0) * (p.get("height") or 0))
        f = self.call("getFile", {"file_id": best["file_id"]}) or {}
        return self.download(f.get("file_path") or "")

    def set_name(self, value: str) -> None:
        self.call("setMyName", {"name": value})

    def set_description(self, value: str) -> None:
        self.call("setMyDescription", {"description": value})

    def set_short_description(self, value: str) -> None:
        self.call("setMyShortDescription", {"short_description": value})

    def set_commands(self, commands: list[dict]) -> None:
        self.call("setMyCommands", {"commands": commands})

    def set_photo(self, jpeg: bytes) -> None:
        self.call("setMyProfilePhoto", {"photo": {"type": "static", "photo": "attach://avatar"}},
                  files={"avatar": ("avatar.jpg", jpeg, "image/jpeg")})
