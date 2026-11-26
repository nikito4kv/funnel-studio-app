"""Дифф двух графов воронки «по смыслу» – было / стало.

Шаги сравниваются по неизменяемому id, а не по key: key поменялся при том же
id – это «переименован», а не «удалён + добавлен». Раскладка, полосы и
заметки в сравнение не входят – это оформление, в бот оно не уходит.

Каждое изменение – строка по-русски теми же словами, что на карточке холста
(фразы – `phrases.py`, порт web/src/phrases.ts):

    Шаг «z_02»: время «через 3 мин» → «через 5 мин»
    Шаг «z_05» удалён
    Добавлена стрелка z_01 → z_06 (параллельно · через 10 мин)

Результат `diff_graphs(old, new)`:
    changes   [{group, kind, text, step_id, step_key, edge_id, field, before, after}]
              group: settings / added / removed / renamed / changed / edges
    steps     {step_id: added | removed | renamed | changed | tombstoned} – подсветка холста
    ghosts    удалённые шаги с их старым местом на холсте (бледная «тень»)
    summary   {всего и по группам}
    warnings  предупреждения публикации (не блокируют): время, переименование…
"""
from __future__ import annotations

import html
import json
import re
from typing import Any

from .phrases import EDGE_KIND_LABEL, STEP_TYPE_LABEL, condition_badges, duration, edge_phrase, schedule_phrase

GROUPS = ["settings", "added", "removed", "renamed", "changed", "edges"]
GROUP_TITLE = {
    "settings": "Настройки воронки",
    "added": "Добавленные шаги",
    "removed": "Удалённые и снятые шаги",
    "renamed": "Переименованные шаги",
    "changed": "Изменённые шаги",
    "edges": "Стрелки",
}

_TAG = re.compile(r"<[^<>]+>")
_WS = re.compile(r"\s+")

# ── форматирование значений ─────────────────────────────────────────────────


def plain(text: str | None) -> str:
    if not text:
        return ""
    return _WS.sub(" ", html.unescape(_TAG.sub("", text))).strip()


def _cut(s: str, n: int = 60) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def fmt(v: Any) -> str:
    if v is None or v == "":
        return "–"
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return f"«{_cut(plain(v) or v)}»"
    return _cut(json.dumps(v, ensure_ascii=False), 80)


def _fname(ref: str | None) -> str:
    if not ref:
        return "–"
    return ref.rsplit("/", 1)[-1]


def text_pair(a: str, b: str, width: int = 56) -> tuple[str, str]:
    """Кусочки двух текстов вокруг первого отличия: «…ждём в 11:00» / «…ждём в 12:00»."""
    i = 0
    n = min(len(a), len(b))
    while i < n and a[i] == b[i]:
        i += 1
    j = 0
    while j < n - i and a[len(a) - 1 - j] == b[len(b) - 1 - j]:
        j += 1
    start = max(0, i - 18)
    # не резать слово пополам
    while start > 0 and a[start - 1] != " ":
        start -= 1
        if i - start > 30:
            break

    def piece(s: str) -> str:
        end = len(s) - j
        mid = s[start:end]
        tail = s[end:end + 12] if j else ""
        out = ("…" if start else "") + mid + tail
        if len(out) > width:
            out = out[: width - 1] + "…"
        elif end + 12 < len(s):
            out += "…"
        return out

    return piece(a), piece(b)


# ── сравнение шагов ─────────────────────────────────────────────────────────

MEDIA_LABEL = {
    "image": "картинка", "images": "несколько картинок", "image_lm": "картинка под лидмагнит",
    "video_note": "кружок", "video_fallback": "запасное видео для кружка", "video": "видео",
    "animation": "GIF", "rich": "статья", "media_optional": "нет ролика – слать текст",
}
SETTINGS_LABEL = {
    "contact": ("запрос телефона", {
        "contact_button": "надпись кнопки контакта", "skip_if_known": "пропускать, если телефон известен",
        "always_send_text": "всегда слать текст", "text_if_known": "текст «телефон известен»",
        "manual_phone_max": "MAX: номер сообщением", "manual_phone_max_text": "текст запроса номера в MAX",
        "manual_phone_retry_text": "повтор запроса номера"}),
    "email": ("запрос почты", {
        "skip_if_known": "пропускать, если почта известна", "text_if_known": "текст «почта известна»",
        "retry_text": "повтор запроса почты"}),
    "survey": ("опрос", {
        "qid": "номер вопроса", "multi": "несколько ответов", "final": "последний вопрос",
        "free": "свободный ответ", "done_button": "кнопка «готово»", "edit": "можно изменить ответ"}),
    "call": ("звонок", {"audio_url": "запись (ссылка)", "audio_id": "запись №", "text": "текст звонка"}),
    "sms": ("SMS", {"text": "текст"}),
    "claim": ("кнопка-действие", {"ack_text": "ответ на нажатие", "keeps_chain": "не рвёт цепочку"}),
    "pin": ("закреп", {"pin": "закрепить", "air_pin": "закреп эфира"}),
}
TOP_LABEL = {"title": "название карточки", "role": "роль шага", "role_day": "день роли",
             "diploma_template": "шаблон диплома", "advanced": "расширенные поля"}


def _g(d: dict | None, *path: str, default: Any = None) -> Any:
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return default if cur is None else cur


def _text_lines(where: str, a: str | None, b: str | None) -> list[tuple[str, Any, Any]]:
    a, b = a or "", b or ""
    if a == b:
        return []
    pa, pb = plain(a), plain(b)
    if not pa and pb:
        return [(f"{where} добавлен: «{_cut(pb)}»", a, b)]
    if pa and not pb:
        return [(f"{where} убран", a, b)]
    if pa == pb:
        return [(f"{where}: изменено оформление (жирный, ссылки…)", a, b)]
    x, y = text_pair(pa, pb)
    return [(f"{where}: «{x}» → «{y}»", a, b)]


def _max_state(v: Any) -> str:
    if v is None:
        return "как в TG"
    if v == "" or v == []:
        return "скрыт" if isinstance(v, str) else "скрыты"
    return "свой" if isinstance(v, str) else "свои"


def _btn_name(b: dict) -> str:
    return b.get("text") or b.get("url") or b.get("callback_data") or "…"


def _buttons_lines(where: str, a: list | None, b: list | None) -> list[tuple[str, Any, Any]]:
    if a == b:
        return []
    a, b = a or [], b or []
    out: list[tuple[str, Any, Any]] = []
    if len(a) == len(b):
        for x, y in zip(a, b):
            if x == y:
                continue
            if x.get("text") != y.get("text"):
                out.append((f"{where}: кнопка «{_btn_name(x)}» → «{_btn_name(y)}»", x, y))
            for f, label in (("url", "ссылка"), ("callback_data", "действие"),
                             ("web_app", "мини-апп"), ("style", "цвет")):
                if x.get(f) != y.get(f):
                    out.append((f"{where}: кнопка «{_btn_name(y)}»: {label} {fmt(x.get(f))} → {fmt(y.get(f))}",
                                x.get(f), y.get(f)))
        return out
    names_a = [_btn_name(x) for x in a]
    names_b = [_btn_name(x) for x in b]
    for n in names_b:
        if n not in names_a:
            out.append((f"{where}: добавлена кнопка «{n}»", None, n))
    for n in names_a:
        if n not in names_b:
            out.append((f"{where}: убрана кнопка «{n}»", n, None))
    if not out:
        out.append((f"{where}: кнопок было {len(a)}, стало {len(b)}", len(a), len(b)))
    return out


def step_field_changes(a: dict, b: dict) -> list[dict]:
    """Изменения одного шага (без key и без стрелок) – [{field, text, before, after}]."""
    out: list[dict] = []

    def add(field: str, text: str, before: Any = None, after: Any = None) -> None:
        out.append({"field": field, "text": text, "before": before, "after": after})

    if a.get("type") != b.get("type"):
        add("type", f"тип «{STEP_TYPE_LABEL.get(a.get('type'), a.get('type'))}» → "
                    f"«{STEP_TYPE_LABEL.get(b.get('type'), b.get('type'))}»", a.get("type"), b.get("type"))

    # текст и кнопки
    ta, tb = _g(a, "content", "tg", default={}), _g(b, "content", "tg", default={})
    for t, x, y in _text_lines("текст TG", ta.get("text"), tb.get("text")):
        add("content.tg.text", t, x, y)
    for t, x, y in _buttons_lines("кнопки TG", ta.get("buttons"), tb.get("buttons")):
        add("content.tg.buttons", t, x, y)
    if ta.get("claim_button_text") != tb.get("claim_button_text"):
        add("content.tg.claim_button_text",
            f"кнопка-действие TG: {fmt(ta.get('claim_button_text'))} → {fmt(tb.get('claim_button_text'))}",
            ta.get("claim_button_text"), tb.get("claim_button_text"))
    ma, mb = _g(a, "content", "max", default={}), _g(b, "content", "max", default={})
    xa, xb = ma.get("text"), mb.get("text")
    if xa != xb:
        sa, sb = _max_state(xa), _max_state(xb)
        if sa == sb == "свой":
            for t, x, y in _text_lines("текст MAX", xa, xb):
                add("content.max.text", t, x, y)
        else:
            tail = f" «{_cut(plain(xb))}»" if sb == "свой" else ""
            add("content.max.text", f"текст MAX: {sa} → {sb}{tail}", xa, xb)
    ba, bb = ma.get("buttons"), mb.get("buttons")
    if ba != bb:
        sa, sb = _max_state(ba), _max_state(bb)
        if sa == sb == "свои":
            for t, x, y in _buttons_lines("кнопки MAX", ba, bb):
                add("content.max.buttons", t, x, y)
        else:
            add("content.max.buttons", f"кнопки MAX: {sa} → {sb}", ba, bb)
    ca, cb = ma.get("claim_button_text"), mb.get("claim_button_text")
    if ca != cb:
        la = "как в TG" if ca is None else ("скрыта" if ca == "" else fmt(ca))
        lb = "как в TG" if cb is None else ("скрыта" if cb == "" else fmt(cb))
        add("content.max.claim_button_text", f"кнопка-действие MAX: {la} → {lb}", ca, cb)
    va, vb = _g(a, "content", "variants", default=[]), _g(b, "content", "variants", default=[])
    if va != vb:
        what = f"было {len(va)}, стало {len(vb)}" if len(va) != len(vb) else "изменены"
        add("content.variants", f"варианты по когортам: {what}", len(va), len(vb))

    # медиа
    mda, mdb = a.get("media") or {}, b.get("media") or {}
    for f, label in MEDIA_LABEL.items():
        x, y = mda.get(f), mdb.get(f)
        if x == y or (not x and not y):
            continue
        if isinstance(x, bool) or isinstance(y, bool):
            add(f"media.{f}", f"{label}: {fmt(bool(x))} → {fmt(bool(y))}", x, y)
        elif isinstance(x, list) or isinstance(y, list):
            add(f"media.{f}", f"{label}: {len(x or [])} → {len(y or [])} шт.", x, y)
        elif not x:
            add(f"media.{f}", f"{label} добавлена: {_fname(y)}" if label.endswith(("а", "я")) else
                f"{label} добавлен: {_fname(y)}", x, y)
        elif not y:
            add(f"media.{f}", f"{label} убрана" if label.endswith(("а", "я")) else f"{label} убран", x, y)
        else:
            add(f"media.{f}", f"{label}: {_fname(x)} → {_fname(y)}", x, y)

    # время
    sa, sb = a.get("schedule") or {}, b.get("schedule") or {}
    core = lambda s: {k: v for k, v in s.items()  # noqa: E731
                      if k not in ("skip_if_past", "dnd_exempt", "min_delay_seconds")}
    if core(sa) != core(sb):
        pa, pb = schedule_phrase(sa), schedule_phrase(sb)
        pa, pb = pa or "по стрелке", pb or "по стрелке"
        if pa == pb:
            add("schedule", f"время: {pa} (изменены подробности расписания)", sa, sb)
        else:
            add("schedule", f"время «{pa}» → «{pb}»", sa, sb)
    if bool(sa.get("dnd_exempt")) != bool(sb.get("dnd_exempt")):
        add("schedule.dnd_exempt", f"можно ночью: {fmt(bool(sa.get('dnd_exempt')))} → "
                                   f"{fmt(bool(sb.get('dnd_exempt')))}", sa.get("dnd_exempt"), sb.get("dnd_exempt"))
    if (sa.get("min_delay_seconds") or 0) != (sb.get("min_delay_seconds") or 0):
        w = lambda v: f"через {duration(v)}" if v else "без ограничения"  # noqa: E731
        add("schedule.min_delay_seconds", f"не раньше чем: {w(sa.get('min_delay_seconds') or 0)} → "
                                          f"{w(sb.get('min_delay_seconds') or 0)}",
            sa.get("min_delay_seconds"), sb.get("min_delay_seconds"))
    if sa.get("skip_if_past", True) != sb.get("skip_if_past", True):
        w = lambda v: "пропустить" if v else "всё равно отправить"  # noqa: E731
        add("schedule.skip_if_past", f"если время прошло: {w(sa.get('skip_if_past', True))} → "
                                     f"{w(sb.get('skip_if_past', True))}",
            sa.get("skip_if_past"), sb.get("skip_if_past"))

    # условия
    cda, cdb = a.get("conditions") or {}, b.get("conditions") or {}
    if cda != cdb:
        bda = {x["icon"] + x["text"]: x for x in condition_badges(cda)}
        bdb = {x["icon"] + x["text"]: x for x in condition_badges(cdb)}
        gone = [x for k, x in bda.items() if k not in bdb]
        new = [x for k, x in bdb.items() if k not in bda]
        for g in list(gone):
            pair = next((n for n in new if n["icon"] == g["icon"]), None)
            if pair:
                add("conditions", f"условие «{g['icon']} {g['text']}» → «{pair['icon']} {pair['text']}»", g, pair)
                gone.remove(g)
                new.remove(pair)
        for g in gone:
            add("conditions", f"условие убрано: {g['icon']} {g['text']}", g, None)
        for n in new:
            add("conditions", f"условие добавлено: {n['icon']} {n['text']}", None, n)
        if cda.get("bizon_room_max", "") != cdb.get("bizon_room_max", ""):
            add("conditions.bizon_room_max", f"зал MAX: {fmt(cda.get('bizon_room_max'))} → "
                                             f"{fmt(cdb.get('bizon_room_max'))}")
        if cda.get("gate_window_hours", 4) != cdb.get("gate_window_hours", 4):
            add("conditions.gate_window_hours", f"окно проверки эфира: {cda.get('gate_window_hours', 4)} ч → "
                                                f"{cdb.get('gate_window_hours', 4)} ч")
        if not attendance_same_room(cda, cdb) and not any(o["field"] == "conditions" for o in out):
            add("conditions.bizon_room", f"зал эфира: {fmt(cda.get('bizon_room'))} → {fmt(cdb.get('bizon_room'))}")

    # настройки по типам
    for grp, (glabel, fields) in SETTINGS_LABEL.items():
        ga, gb = a.get(grp) or {}, b.get(grp) or {}
        if ga == gb:
            continue
        for f in sorted(set(ga) | set(gb), key=lambda k: list(fields).index(k) if k in fields else 99):
            if ga.get(f) != gb.get(f):
                add(f"{grp}.{f}", f"{glabel} → {fields.get(f, f)}: {fmt(ga.get(f))} → {fmt(gb.get(f))}",
                    ga.get(f), gb.get(f))
    for f, label in TOP_LABEL.items():
        if (a.get(f) or None) != (b.get(f) or None):
            add(f, f"{label}: {fmt(a.get(f))} → {fmt(b.get(f))}", a.get(f), b.get(f))
    return out


def attendance_same_room(a: dict, b: dict) -> bool:
    return (a.get("bizon_room") or None) == (b.get("bizon_room") or None)


# ── настройки воронки ───────────────────────────────────────────────────────

_DAY = {"today": "сегодня", "tomorrow": "завтра", "fixed": "дата"}


def _windows(ws: list[dict]) -> str:
    return "; ".join(f"рег {w.get('from')}–{w.get('to')} → "
                     f"{w.get('date') if w.get('day') == 'fixed' else _DAY.get(w.get('day'), w.get('day'))}"
                     for w in ws or [])


def settings_changes(a: dict, b: dict, key_of) -> list[dict]:
    out: list[dict] = []

    def add(field: str, text: str, before: Any = None, after: Any = None) -> None:
        out.append({"field": field, "text": text, "before": before, "after": after})

    if a.get("id") != b.get("id"):
        add("id", f"id воронки: {fmt(a.get('id'))} → {fmt(b.get('id'))}", a.get("id"), b.get("id"))
    if (a.get("title") or "") != (b.get("title") or ""):
        add("title", f"название воронки: {fmt(a.get('title'))} → {fmt(b.get('title'))}")
    ra, rb = a.get("branch_router"), b.get("branch_router")
    if not ra and not rb:
        fa = (a.get("steps") or [{}])[0].get("id") if a.get("steps") else None
        fb = (b.get("steps") or [{}])[0].get("id") if b.get("steps") else None
        if fa and fb and fa != fb:
            add("start", f"первый шаг воронки: «{key_of(fa)}» → «{key_of(fb)}»", fa, fb)
    elif ra and not rb:
        add("branch_router", "ветки по времени регистрации убраны")
    elif rb and not ra:
        add("branch_router", "включены ветки по времени регистрации: " +
            ", ".join(f"{x['id']} (эфир {x['webinar_time']})" for x in rb.get("branches", [])))
    elif ra != rb:
        ba = {x["id"]: x for x in ra.get("branches", [])}
        bb = {x["id"]: x for x in rb.get("branches", [])}
        for bid, x in bb.items():
            if bid not in ba:
                add(f"branch.{bid}", f"добавлена ветка {bid} (эфир {x['webinar_time']})")
        for bid in ba:
            if bid not in bb:
                add(f"branch.{bid}", f"убрана ветка {bid}")
        for bid, x in ba.items():
            y = bb.get(bid)
            if not y or x == y:
                continue
            if x.get("webinar_time") != y.get("webinar_time"):
                add(f"branch.{bid}.webinar_time", f"ветка {bid}: эфир {x['webinar_time']} → {y['webinar_time']}")
            if x.get("windows") != y.get("windows"):
                add(f"branch.{bid}.windows", f"ветка {bid}: «{_windows(x.get('windows'))}» → "
                                             f"«{_windows(y.get('windows'))}»")
            if x.get("repeat_offset_minutes", 0) != y.get("repeat_offset_minutes", 0):
                add(f"branch.{bid}.repeat", f"ветка {bid}: повтор через {x.get('repeat_offset_minutes', 0)} мин → "
                                            f"{y.get('repeat_offset_minutes', 0)} мин")
            if x.get("entry_step") != y.get("entry_step"):
                add(f"branch.{bid}.entry", f"ветка {bid}: вход «{key_of(x.get('entry_step'))}» → "
                                           f"«{key_of(y.get('entry_step'))}»")
    ea, eb = a.get("entry_variants") or [], b.get("entry_variants") or []
    if ea != eb:
        what = f"было {len(ea)}, стало {len(eb)}" if len(ea) != len(eb) else "изменены"
        add("entry_variants", f"входы по когортам: {what}", len(ea), len(eb))
    wa, wb = a.get("webinar_room") or {}, b.get("webinar_room") or {}
    for f, label in (("stream_url", "ссылка на эфир"), ("bizon_room", "зал Bizon"),
                     ("replaces_room", "заменяет зал")):
        if (wa.get(f) or "") != (wb.get(f) or ""):
            add(f"webinar_room.{f}", f"{label}: {fmt(wa.get(f))} → {fmt(wb.get(f))}")
    if (a.get("air_email_profile") or "") != (b.get("air_email_profile") or ""):
        add("air_email_profile", f"профиль писем: {fmt(a.get('air_email_profile'))} → "
                                 f"{fmt(b.get('air_email_profile'))}")
    if bool(a.get("resend_entry_on_repeat")) != bool(b.get("resend_entry_on_repeat")):
        add("resend_entry_on_repeat", f"повторный /start шлёт первое сообщение ещё раз: "
                                      f"{fmt(bool(a.get('resend_entry_on_repeat')))} → "
                                      f"{fmt(bool(b.get('resend_entry_on_repeat')))}")
    if a.get("max_video_note_caption", True) != b.get("max_video_note_caption", True):
        add("max_video_note_caption", f"подпись к кружку в MAX: {fmt(a.get('max_video_note_caption', True))} → "
                                      f"{fmt(b.get('max_video_note_caption', True))}")
    return out


# ── стрелки ─────────────────────────────────────────────────────────────────

def _pair_edges(ea: dict[str, dict], eb: dict[str, dict]):
    """[(старая, новая)] + несопоставленные. Сперва по id, потом по смыслу."""
    pairs = [(ea[i], eb[i]) for i in ea if i in eb]
    la = [e for i, e in ea.items() if i not in eb]
    lb = [e for i, e in eb.items() if i not in ea]

    def target(e: dict):
        return e.get("to_step") or ("funnel:" + str(e.get("to_funnel")))

    for by_target in (True, False):
        def key(e: dict, by_target: bool = by_target):
            return (e["from_step"], e["kind"], target(e)) if by_target else (e["from_step"], e["kind"])
        rest_a = []
        for x in la:
            if not by_target and x["kind"] == "also_fire":   # параллельных бывает несколько
                rest_a.append(x)
                continue
            cands = [y for y in lb if key(y) == key(x)]
            twins = [z for z in la if key(z) == key(x)]
            if len(cands) == 1 and len(twins) == 1:
                pairs.append((x, cands[0]))
                lb.remove(cands[0])
            else:
                rest_a.append(x)
        la = rest_a
    return pairs, la, lb


# ── главное ─────────────────────────────────────────────────────────────────

def _positions(g: dict) -> dict:
    return ((g.get("layout") or {}).get("positions")) or {}


def diff_graphs(old: dict | None, new: dict) -> dict:
    old = old or {"steps": [], "edges": []}
    sa = {s["id"]: s for s in old.get("steps", [])}
    sb = {s["id"]: s for s in new.get("steps", [])}
    unresolved = {u["id"]: u["key"] for g in (old, new) for u in g.get("unresolved", [])}

    def key_of(sid: str | None) -> str:
        if not sid:
            return "?"
        s = sb.get(sid) or sa.get(sid)
        return s["key"] if s else unresolved.get(sid, sid[:8])

    changes: list[dict] = []
    status: dict[str, str] = {}

    def add(group: str, kind: str, text: str, *, step: dict | None = None, edge: dict | None = None,
            field: str | None = None, before: Any = None, after: Any = None) -> None:
        changes.append({"group": group, "kind": kind, "text": text,
                        "step_id": step["id"] if step else (edge or {}).get("to_step"),
                        "step_key": step["key"] if step else None,
                        "edge_id": edge["id"] if edge else None,
                        "field": field, "before": before, "after": after})

    for c in settings_changes(old, new, key_of):
        add("settings", "setting", c["text"], field=c["field"], before=c["before"], after=c["after"])

    # шаги
    tombstoned: set[str] = set()
    for sid, s in sb.items():
        if sid not in sa:
            status[sid] = "added"
            add("added", "step_added", f"Добавлен шаг «{s['key']}» – {STEP_TYPE_LABEL.get(s['type'], s['type'])}",
                step=s)
    ghosts = []
    pos_a = _positions(old)
    for sid, s in sa.items():
        if sid in sb:
            continue
        status[sid] = "removed"
        same_key = any(x["key"] == s["key"] for x in sb.values())
        hint = " (новый шаг с тем же именем – это другой шаг)" if same_key else ""
        add("removed", "step_removed", f"Шаг «{s['key']}» удалён{hint}", step=s)
        ghosts.append({"id": sid, "key": s["key"], "type": s["type"], "title": s.get("title", ""),
                       "text": _cut(plain(_g(s, "content", "tg", "text", default="")), 140),
                       "position": pos_a.get(sid)})
    edges_b = {e["id"]: e for e in new.get("edges", [])}
    for sid, b in sb.items():
        a = sa.get(sid)
        if a is None:
            continue
        if a["key"] != b["key"]:
            status[sid] = "renamed"
            add("renamed", "step_renamed", f"Шаг «{a['key']}» переименован в «{b['key']}»", step=b,
                field="key", before=a["key"], after=b["key"])
        if a.get("type") != "jump" and b.get("type") == "jump":
            tombstoned.add(sid)
            status[sid] = "tombstoned"
            to = [e for e in edges_b.values() if e["from_step"] == sid and e["kind"] == "jump"]
            where = f" – люди с него переходят на «{key_of(to[0]['to_step'])}»" if to else ""
            add("removed", "step_tombstoned", f"Шаг «{b['key']}» удалён{where}", step=b)
            continue
        fields = step_field_changes(a, b)
        for c in fields:
            add("changed", "step_changed", f"Шаг «{b['key']}»: {c['text']}", step=b,
                field=c["field"], before=c["before"], after=c["after"])
        if fields and sid not in status:
            status[sid] = "changed"

    # стрелки
    ea = {e["id"]: e for e in old.get("edges", [])}
    eb = edges_b
    pairs, gone, new_e = _pair_edges(ea, eb)
    incoming_b: dict[str, int] = {}
    for e in eb.values():
        if e.get("to_step"):
            incoming_b[e["to_step"]] = incoming_b.get(e["to_step"], 0) + 1

    def target_name(e: dict) -> str:
        if e["kind"] == "chain_to_funnel":
            return f"воронка «{e.get('to_funnel')}»"
        return key_of(e.get("to_step"))

    for x, y in pairs:
        if y["from_step"] in tombstoned:
            continue
        fk = key_of(y["from_step"])
        from_a, from_b = sa.get(x["from_step"]), sb.get(y["from_step"])
        tgt_a, tgt_b = x.get("to_step") or x.get("to_funnel"), y.get("to_step") or y.get("to_funnel")
        pa, pb = edge_phrase(x, from_a), edge_phrase(y, from_b)
        if tgt_a != tgt_b:
            add("edges", "edge_retargeted",
                f"Стрелка из «{fk}» ({EDGE_KIND_LABEL.get(y['kind'], y['kind'])}) теперь ведёт в "
                f"«{target_name(y)}» (было «{target_name(x)}»)", edge=y, field="to", before=tgt_a, after=tgt_b)
            if y.get("to_step") and y["to_step"] in sb and y["to_step"] not in status:
                status[y["to_step"]] = "changed"
            continue
        if (x["kind"] == y["kind"] and x.get("delay_seconds") == y.get("delay_seconds")
                and x.get("typing_before") == y.get("typing_before")):
            continue
        if y["kind"] == "chain_to_funnel" or not y.get("to_step") or x["kind"] != y["kind"]:
            add("edges", "edge_changed", f"Стрелка {fk} → {target_name(y)}: «{pa}» → «{pb}»", edge=y,
                field="delay" if x["kind"] == y["kind"] else "kind",
                before=x.get("delay_seconds"), after=y.get("delay_seconds"))
            if y.get("to_step") in sb and y["to_step"] not in status:
                status[y["to_step"]] = "changed"
            continue
        tk = key_of(y["to_step"])
        suffix = "" if (y["kind"] == "done" and incoming_b.get(y["to_step"], 0) <= 1) else f" (стрелка из «{fk}»)"
        add("edges", "edge_changed", f"Шаг «{tk}»: время «{pa}» → «{pb}»{suffix}", edge=y,
            field="delay", before=x.get("delay_seconds"), after=y.get("delay_seconds"))
        if y["to_step"] in sb and y["to_step"] not in status:
            status[y["to_step"]] = "changed"
    for e in new_e:
        if e["from_step"] in tombstoned and e["kind"] == "jump":
            continue
        add("edges", "edge_added", f"Добавлена стрелка {key_of(e['from_step'])} → {target_name(e)} "
                                   f"({edge_phrase(e, sb.get(e['from_step']))})", edge=e)
    for e in gone:
        if e["from_step"] not in sb or (e.get("to_step") and e["to_step"] not in sb):
            continue          # шаг удалён – строка уже есть
        if e["from_step"] in tombstoned:
            continue
        ch = {"group": "edges", "kind": "edge_removed",
              "text": f"Удалена стрелка {key_of(e['from_step'])} → {target_name(e)} "
                      f"({edge_phrase(e, sa.get(e['from_step']))})",
              "step_id": e.get("to_step"), "step_key": None, "edge_id": e["id"],
              "field": None, "before": None, "after": None}
        changes.append(ch)

    order = {g: i for i, g in enumerate(GROUPS)}
    changes.sort(key=lambda c: order[c["group"]])
    summary = {g: sum(1 for c in changes if c["group"] == g) for g in GROUPS}
    summary["total"] = len(changes)
    return {"changes": changes, "steps": status, "ghosts": ghosts, "summary": summary,
            "warnings": publish_warnings(changes)}


def publish_warnings(changes: list[dict]) -> list[dict]:
    """Что публикация сделает с людьми внутри воронки (исследование §6.1) – не блокирует."""
    out = []
    for c in changes:
        if c["kind"] == "step_renamed":
            out.append({"step_id": c["step_id"], "text":
                        f"{c['text']}: в нынешнем движке таймеры привязаны к имени – у тех, кому шаг "
                        "уже запланирован, он не придёт"})
        elif c["kind"] == "step_changed" and c["field"] == "schedule":
            out.append({"step_id": c["step_id"], "text":
                        f"{c['text']} – уже поставленные таймеры не пересчитаются, новое время – для новых"})
        elif c["kind"] == "edge_changed" and c["field"] == "delay":
            out.append({"step_id": c["step_id"], "text":
                        f"{c['text']} – у кого таймер уже стоит, придёт по старому времени"})
        elif c["field"] == "start":
            out.append({"step_id": None, "text": f"{c['text']} – уже вошедших это не касается"})
    return out


def removed_without_tombstone(published: dict | None, draft: dict) -> list[dict]:
    """Шаги опубликованной версии, которых в черновике нет вовсе (ни шагом, ни надгробием)."""
    if not published:
        return []
    ids = {s["id"] for s in draft.get("steps", [])}
    return [s for s in published.get("steps", []) if s["id"] not in ids]
