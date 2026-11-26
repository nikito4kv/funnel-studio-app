"""Разбор стикеров доски: «через 3 минуты если не перешёл в канал» → настройки.

Разборщик – интерфейс с двумя реализациями:
  * `RuleParser` (по умолчанию) – регулярки по фразам, которыми реально подписаны
    доски запусков (август–сентябрь 2026): «через N минут/часов/секунд», «сразу»,
    «если не перешёл в канал», «если НЕ нажал на кнопку випа», «если контакт ранее
    оставлял – пропускаем этот этап», «10 секунд если не хочет оставлять контакт»;
  * `ClaudeParser` – тот же ответ от Claude API (структурированный вывод с полем
    уверенности). Включается сам, если задан `ANTHROPIC_API_KEY`, и разбирает
    только то, в чём правила не уверены.

У каждого ответа есть `confidence` (0–1): меньше 1 – Студия подсвечивает шаг.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

log = logging.getLogger("studio.miro")

Event = Literal["contact", "no_contact", "email", "no_email", "clicked", "not_clicked", "stopped"]


@dataclass
class StickyMeaning:
    text: str
    delay_seconds: int | None = None          # None – время не названо
    event: Event | None = None                # к какому исходу стрелки относится
    conditions: dict[str, Any] = field(default_factory=dict)   # условия шага-цели (поля Conditions)
    skip_if_known: Literal["phone", "email"] | None = None     # «если ранее оставлял – пропускаем»
    unsupported: list[str] = field(default_factory=list)       # понято, но движок так не умеет
    leftover: str = ""                         # непонятый остаток
    confidence: float = 1.0
    source: str = "rules"

    @property
    def empty(self) -> bool:
        return (self.delay_seconds is None and not self.event and not self.conditions
                and not self.skip_if_known and not self.unsupported)


class StickyParser(Protocol):
    name: str

    def parse(self, text: str, context: str = "") -> StickyMeaning: ...


# ── правила ──────────────────────────────────────────────────────────────────

_UNITS = [
    (r"сек\w*|с(?=\b|\s|$)", 1),
    (r"мин\w*|м(?=\b|\s|$)", 60),
    (r"час\w*|ч(?=\b|\s|$)", 3600),
    (r"сут\w*|дн\w*|день|дня", 86400),
]
_NUM = r"(\d+(?:[.,]\d+)?)"
_WORD_NUM = {
    "одну": 1, "одна": 1, "один": 1, "две": 2, "два": 2, "пару": 2, "три": 3, "четыре": 4,
    "пять": 5, "десять": 10, "пятнадцать": 15, "двадцать": 20, "тридцать": 30, "сорок": 40,
}

_E = r"[её]"


def _rx(p: str) -> re.Pattern:
    return re.compile(p.replace("Ё", _E), re.I | re.S)


# (шаблон, что даёт) – порядок важен: сначала длинные фразы
_PHRASES: list[tuple[re.Pattern, dict]] = [
    # телефон / почта уже известны – этап пропускаем (условие шага)
    (_rx(r"(контакт|телефон|номер)\w*\s+(ранее|уже)\s+(оставлял|оставил|дал|есть|известен)\w*[^.]*?(пропуска\w*[^.]*?(этап|шаг)\w*)?"),
     {"skip_if_known": "phone"}),
    (_rx(r"(почт\w*|e-?mail|емейл|имейл)\s+(ранее|уже)\s+(оставлял|оставил|дал|есть|известн)\w*[^.]*?(пропуска\w*[^.]*?(этап|шаг)\w*)?"),
     {"skip_if_known": "email"}),
    # исходы запроса контакта
    (_rx(r"(если\s+)?не\s+(хочет\s+)?(оставля\w*|оставил\w*|дал\w*|дает|даёт|поделил\w*|отправил\w*)\s+(контакт|телефон|номер)\w*"),
     {"event": "no_contact"}),
    (_rx(r"(если\s+)?(оставля\w*|оставил\w*|дал\w*|поделил\w*|отправил\w*)\s+(контакт|телефон|номер)\w*"),
     {"event": "contact"}),
    (_rx(r"(если\s+)?не\s+(прислал\w*|ввел\w*|ввёл\w*|оставил\w*|отправил\w*)\s+(почт\w*|e-?mail|мейл\w*|мэйл\w*)"),
     {"event": "no_email"}),
    (_rx(r"(если\s+)?(прислал\w*|ввел\w*|ввёл\w*|оставил\w*|отправил\w*)\s+(почт\w*|e-?mail|мейл\w*|мэйл\w*)"),
     {"event": "email"}),
    # канал
    (_rx(r"(если\s+)?(ещ[её]\s+)?не\s+(перешел|перешёл|вступил|подписал\w*|зашел|зашёл|перешли|вступили)\w*(\s+в\s+(закрытый\s+)?канал\w*)?(\s+из\s+бота)?"),
     {"conditions": {"channel": "not_joined"}}),
    (_rx(r"(если\s+)?(перешел|перешёл|вступил|подписал\w*|зашел|зашёл)\w*\s+в\s+(закрытый\s+)?канал\w*"),
     {"conditions": {"channel": "joined"}}),
    # ВИП
    (_rx(r"(если\s+)?не\s+(нажал|купил|оплатил|забрал|взял)\w*(\s+на)?(\s+кнопку)?\s+(вип\w*|vip\w*)(\s+билет\w*)?"),
     {"conditions": {"not_paid": True}}),
    (_rx(r"(если\s+)?(купил|оплатил|забрал|взял)\w*\s+(вип\w*|vip\w*)(\s+билет\w*)?"),
     {"conditions": {"paid": True}}),
    # опрос
    (_rx(r"(если\s+)?не\s+прош[её]л\s+опрос\w*"), {"conditions": {"survey_done": True}}),
    (_rx(r"(если\s+)?нажал\w*[^.]{0,30}?\s+на\s+(\w+\s+)?вопрос\w*"),
     {"event": "clicked", "unsupported": "«вернуть на вопрос, где остановился» – это делает game-модуль"}),
    (_rx(r"(если\s+)?остановил\w*"), {"event": "stopped", "unsupported": "«если остановился» – движок внутри опроса таймеров не держит"}),
    (_rx(r"(если\s+)?не\s+нажал\w*(\s+на)?(\s+кнопку)?"), {"event": "not_clicked"}),
    (_rx(r"(если\s+)?нажал\w*(\s+на)?(\s+кнопку)?"), {"event": "clicked"}),
    # время «сразу»
    (_rx(r"сразу(\s+после\s+(последнего|предыдущего)\s+(вопроса|сообщения|шага))?"), {"delay": 0}),
    (_rx(r"через\s+(пол\s*часа|полчаса)"), {"delay": 1800}),
    (_rx(r"через\s+час"), {"delay": 3600}),
    (_rx(r"через\s+минуту"), {"delay": 60}),
    (_rx(r"через\s+сутки"), {"delay": 86400}),
]

_STOP = {"если", "через", "и", "на", "в", "кнопку", "после", "этот", "этап", "а", "то", "уже",
         "из", "бота", "бот", "его", "он", "она", "по", "не", "с", "к", "до", "—", "–", "-",
         "пропускаем", "человек", "юзер", "пользователь", "ранее", "отправлять", "отправить",
         "отправляем", "присылаем", "шлем", "шлём"}


def _durations(s: str) -> tuple[int | None, str]:
    """Все «N единиц» в строке → сумма секунд; возвращает и строку без них."""
    total = 0
    found = False
    rest = s
    for unit_rx, mul in _UNITS:
        rx = re.compile(rf"{_NUM}\s*({unit_rx})", re.I)
        for m in list(rx.finditer(rest)):
            total += round(float(m.group(1).replace(",", ".")) * mul)
            found = True
        rest = rx.sub(" ", rest)
        words = "|".join(_WORD_NUM)
        rxw = re.compile(rf"\b({words})\s+({unit_rx})", re.I)
        for m in list(rxw.finditer(rest)):
            total += _WORD_NUM[m.group(1).lower()] * mul
            found = True
        rest = rxw.sub(" ", rest)
    return (total if found else None), rest


class RuleParser:
    name = "rules"

    def parse(self, text: str, context: str = "") -> StickyMeaning:
        raw = " ".join((text or "").split())
        m = StickyMeaning(text=raw)
        s = raw.replace("--", " ").replace("—", " ").replace("–", " ")
        for rx, what in _PHRASES:
            hit = rx.search(s)
            if not hit:
                continue
            if "delay" in what and m.delay_seconds is None:
                m.delay_seconds = what["delay"]
            if "event" in what and not m.event:
                m.event = what["event"]
            if "conditions" in what:
                m.conditions.update(what["conditions"])
            if "skip_if_known" in what:
                m.skip_if_known = what["skip_if_known"]
            if "unsupported" in what:
                m.unsupported.append(what["unsupported"])
            s = s[:hit.start()] + " " + s[hit.end():]
        d, s = _durations(s)
        if d is not None:
            m.delay_seconds = (m.delay_seconds or 0) + d
        s = re.sub(r"через", " ", s, flags=re.I)
        left = [w for w in re.findall(r"[\wёЁ]+", s) if w.lower() not in _STOP and not w.isdigit()]
        m.leftover = " ".join(left)
        if m.empty:
            m.confidence = 0.0
        elif m.leftover:
            m.confidence = 0.5
        return m


# ── Claude API ───────────────────────────────────────────────────────────────

class ClaudeParser:
    """Разбор стикера через Claude API. Правила идут первыми: к модели уходит только то,
    в чём они не уверены (confidence < 1), – дёшево и предсказуемо."""
    name = "claude"

    def __init__(self, client: Any = None, model: str | None = None,
                 fallback: StickyParser | None = None):
        self.fallback = fallback or RuleParser()
        self.model = model or os.environ.get("STUDIO_CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL
        if client is None:
            import anthropic  # noqa: PLC0415 – пакет нужен только с ключом
            client = anthropic.Anthropic()
        self.client = client
        self.calls = 0

    def parse(self, text: str, context: str = "") -> StickyMeaning:
        base = self.fallback.parse(text, context)
        if base.confidence >= 1.0:
            return base
        try:
            got = self._ask(text, context)
        except Exception as e:  # noqa: BLE001 – без модели импорт всё равно работает
            log.warning("Claude не разобрал стикер «%s»: %s", text[:60], type(e).__name__)
            return base
        got.text = base.text
        return got

    def _ask(self, text: str, context: str) -> StickyMeaning:
        self.calls += 1
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=CLAUDE_SYSTEM,
            messages=[{"role": "user", "content":
                       f"Стикер: «{text}»\nГде он на доске: {context or 'рядом со стрелкой между сообщениями'}"}],
            # разбор короткой фразы – низкое усилие хватает; формат – строго по схеме
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": CLAUDE_SCHEMA}},
        )
        import json  # noqa: PLC0415
        if getattr(resp, "stop_reason", None) == "refusal":
            raise RuntimeError("модель отказалась разбирать стикер")
        body = next(b.text for b in resp.content if getattr(b, "type", "") == "text")
        d = json.loads(body)
        conds: dict[str, Any] = {}
        c = d.get("condition") or "none"
        if c == "not_in_channel":
            conds["channel"] = "not_joined"
        elif c == "in_channel":
            conds["channel"] = "joined"
        elif c == "not_paid_vip":
            conds["not_paid"] = True
        elif c == "paid_vip":
            conds["paid"] = True
        elif c == "survey_not_done":
            conds["survey_done"] = True
        ev = d.get("event") or "none"
        skip = d.get("skip_if_known") or "none"
        return StickyMeaning(
            text=text,
            delay_seconds=d.get("delay_seconds"),
            event=None if ev == "none" else ev,
            conditions=conds,
            skip_if_known=None if skip == "none" else skip,
            unsupported=[d["unsupported"]] if d.get("unsupported") else [],
            leftover="" if d.get("confidence", 0) >= 0.8 else (d.get("unsupported") or ""),
            confidence=float(d.get("confidence", 0)),
            source="claude",
        )


DEFAULT_CLAUDE_MODEL = "claude-opus-5"

CLAUDE_SYSTEM = """\
Ты разбираешь стикеры с доски Miro, на которой постановщик нарисовал воронку чат-бота \
(Telegram/MAX). Стикер стоит у стрелки между двумя сообщениями и говорит, КОГДА и КОМУ \
уходит следующее сообщение. Время отсчитывается от предыдущего сообщения.

Верни:
- delay_seconds – задержка в секундах («через 3 минуты» = 180, «сразу» = 0), null если время не названо;
- event – к какому исходу относится стрелка: contact (дал телефон), no_contact (не дал телефон за время), \
email / no_email (прислал / не прислал почту), clicked / not_clicked (нажал / не нажал кнопку), \
stopped (остановился посреди опроса), none;
- condition – условие для следующего сообщения: not_in_channel (не вступил в закрытый канал), \
in_channel, not_paid_vip (не купил ВИП-билет), paid_vip, survey_not_done (не прошёл опрос), none;
- skip_if_known – «если телефон/почту ранее оставлял – этап пропускаем»: phone, email, none;
- unsupported – то, что понятно, но в эти поля не ложится (коротко, по-русски), или пустая строка;
- confidence – насколько ты уверен в разборе, от 0 до 1.
"""

CLAUDE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "delay_seconds": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "event": {"type": "string", "enum": ["contact", "no_contact", "email", "no_email", "clicked",
                                             "not_clicked", "stopped", "none"]},
        "condition": {"type": "string", "enum": ["not_in_channel", "in_channel", "not_paid_vip",
                                                 "paid_vip", "survey_not_done", "none"]},
        "skip_if_known": {"type": "string", "enum": ["phone", "email", "none"]},
        "unsupported": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["delay_seconds", "event", "condition", "skip_if_known", "unsupported", "confidence"],
    "additionalProperties": False,
}


def default_parser() -> StickyParser:
    """Claude, если задан ANTHROPIC_API_KEY и стоит пакет anthropic, иначе правила."""
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        try:
            return ClaudeParser()
        except Exception as e:  # noqa: BLE001
            log.warning("Claude API недоступен (%s) – стикеры разбираются правилами", type(e).__name__)
    return RuleParser()
