"""Настройки запуска для видов шагов из runtime/steps.

Живут в том же `tenants.settings` (JSONB), что и остальное, – отдельными
ключами. `TenantSettings` принимает незнакомые ключи (extra="allow"), поэтому
общий settings.py для этого не менялся:

  telephony:  {"provider": "zvonobot", "api_key": "env:ZVONOBOT_KEY",
               "outgoing_phone_id": 123}
  sms:        {"provider": "p1sms", "api_key": "env:P1SMS_KEY",
               "sender": "DEMO", "channel": "char"}
  air_pin:    {"days": [{"when": "2026-09-24T11:00", "title": "Первый эфир",
                         "url": "https://…"}], "soon_minutes": 30, …}
  funnel_aliases: {"alt": "funnel_alt"}   # chain_to_funnel: имя → id воронки
  service_chat_id: -100…                  # rich: куда заливать картинки статьи

⚠️ Ключи провайдеров в базу НЕ пишутся – только ссылка `env:ИМЯ`, как у токенов
ботов. Ключ открытым текстом движок не использует (шаг уходит с пометкой).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _S(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ProviderSettings(_S):
    # zvonobot / p1sms / stub (только лог, никуда не звоним); пусто – провайдера нет
    provider: str = ""
    api_key: str = ""                  # ТОЛЬКО env:ИМЯ
    outgoing_phone_id: int | None = None   # zvonobot: с какого номера звонить
    sender: str = "P1SMS"              # p1sms: имя отправителя
    channel: str = "char"              # p1sms: char / vk


class AirPinDay(_S):
    when: str = ""                     # ISO в МСК: «2026-09-24T11:00»
    title: str = ""                    # «Первый эфир»; пусто – «Эфир N»
    url: str = ""                      # зал этого эфира (кнопка «Подключиться»)
    subtitle: str = ""
    desc: str = ""
    live_minutes: int | None = None


class AirPinSettings(_S):
    """Как AirPinConfig bot-engine. Пустые days – эфир человека из его позиции
    (branch_router) или webinar.datetime_msk запуска: так закреп работает и
    у вечнозелёного автовебинара, где у каждого своя дата."""
    days: list[AirPinDay] = []
    live_minutes: int = 240
    title: str = "Прямой эфир"
    soon_minutes: int = 30
    button_text: str = "Подключиться"
    button_wait_text: str = "В КАНАЛ"
    button_wait_url: str = ""          # пусто – канал запуска
    quote: bool = False
    pin_delay_sec: int = 0
    after_text: str = ""               # все эфиры прошли; пусто – закреп снимается
    emoji_wait: str = "⏳"
    emoji_soon: str = "⏰"
    emoji_live: str = "🔴"
    text_wait: str = ""
    text_soon: str = ""
    text_live: str = ""


class StepsSettings(_S):
    telephony: ProviderSettings = ProviderSettings()
    sms: ProviderSettings = ProviderSettings()
    air_pin: AirPinSettings = AirPinSettings()
    funnel_aliases: dict[str, str] = {}
    service_chat_id: int = 0


_KEYS = ("telephony", "sms", "air_pin", "funnel_aliases", "service_chat_id")


def steps_settings(tenant_settings) -> StepsSettings:
    """Свои ключи из TenantSettings (лишние ключи там – model_extra)."""
    extra = dict(getattr(tenant_settings, "model_extra", None) or {})
    raw = {k: extra[k] for k in _KEYS if k in extra and extra[k] is not None}
    return StepsSettings.model_validate(raw)
