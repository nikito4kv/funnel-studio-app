"""Фразы времени и условий – порт web/src/phrases.ts один в один.

Дифф версий пишет изменения теми же словами, что стоят на карточках холста
(«через 3 мин», «в 11:00, день 2», «💳 не оплатил ВИП»). Чтобы Python и
TypeScript не разошлись, обе стороны сверяются с одним эталоном:
`web/src/versions/__fixtures__/phrases_golden.json` (пересобрать –
`uv run python -m studio.versions.golden`; тест vitest сверяет TS с ним,
pytest – Python с ним).

Работает на «сырых» словарях графа (JSON), а не на pydantic-модели: дифф
считается и для черновика, который ещё не прошёл проверку.
"""
from __future__ import annotations

from typing import Any

STEP_TYPE_LABEL = {
    "text": "💬 Текст",
    "photo": "🖼 Сообщение с картинкой",
    "video_note": "◉ Кружок",
    "video": "🎬 Видео",
    "animation": "🎞 GIF",
    "rich": "📰 Статья",
    "phone_request": "📞 Запрос телефона",
    "email_request": "✉ Запрос почты",
    "jump": "⤳ Переход (удалённый шаг)",
    "call": "☎ Автозвонок",
    "sms": "📱 SMS",
    "air_pin": "📌 Закреп эфира",
}

EDGE_KIND_LABEL = {
    "done": "по времени",
    "contact": "дал телефон",
    "skip": "не дал (таймаут)",
    "skip_if_phone_known": "телефон уже известен",
    "email": "прислал почту",
    "skip_if_email_known": "почта уже известна",
    "callback": "нажал кнопку-действие",
    "survey_next": "ответил – следующий вопрос",
    "jump": "переход",
    "also_fire": "параллельно",
    "chain_to_funnel": "в другую воронку",
}


def duration(sec: int) -> str:
    if sec == 0:
        return "сразу"
    units = [(86400, "дн"), (3600, "ч"), (60, "мин"), (1, "сек")]
    parts: list[str] = []
    rest = sec
    for n, name in units:
        if rest >= n:
            parts.append(f"{rest // n} {name}")
            rest %= n
        if len(parts) == 2:
            break
    return " ".join(parts)


def minutes(m: int) -> str:
    a = abs(m)
    if a == 0:
        return "0 мин"
    if a % 60 == 0:
        return f"{a // 60} ч"
    if a > 60:
        return f"{a // 60} ч {a % 60} мин"
    return f"{a} мин"


def day_word(d: int) -> str:
    if d == 0:
        return "день эфира"
    if d < 0:
        return f"за {-d} дн. до эфира"
    return f"день {d + 1}"


def _hm(total: int) -> tuple[int, str]:
    day = total // 1440          # как Math.floor
    m = ((total % 1440) + 1440) % 1440
    return day, f"{m // 60:02d}:{m % 60:02d}"


def _to_min(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def schedule_phrase(s: dict, branch: dict | None = None) -> str | None:
    """Фраза «когда отправить»; None – шаг идёт по входящей стрелке."""
    wom = s.get("webinar_offset_minutes")
    wob = s.get("webinar_offset_by_branch")
    day_offset = s.get("day_offset") or 0
    if wom is not None:
        repeat = s.get("anchor") == "repeat"
        what = "повтора" if repeat else "эфира"
        off = wob[branch["id"]] if (branch and wob and branch["id"] in wob) else wom
        if off < 0:
            rel = f"за {minutes(off)} до {what}"
        elif off == 0:
            rel = f"в начале {what}"
        else:
            rel = f"через {minutes(off)} после начала {what}"
        day = f", {day_word(day_offset)}" if day_offset else ""
        if branch:
            base = _to_min(branch["webinar_time"]) + (branch.get("repeat_offset_minutes", 0) if repeat else 0)
            d, text = _hm(base + off)
            shift = f" (+{d} дн)" if d else ""
            return f"в {text}{shift} · {rel}{day}"
        per = (" (" + " / ".join(f"{b}: {minutes(v)}" for b, v in wob.items()) + ")") if wob else ""
        return rel + per + day
    at = s.get("absolute_time_msk")
    dob = s.get("day_offset_by_branch")
    if at:
        if branch or not dob:
            d = dob[branch["id"]] if (branch and dob and branch["id"] in dob) else day_offset
            return f"в {at}, {day_word(d)}"
        return f"в {at} (" + " / ".join(f"{b}: {day_word(d)}" for b, d in dob.items()) + ")"
    if s.get("after_start_seconds") is not None:
        return f"через {duration(s['after_start_seconds'])} после входа"
    return None


def edge_phrase(e: dict, from_step: dict | None = None) -> str:
    d = e.get("delay_seconds")
    after = f" · через {duration(d)}" if d else ""
    k = e.get("kind")
    if k == "done":
        return f"через {duration(d)}" if d else "сразу"
    if k == "contact":
        return "дал телефон" + after
    if k == "skip":
        return f"не дал за {duration(d)}" if d is not None else "не дал"
    if k == "skip_if_phone_known":
        return "телефон известен" + after
    if k == "email":
        return "прислал почту" + after
    if k == "skip_if_email_known":
        return "почта известна" + after
    if k == "callback":
        claim = (((from_step or {}).get("content") or {}).get("tg") or {}).get("claim_button_text")
        return f"нажал «{claim}»" if claim else "нажал кнопку"
    if k == "survey_next":
        return "ответил"
    if k == "jump":
        return "переход"
    if k == "also_fire":
        return f"параллельно · через {duration(d or 0)}"
    if k == "chain_to_funnel":
        return f"в воронку «{e.get('to_funnel')}»" + after
    return str(k)


def condition_badges(c: dict) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    room = f" · {c['bizon_room']}" if c.get("bizon_room") else ""
    if c.get("attendance") == "was":
        out.append({"icon": "📺", "text": f"был на эфире{room}"})
    if c.get("attendance") == "was_not":
        out.append({"icon": "🙈", "text": f"не был на эфире{room}"})
    if c.get("not_paid"):
        out.append({"icon": "💳", "text": "не оплатил ВИП"})
    if c.get("paid"):
        out.append({"icon": "💎", "text": "оплатил ВИП"})
    if c.get("not_course_paid"):
        out.append({"icon": "🎓", "text": "не купил курс"})
    if c.get("course_paid"):
        out.append({"icon": "🎓", "text": "купил курс"})
    if c.get("channel") == "not_joined":
        out.append({"icon": "🚪", "text": "не в канале"})
    if c.get("channel") == "joined":
        out.append({"icon": "🚪", "text": "в канале"})
    if c.get("survey_done"):
        out.append({"icon": "📋", "text": "не прошёл опрос"})
    if c.get("has_lm"):
        out.append({"icon": "🎁", "text": "есть лидмагнит"})
    if c.get("no_link_click"):
        out.append({"icon": "🔗", "text": f"не кликнул {c['no_link_click']}*"})
    if c.get("link_click"):
        out.append({"icon": "👆", "text": f"кликнул {c['link_click']}*"})
    if c.get("no_manager_dm"):
        out.append({"icon": "💬", "text": "не написал менеджеру"})
    if c.get("max_subscription_gate"):
        out.append({"icon": "Ⓜ", "text": "подписан в MAX"})
    if c.get("registered_windows"):
        out.append({"icon": "🕘", "text": "пришёл не в " + ", ".join(
            w.replace("-", "–") for w in c["registered_windows"])})
    return out


def golden_for(graph: dict[str, Any]) -> dict[str, Any]:
    """Фразы всех шагов и стрелок графа – для эталона, общего с TS."""
    by_id = {s["id"]: s for s in graph.get("steps", [])}
    branches = (graph.get("branch_router") or {}).get("branches") or []
    steps = {}
    for s in graph.get("steps", []):
        steps[s["id"]] = {
            "key": s["key"],
            "schedule": schedule_phrase(s["schedule"]),
            "by_branch": {b["id"]: schedule_phrase(s["schedule"], b) for b in branches},
            "badges": [f"{b['icon']} {b['text']}" for b in condition_badges(s["conditions"])],
        }
    edges = {e["id"]: edge_phrase(e, by_id.get(e["from_step"])) for e in graph.get("edges", [])}
    return {"steps": steps, "edges": edges}
