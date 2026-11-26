"""Провайдеры звонков и SMS – за интерфейсом.

Боевые клиенты повторяют services/telephony.py и services/sms.py bot-engine
(zvonobot.ru, p1sms.ru). Выбор – настройкой запуска:
`telephony.provider` / `sms.provider`, ключ – ТОЛЬКО ссылкой `env:ИМЯ`
(сам ключ в базу не пишется, как токены ботов). `stub` – ничего не отправляет,
только пишет в лог (для проверки воронки без денег и без звонков людям).

Тесты подменяют провайдера через `register(канал, имя, фабрика)`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Protocol

import aiohttp

from ..config import SecretError, resolve_secret
from .config import ProviderSettings

log = logging.getLogger("runtime.steps.providers")


@dataclass
class ProviderResult:
    ok: bool
    detail: str = ""


class Telephony(Protocol):
    name: str

    async def dial(self, phone: str, *, audio_url: str | None, audio_id: int | None,
                   text: str | None) -> ProviderResult: ...


class Sms(Protocol):
    name: str

    async def send(self, phone: str, text: str) -> ProviderResult: ...


def norm_phone(raw: str | None) -> str | None:
    """11 цифр, начинается с 7 (как _norm_phone bot-engine)."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if len(digits) == 10 and not digits.startswith("7"):
        digits = "7" + digits
    if len(digits) != 11 or not digits.startswith("7"):
        return None
    return digits


def mask_phone(phone: str | None) -> str:
    return f"…{phone[-4:]}" if phone else "–"


async def _post(url: str, body: dict) -> ProviderResult:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.post(url, json=body) as resp:
            status, data = resp.status, await resp.text()
    return ProviderResult(200 <= status < 300, f"HTTP {status}: {data[:300]}")


class ZvonobotTelephony:
    """POST https://lk.zvonobot.ru/apiCalls/create (как ZvonobotTelephony bot-engine)."""
    name = "zvonobot"
    API_URL = "https://lk.zvonobot.ru/apiCalls/create"

    def __init__(self, api_key: str, outgoing_phone_id: int | None = None):
        self.api_key, self.outgoing_phone_id = api_key, outgoing_phone_id

    async def dial(self, phone, *, audio_url=None, audio_id=None, text=None) -> ProviderResult:
        body: dict = {"apiKey": self.api_key, "phone": phone}
        if self.outgoing_phone_id:
            body["outgoingPhoneId"] = self.outgoing_phone_id
        # приоритет как в движке: запись из кабинета > ссылка на аудио > синтез речи
        if audio_id:
            body["record"] = {"id": int(audio_id)}
        elif audio_url:
            body["record"] = {"audioUrl": audio_url}
        elif text:
            body["record"] = {"text": text}
        else:
            return ProviderResult(False, "нет ни записи, ни текста")
        return await _post(self.API_URL, body)


class P1Sms:
    """POST https://admin.p1sms.ru/apiSms/create (как P1Sms bot-engine)."""
    name = "p1sms"
    API_URL = "https://admin.p1sms.ru/apiSms/create"

    def __init__(self, api_key: str, sender: str = "P1SMS", channel: str = "char"):
        self.api_key, self.sender, self.channel = api_key, sender or "P1SMS", channel or "char"

    async def send(self, phone, text) -> ProviderResult:
        return await _post(self.API_URL, {"apiKey": self.api_key, "sms": [
            {"channel": self.channel, "sender": self.sender, "text": text, "phone": phone}]})


class StubTelephony:
    name = "stub"

    async def dial(self, phone, *, audio_url=None, audio_id=None, text=None) -> ProviderResult:
        log.info("ЗАГЛУШКА звонка %s: %s", mask_phone(phone), audio_url or audio_id or (text or "")[:80])
        return ProviderResult(True, "заглушка: звонок не совершался")


class StubSms:
    name = "stub"

    async def send(self, phone, text) -> ProviderResult:
        log.info("ЗАГЛУШКА SMS %s: %r", mask_phone(phone), text[:160])
        return ProviderResult(True, "заглушка: SMS не отправлялась")


def _key(cfg: ProviderSettings) -> str:
    ref = (cfg.api_key or "").strip()
    if not ref:
        raise SecretError("не задан api_key (нужна ссылка env:ИМЯ)")
    if not ref.startswith("env:"):
        raise SecretError("ключ провайдера записан открытым текстом – движок его не использует, "
                          "укажите ссылку env:ИМЯ")
    return resolve_secret(ref)


Factory = Callable[[ProviderSettings], object]

FACTORIES: dict[str, dict[str, Factory]] = {
    "call": {
        "zvonobot": lambda c: ZvonobotTelephony(_key(c), c.outgoing_phone_id),
        "stub": lambda c: StubTelephony(),
    },
    "sms": {
        "p1sms": lambda c: P1Sms(_key(c), c.sender, c.channel),
        "stub": lambda c: StubSms(),
    },
}


def register(channel: str, name: str, factory: Factory) -> None:
    """Свой провайдер (тесты: фальшивый; позже – другие сервисы)."""
    FACTORIES.setdefault(channel, {})[name] = factory


def build(channel: str, cfg: ProviderSettings) -> tuple[object | None, str]:
    """→ (провайдер, «») или (None, почему нет)."""
    name = (cfg.provider or "").strip().lower()
    if not name:
        return None, "провайдер не настроен"
    f = FACTORIES.get(channel, {}).get(name)
    if f is None:
        return None, f"неизвестный провайдер «{name}»"
    try:
        return f(cfg), ""
    except SecretError as e:
        return None, f"{name}: {e}"
