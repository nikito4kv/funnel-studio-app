"""Настройки запуска (tenants.settings, JSONB).

Одно место вместо launch.yaml + launch_settings + .env + LAUNCH_META
(исследование, §3). Здесь только то, что нужно фундаменту движка; прочее
(Sheets, amo, vip_sync, залы Bizon по дням) добавится вместе с источниками
данных. Незнакомые ключи не теряются (extra="allow") – их положит пульт.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .gates.config import BizonSettings, LinksSettings, PaymentsSettings


class _S(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ChannelSettings(_S):
    link: str = ""
    title: str = ""
    # id канала для гейта «вступил / не вступил» (getChatMember). 0 – не проверяем.
    chat_id: int = 0
    max_link: str = ""
    vk_link: str = ""
    # id канала в MAX для гейта «вступил / не вступил» (бот – админ канала). 0 – не проверяем.
    max_chat_id: int = 0


class WebinarSettings(_S):
    # эфир по умолчанию (когда в воронке нет branch_router): "YYYY-MM-DDTHH:MM" МСК
    datetime_msk: str = ""
    stream_url: str = ""
    stream_url_max: str = ""
    gift_url: str = ""
    vip_url: str = ""

    @property
    def unix(self) -> int:
        if not self.datetime_msk:
            return 0
        try:
            dt = datetime.fromisoformat(self.datetime_msk)
            return int(dt.timestamp() - 3 * 3600) if dt.tzinfo is None else int(dt.timestamp())
        except Exception:
            return 0

    @property
    def vip_target(self) -> str:
        return self.vip_url or self.gift_url

    def stream_url_for(self, platform: str = "tg") -> str:
        if platform == "max" and self.stream_url_max:
            return self.stream_url_max
        return self.stream_url


class DndSettings(_S):
    """Ночной режим: шаг, попавший в окно [from..to] МСК, переносится на reschedule_to."""
    enabled: bool = False
    from_: str = Field(default="21:00", alias="from")
    to: str = "09:00"
    reschedule_to: str = "09:01"


class InfraSettings(_S):
    # base_url + go_prefix – прокси-ссылки bot-engine (/govip). Если у запуска
    # включены свои ссылки движка (links), RenderCfg подменяет их на свои.
    base_url: str = ""
    go_prefix: str = ""


class TenantSettings(_S):
    display_name: str = ""
    channel: ChannelSettings = ChannelSettings()
    webinar: WebinarSettings = WebinarSettings()
    dnd: DndSettings = DndSettings()
    infra: InfraSettings = InfraSettings()
    # воронка, в которую ведёт /start (id воронки из графа). Пусто – первая опубликованная.
    entry_funnel: str = ""
    # запуск bot-engine, из static которого брать «./static/…» (если файла нет в data/media Студии)
    static_launch: str = ""
    # повторный /start: engine – как движок bot-engine (CLAUDE.md §23: недавний вход и живые
    # таймеры → ничего не трогаем, иначе перезапуск с первого шага);
    # keep – пока у человека есть живые таймеры, /start ничего не ломает;
    # restart – всегда с начала
    restart_policy: Literal["engine", "keep", "restart"] = "engine"
    # сколько шагов запуска выполняется одновременно (авария 22.09: пачка таймеров
    # выбирала весь пул соединений)
    max_parallel_fires: int = 8
    # источники данных для гейтов (runtime/gates): касса, Bizon, прокси-ссылки
    payments: PaymentsSettings = PaymentsSettings()
    bizon: BizonSettings = BizonSettings()
    links: LinksSettings = LinksSettings()


class RenderCfg:
    """Утиный LaunchConfig для engine_lib/render.py."""

    def __init__(self, slug: str, s: TenantSettings, *, stream_url_override: str = ""):
        self.slug = slug
        self.display_name = s.display_name or slug
        self.channel = s.channel
        self.infra = s.infra
        if s.links.configured():
            # {vip_link} → своя прокси движка /t/<запуск>/govip (runtime/gates/links.py)
            self.infra = InfraSettings(base_url=s.links.base, go_prefix=f"/t/{slug}")
        if stream_url_override:
            self.webinar = s.webinar.model_copy(update={"stream_url": stream_url_override})
        else:
            self.webinar = s.webinar
