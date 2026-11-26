"""Ошибки проверки графа – понятным русским текстом, с id и key шага."""
from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

GROUPS = {
    "content": "текст и кнопки", "media": "медиа", "schedule": "время отправки",
    "conditions": "условия", "contact": "запрос телефона", "email": "запрос почты",
    "survey": "опрос", "call": "звонок", "sms": "SMS", "claim": "кнопка-действие",
    "pin": "закреп", "key": "техническое имя", "type": "тип", "id": "id",
}

TYPE_MSG = {
    "missing": "не заполнено",
    "int_parsing": "нужно целое число",
    "int_type": "нужно целое число",
    "float_parsing": "нужно число",
    "bool_parsing": "нужно да/нет",
    "bool_type": "нужно да/нет",
    "string_type": "нужна строка",
    "list_type": "нужен список",
    "dict_type": "нужен словарь",
    "model_type": "неверная структура",
    "literal_error": "недопустимое значение",
    "enum": "недопустимое значение",
    "extra_forbidden": "лишнее поле – в модели его нет",
    "uuid_parsing": "неверный id",
    "uuid_type": "неверный id",
}

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _step_ref(raw: dict, idx: int) -> tuple[str | None, str | None]:
    try:
        s = raw["steps"][idx]
        return s.get("id"), s.get("key")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None, None


def _key_of_id(raw: dict, sid: str) -> str | None:
    for s in raw.get("steps") or []:
        if isinstance(s, dict) and s.get("id") == sid:
            return s.get("key")
    return None


def _msg(err: dict) -> str:
    t = err.get("type", "")
    m = str(err.get("msg", ""))
    if t == "value_error":
        return m.removeprefix("Value error, ")
    if t in TYPE_MSG:
        return TYPE_MSG[t]
    return m


def translate(exc: ValidationError, raw: Any) -> list[dict]:
    """ValidationError pydantic → список {message, step_id, step_key, path}."""
    raw = raw if isinstance(raw, dict) else {}
    out = []
    for err in exc.errors():
        loc = list(err.get("loc", ()))
        step_id = step_key = None
        where = ""
        text = _msg(err)
        if len(loc) >= 2 and loc[0] == "steps" and isinstance(loc[1], int):
            step_id, step_key = _step_ref(raw, loc[1])
            rest = [str(x) for x in loc[2:]]
            group = GROUPS.get(rest[0], rest[0]) if rest else ""
            field = ".".join(rest[1:]) if len(rest) > 1 else ""
            where = f"Шаг «{step_key or '?'}»" + (f" (id {step_id})" if step_id else "")
            if group:
                where += f", {group}" + (f" → {field}" if field else "")
        elif len(loc) >= 2 and loc[0] == "edges" and isinstance(loc[1], int):
            e = (raw.get("edges") or [{}])[loc[1]] if loc[1] < len(raw.get("edges") or []) else {}
            fk = _key_of_id(raw, e.get("from_step", "")) if isinstance(e, dict) else None
            where = f"Стрелка из шага «{fk or '?'}»" + (f" ({e.get('kind')})" if isinstance(e, dict) and e.get("kind") else "")
            step_id = e.get("from_step") if isinstance(e, dict) else None
            step_key = fk
            rest = [str(x) for x in loc[2:]]
            if rest:
                where += f", поле {'.'.join(rest)}"
        elif loc:
            where = "Воронка, поле " + ".".join(str(x) for x in loc)
        else:
            where = "Воронка"
            # ошибки ссылок уровня воронки упоминают uuid – подставим key
            for u in _UUID.findall(text):
                for e in raw.get("edges") or []:
                    if isinstance(e, dict) and e.get("id") == u:
                        fk = _key_of_id(raw, e.get("from_step", ""))
                        text = text.replace(u, f"{u} (из шага «{fk}», {e.get('kind')})")
                        step_id, step_key = e.get("from_step"), fk
                k = _key_of_id(raw, u)
                if k:
                    text = text.replace(u, f"«{k}» ({u})")
        out.append({
            "message": f"{where}: {text}",
            "step_id": step_id,
            "step_key": step_key,
            "path": ".".join(str(x) for x in loc),
        })
    return out
