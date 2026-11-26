"""Вход симулятора: момент регистрации, платформа и «персона» – кто этот человек и что он делает."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Persona(_M):
    """Ответы на условия и действия человека. По умолчанию – «типовой человек»:
    контакт дал сразу, в канал не вступил, ВИП не оплатил, на эфирах не был,
    на кнопки и вопросы отвечает, почту присылает.

    `*_after_min` – через сколько минут после регистрации это случилось
    (0 – было уже при входе). Условие проверяется в момент отправки шага.
    """
    # ── условия ──
    attended_days: list[int] = []          # «был на эфире дня N» (N с 1)
    vip_paid: bool = False
    vip_paid_after_min: int = 0
    course_paid: bool = False
    course_paid_after_min: int = 0
    channel_joined: bool = False
    channel_joined_after_min: int = 0
    link_clicked: bool = False             # кликнул ссылку (любую отслеживаемую)
    link_clicked_after_min: int = 0
    manager_dm: bool = False               # написал менеджеру / оставил заявку
    manager_dm_after_min: int = 0
    survey_done: bool = False              # прошёл опрос (внешний) ещё до входа
    phone_known: bool = False              # телефон уже известен боту
    email_known: bool = False              # почта уже известна
    has_lm: bool = True                    # пришёл с лидмагнитом
    # ── действия ──
    contact: Literal["given", "no"] = "given"
    contact_after_s: int = Field(15, ge=0)
    claim: bool = True                     # нажимает кнопки-действия («Забрать», «Активировать»)
    claim_after_s: int = Field(20, ge=0)
    survey_answer: bool = True             # отвечает на вопросы опроса в боте
    survey_after_s: int = Field(20, ge=0)
    email: bool = True                     # присылает почту, когда просят
    email_after_s: int = Field(40, ge=0)


class SimOptions(_M):
    registered_at: str                     # "YYYY-MM-DDTHH:MM" – МСК
    platform: Literal["tg", "max"] = "tg"
    persona: Persona = Persona()
    dnd: bool | None = None                # None – как в launch.yaml запуска
    horizon_days: int = Field(14, ge=1, le=14)
    max_events: int = Field(2000, ge=10, le=2000)
