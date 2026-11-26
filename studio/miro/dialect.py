"""Диалект доски – первый шаг импорта: что на этой доске значат цвета и формы.

Досок Miro, с которых переносят воронки, четыре «диалекта», и один цвет в них значит разное:
голубой `#c6dcff` – сообщение в доске «По дням», но плашка времени в доске «Запуск». Поэтому импорт сперва определяет
диалект, а роли элементов читает по его таблице (данные ниже), а не по одному «зелёному».

Как определяется: самый частый цвет фигур с длинным
текстом (≥ 120 символов без ссылок) плюс цвет элементов времени (стикер / плашка).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

GREEN = "green"          # #adf0c7 и соседние оттенки (см. board.is_message_fill)


@dataclass(frozen=True)
class Dialect:
    code: str
    title: str
    # сообщение: (цвет, формы) – цвет GREEN означает «зелёный и близкие»
    message: tuple[tuple[str, frozenset[str]], ...]
    # кнопка своего цвета (кроме белых, которые кнопки во всех диалектах)
    buttons: frozenset[str] = frozenset()
    # кнопка цветом сообщения – только короткий текст без ссылки («👉 ЗАНЯТЬ МЕСТО»)
    buttons_like_message: bool = False
    # элемент времени: плашки-фигуры этих цветов и стикеры этих цветов
    timing_shapes: frozenset[str] = frozenset()
    timing_stickies: frozenset[str] = frozenset()
    notes: str = ""
    evidence: dict = field(default_factory=dict, compare=False)


_RECT = frozenset({"rectangle", "round_rectangle", "flow_chart_process", "can", "wedge_round_rectangle_callout"})
_ROUND = frozenset({"round_rectangle", "circle", "ellipse"})
_ANY = _RECT | frozenset({"circle", "ellipse", "flow_chart_terminator"})

DIALECTS: dict[str, Dialect] = {
    "A": Dialect("A", "«Запуск»", message=((GREEN, _ANY),),
                 timing_stickies=frozenset({"blue", "light_blue", "green"}), timing_shapes=frozenset({"#c6dcff"}),
                 notes="сообщение – зелёный прямоугольник, кнопки белые под ним, время – синий стикер у стрелки"),
    "B": Dialect("B", "«Белые скруглённые / автовеб»", message=(("#ffffff", _ROUND), (GREEN, _ANY)),
                 timing_stickies=frozenset({"cyan", "violet", "light_yellow"}),
                 notes="сообщение – белый скруглённый прямоугольник, время – голубой стикер слева от карточки, "
                       "второй стикер под карточкой – время в повторе"),
    "C": Dialect("C", "«По дням»", message=(("#c6dcff", _ANY), ("#ffffff", frozenset({"round_rectangle"})),
                                               (GREEN, _ANY)),
                 buttons_like_message=True, timing_shapes=frozenset({"#ffdc4a"}),
                 timing_stickies=frozenset({"cyan", "light_yellow"}),
                 notes="сообщение – голубой прямоугольник, кнопки того же цвета, время – жёлтая плашка, "
                       "полосы дней по вертикали, «повтор» – стикер"),
    "D": Dialect("D", "«Короткие воронки»", message=(("#ffffff", _RECT), (GREEN, _ANY)),
                 buttons=frozenset({"#2dc75c"}), timing_shapes=frozenset({"#659df2"}),
                 notes="сообщение – белый прямоугольник, кнопки зелёные #2dc75c, время – синяя плашка на стрелке"),
}

URL_RE = re.compile(r"https?://\S+")


def body_len(text: str) -> int:
    return len(" ".join(URL_RE.sub(" ", text or "").split()))


def _green(fill: str) -> bool:
    from .board import is_message_fill   # noqa: PLC0415 – без цикла импортов
    return is_message_fill(fill)


def fill_matches(spec: str, fill: str) -> bool:
    return _green(fill) if spec == GREEN else (fill or "").lower() == spec


def detect(items: list) -> Dialect:
    """Диалект по элементам фрейма (board.Item)."""
    long: Counter = Counter()
    for it in items:
        if it.type == "shape" and it.w < 4000 and body_len(it.text) >= 120:
            long["green" if _green(it.fill) else (it.fill or "-")] += 1
            if (it.fill or "").lower() in ("#ffffff", "", "white"):
                long["white_round" if it.shape in _ROUND else "white_rect"] += 1
    tshape: Counter = Counter()
    tsticky: Counter = Counter()
    for it in items:
        if it.type == "shape" and it.fill:
            tshape[it.fill.lower()] += 1
        elif it.type == "sticky_note":
            tsticky[it.fill] += 1
    score = {
        "A": long["green"] * 3 + tsticky["blue"],
        "B": long["white_round"] * 3 + tsticky["cyan"],
        "C": long["#c6dcff"] * 3 + tshape["#ffdc4a"],
        "D": long["white_rect"] * 3 + tshape["#659df2"] + tshape["#2dc75c"],
    }
    code = max(score, key=lambda k: (score[k], k == "A"))
    if score[code] == 0:
        code = "A"
    d = DIALECTS[code]
    return Dialect(**{**d.__dict__, "evidence": {"score": score, "long": dict(long.most_common(4))}})
