"""Настройки источников данных для гейтов (часть tenants.settings).

Секреты в базе не хранятся – только ссылки `env:ИМЯ` (как токены ботов).
Незнакомые ключи не теряются (extra="allow").

Пример (python -m runtime tenant set <slug> …):

    payments.enabled=true payments.secret=env:PAYMENTS_WEBHOOK_SECRET
    payments.vip_tariffs=["01a0612a-…"] payments.paid_from=2026-09-01
    bizon.enabled=true bizon.token=env:BIZON_API_TOKEN bizon.room=demo_room_day1
    links.enabled=true links.base_url=https://engine.example.ru
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _S(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class PaymentsSettings(_S):
    """Вебхук кассы (pay.example.com): HMAC-SHA256 от тела, заголовок X-Payment-Signature."""
    enabled: bool = False
    secret: str = ""                    # env:ИМЯ – общий с кассой секрет подписи
    vip_tariffs: list[str] = []         # тарифы, оплата которых = «оплатил ВИП» (paid / not_paid)
    course_tariffs: list[str] = []      # = «купил курс» (course_paid / not_course_paid)
    # Матч по телефону идёт по всем оплатам тарифа; если тот же тариф продаёт
    # другой бот, он ловит чужих покупателей (§34). Поэтому по умолчанию – нет:
    # только по id человека, который прокси /govip кладёт в ссылку на оплату.
    match_by_phone: bool = False
    # Не учитывать оплаты раньше этого момента, МСК: "YYYY-MM-DD" или "YYYY-MM-DDTHH:MM".
    # Нужен, когда источник включают на уже идущем тарифе.
    paid_from: str = ""
    # Шаг, который запустить человеку в момент привязки оплаты: {"vip": key, "course": key}.
    # Ищется по ключу в воронке, где у человека есть позиция.
    on_paid: dict[str, str] = {}

    def configured(self) -> bool:
        return bool(self.enabled and self.secret and (self.vip_tariffs or self.course_tariffs))

    def tariffs(self, product: str) -> list[str]:
        return list(self.vip_tariffs if product == "vip" else self.course_tariffs)


class BizonSettings(_S):
    """Отчёты зрителей Bizon365 (webinars/reports/getlist + getviewers, заголовок X-Token)."""
    enabled: bool = False
    token: str = ""                     # env:ИМЯ
    api_base: str = "https://online.bizon365.ru/api/v1"
    # зал по умолчанию, если у шага и у воронки его нет; *_max – для MAX
    room: str = ""
    room_max: str = ""
    # как часто перечитывать список отчётов (секунды)
    list_ttl_seconds: int = 300
    # сколько последних отчётов просматривать (getlist отдаёт свежие вперёд, по 100)
    list_limit: int = 300
    # ранний запас окна: комнату открывают раньше эфира, отчёт «создан» до его часа
    window_lead_minutes: int = 15
    # переход по ссылке эфира (своя прокси /go на адрес с именем зала) = «был»
    count_link_clicks: bool = True
    # в ссылку на Bizon при клике дописать id/имя/телефон/UTM – тогда Bizon
    # запишет id в отчёт, и зритель найдётся без телефона
    autologin: bool = True

    def configured(self) -> bool:
        return bool(self.enabled and self.token)


DEFAULT_SKIP_HOSTS = ["t.me", "telegram.me", "telegram.org", "max.ru", "vk.com", "vk.ru"]


class LinksSettings(_S):
    """Своя прокси-ссылка движка: {base_url}/go/<code>?u=<id> и {base_url}/t/<запуск>/govip."""
    enabled: bool = False
    base_url: str = ""                  # публичный адрес HTTP-сервиса движка, без / в конце
    wrap_buttons: bool = True
    wrap_text: bool = True
    skip_hosts: list[str] = DEFAULT_SKIP_HOSTS

    def configured(self) -> bool:
        return bool(self.enabled and self.base_url.strip())

    @property
    def base(self) -> str:
        return self.base_url.strip().rstrip("/")
