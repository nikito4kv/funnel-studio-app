"""Пустые шаги: бот «выполнит» шаг, а человеку не уйдёт ничего.

Живой тест 24.09.2026: у двух шагов опубликованной воронки не было текста – движок
молча прошёл их, в Telegram не ушло ничего, ошибки нигде не было. Теперь:

  * проверка публикации Студии (`studio/versions/publish.py`) – пустой шаг блокирует
    «Запустить в боте» («Шаг «Текст» пустой – бот ничего не отправит»), сообщение с
    картинкой без картинки – предупреждение;
  * публикация движка (`runtime.publish`) – предупреждения в отчёте;
  * исполнитель движка – запись `empty_step` в `actions` и WARNING в логе.

Та же проверка в браузере – правило 84 панели «Проверка» (`web/src/model/validate.ts`).

Что считается содержимым: видимый текст (после снятия HTML-тегов), картинка
(или альбом), ролик, кружок (или запасное видео), статья. Кнопки без текста
Telegram не отправит, поэтому содержимым не считаются. Шаги без сообщения
(переход, звонок, SMS, закреп эфира) не проверяются.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .funnel import Funnel, Step, StepType

_TAG = re.compile(r"<[^<>]+>")
_INVISIBLE = re.compile(r"[\s​‌‍⁠﻿]+")

# шаги, которые шлют сообщение
MESSAGE_TYPES = {StepType.text, StepType.photo, StepType.video, StepType.animation,
                 StepType.video_note, StepType.rich, StepType.phone_request, StepType.email_request}


def visible(html: str | None) -> str:
    """Что увидит человек: текст без тегов и невидимых символов."""
    if not html:
        return ""
    txt = _TAG.sub("", html).replace("&nbsp;", " ")
    return _INVISIBLE.sub(" ", txt).strip()


def step_name(s: Step) -> str:
    return s.title.strip() or s.key


@dataclass
class EmptyIssue:
    step_id: str
    key: str
    level: str          # error – бот ничего не отправит; warn – уйдёт не то, что задумано
    text: str

    def as_dict(self) -> dict:
        return {"step_id": self.step_id, "step_key": self.key, "level": self.level, "text": self.text}


def _media(s: Step) -> dict[str, bool]:
    m = s.media
    return {
        "image": bool(m.image) or any(bool(i) for i in (m.images or [])),
        "video": bool(m.video),
        "animation": bool(m.animation),
        "video_note": bool(m.video_note) or bool(m.video_fallback),
        "rich": bool(m.rich),
    }


def step_issue(s: Step) -> EmptyIssue | None:
    if s.type not in MESSAGE_TYPES:
        return None
    name = step_name(s)
    tg_text = visible(s.content.tg.text)
    max_text = tg_text if s.content.max.text is None else visible(s.content.max.text)
    variants_text = any(visible(v.text) for v in s.content.variants)
    has = _media(s)
    media_any = any(has.values())

    def issue(level: str, text: str) -> EmptyIssue:
        return EmptyIssue(str(s.id), s.key, level, text)

    if not tg_text and not media_any:
        if max_text or variants_text:
            where = "в Telegram" if max_text else "для людей вне вариантов формулировок"
            return issue("warn", f"Шаг «{name}» {where} пустой – там бот ничего не отправит")
        return issue("error", f"Шаг «{name}» пустой – бот ничего не отправит")
    if s.type == StepType.photo and not has["image"]:
        return issue("warn", f"Шаг «{name}» – сообщение с картинкой, но картинки нет: уйдёт только текст")
    if s.type in (StepType.video, StepType.animation) and not (has["video"] or has["animation"]):
        return issue("warn", f"Шаг «{name}» – сообщение с видео, но ролика нет: уйдёт только текст")
    if s.type == StepType.video_note and not has["video_note"]:
        return issue("warn", f"Шаг «{name}» – кружок, но ролика нет: уйдёт только текст")
    return None


def empty_issues(f: Funnel) -> list[EmptyIssue]:
    return [i for i in (step_issue(s) for s in f.steps) if i is not None]
