"""Фрейм-«расписание»: полосы дней, колонки слева направо, подписи-тайминги.

Так нарисован evergreen-автовебинар (доска с полосами дней) – и так же его переносил человек (bot-engine: launches/<slug>/scripts/
extract_avtoveb_miro.py → gen_avtoveb.py → funnel_avtoveb.yaml):

  * по вертикали – полосы: регистрация сверху, под ней «1 день» … «5 день» (зелёные
    стикеры в начале полосы). Граница полос – пустая горизонтальная щель между ними;
  * внутри дня – хронология слева направо, одна колонка – одно сообщение: картинка,
    текст, кнопки, под ними подпись-тайминг («за 30 мин до эфира», «через 17 мин
    эфира», «старт эфира», «в 9:00 по МСК»). Стрелок в днях почти нет – порядок задают
    колонки, время – подписи; если стрелка есть, подпись висит на ней;
  * стикер, начинающийся словом «повтор», делит день на эфир (слева) и повтор (справа);
    в нём же правило веток: «Если зарегался до 12:00 – стартует каждый день в 13 с
    повтором в 18, если с 12 до 17 – в 18 с повтором в 13»;
  * голубой и светло-жёлтый стикеры «в 13:05» / «в 18:05» под колонкой – то же время,
    пересчитанное в часы двух веток (подсказка людям, не отдельное правило);
  * ромб «+ автозвонок за час до эфира» – звонок в цепочке дня.

Как это ложится в воронку (так же, как у человека):
  * время шага – от эфира (`webinar_offset_minutes`, «за 30 мин» = -30, «через 17 мин
    эфира» = +17), в блоке повтора – от повтора (`anchor: repeat`); «в 9:00 по МСК» –
    часы (`absolute_time_msk` + `day_offset`); «в 9:50 ИЛИ в 15:50» – разное смещение
    по веткам;
  * внутри дня шаги связаны по порядку колонок, каждый день – своя цепочка, все дни
    армируются разом из входа (`also_fire`): сквозная цепочка теряет утро следующего
    дня у вечерней ветки (документация bot-engine);
  * блок повтора – только тем, кто не был на эфире этого дня (по правилу прошлого
    переноса: тексты там обращаются к пропустившим).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .board import Board, Item, Message
from .stickers import _durations

DAY_RE = re.compile(r"^\s*(\d{1,2})\s*[-–]?\s*(й\s*)?день\s*$", re.I)
REPEAT_RE = re.compile(r"^\s*повтор\b", re.I)
MISSED_RE = re.compile(r"^\s*если\s+не\s+был", re.I)
HINT_TIME_RE = re.compile(r"^\s*в\s*\d{1,2}[:.]\d{2}\s*$", re.I)
BRANCH_RULE_RE = re.compile(
    r"(?:(?P<a>с)\s*(?P<f>\d{1,2})(?::(?P<fm>\d{2}))?\s*(?:до|по|-|–)\s*|до\s*)(?P<t>\d{1,2})(?::(?P<tm>\d{2}))?"
    r"[^.]*?стартует[^.]*?\bв\s*(?P<w>\d{1,2})(?::(?P<wm>\d{2}))?\s*(?:ч\w*\s*)?с\s+повтор\w*\s+в\s*"
    r"(?P<r>\d{1,2})(?::(?P<rm>\d{2}))?", re.I)
WINDOW_RE = re.compile(r"\bс\s*(\d{1,2})[:.](\d{2})\s*(?:-|–|—)?\s*(?:по|до)\s*(\d{1,2})[:.](\d{2})", re.I)
TIMER_RE = re.compile(r"(поменять|изменить|поставить)\s+таймер", re.I)
MSG_AT_RE = re.compile(r"^\s*сообщени\w*\s+в\s*\d", re.I)
TEXT_TIME_RES = [
    # «ЧЕРЕЗ 1 ЧАС НАЧНЁМ ЭФИР», «5 ПОДАРКОВ УЖЕ ЧЕРЕЗ 30 МИНУТ», «Через 5 минут начинаю»
    (re.compile(r"через\s+(\d+|час|полчаса)\s*(мин\w*|час\w*)?[^.!?\n]{0,40}?(начн|начина|старту|стартан|повтор|эфир|подар)", re.I), -1),
    # «до эфира осталось 15 минут», «ОСТАЛОСЬ 10 МИНУТ»
    (re.compile(r"осталось\s+(\d+|час|полчаса)\s*(мин\w*|час\w*)?", re.I), -1),
]


def _hm(h: str | None, m: str | None) -> int:
    return int(h or 0) * 60 + int(m or 0)


def _fmt(minutes: int) -> str:
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass
class BranchRule:
    webinar: int                # минуты от полуночи
    repeat_offset: int          # минуты от эфира до повтора
    windows: list[tuple[int, int, str]]   # (с, по включительно, today/tomorrow)


@dataclass
class Timing:
    """Разобранная подпись-тайминг."""
    kind: str                   # webinar / clock / relative / none
    minutes: int = 0            # смещение от эфира или часы (минуты от полуночи)
    anchor: str = "webinar"
    by_branch: dict[int, int] = field(default_factory=dict)    # время эфира ветки → смещение
    text: str = ""
    source: str = ""            # откуда время: подпись / стикеры веток / текст сообщения / правило
    overrides: str = ""         # подпись, которую перебили стикеры веток
    bumped: bool = False        # то же время, что у соседа слева, – сдвинут на минуту


def parse_timing(text: str, webinar_times: list[int]) -> Timing:
    s = " ".join((text or "").lower().replace("ё", "е").split())
    s = re.sub(r"^\+?\s*авто\s*звон\w*\s*", "", s)
    t = Timing("none", text=" ".join((text or "").split()))
    t.anchor = "repeat" if "повтор" in s else "webinar"
    clocks = [(int(h), int(m)) for h, m in re.findall(r"(\d{1,2})[:.](\d{2})", s)]
    if clocks and re.match(r"^в\s*\d", s):
        mins = [h * 60 + m for h, m in clocks]
        if len(mins) >= 2 and len(webinar_times) >= 2 and re.search(r"\bили\b", s):
            # «в 9:50 ИЛИ в 15:50» – одна колонка на две ветки: смещение от эфира своей ветки
            offs = {w: c - w for w, c in zip(sorted(webinar_times), mins)}
            first = offs[sorted(webinar_times)[0]]
            t.kind, t.minutes = "webinar", first
            t.by_branch = {w: o for w, o in offs.items() if o != first}
            return t
        t.kind, t.minutes = "clock", mins[0]
        return t
    if re.search(r"^(старт|время|в основное|основное)", s) and not re.search(r"\d+\s*(мин|час|ч\b)", s):
        t.kind, t.minutes = "webinar", 0
        return t
    d, _ = _durations(s)
    if d is None:
        if re.search(r"\bчас\b", s):
            d = 3600
        elif re.search(r"\bминуту\b", s):
            d = 60
        elif re.search(r"полчаса|пол часа", s):
            d = 1800
    if d is None:
        return t
    mins = round(d / 60)
    if s.startswith("за"):
        t.kind, t.minutes = "webinar", -mins
    elif s.startswith("через") and re.search(r"эфир|повтор", s):
        t.kind, t.minutes = "webinar", mins
    elif s.startswith("через"):
        t.kind, t.minutes = "relative", d
    return t


def text_timing(text: str) -> Timing | None:
    """Время из первых строк текста – когда под колонкой нет подписи: «ЧЕРЕЗ 1 ЧАС НАЧНЁМ
    ПЕРВЫЙ ЭФИР» = за час до эфира. Так читал доску и человек."""
    head = " ".join((text or "").split())[:160]
    for rx, sign in TEXT_TIME_RES:
        m = rx.search(head)
        if not m:
            continue
        n, unit = m.group(1).lower(), (m.group(2) or "").lower()
        if n == "час":
            mins = 60
        elif n == "полчаса":
            mins = 30
        else:
            mins = int(n) * (60 if unit.startswith("час") else 1)
        return Timing("webinar", sign * mins, text=m.group(0), source="текст сообщения")
    return None


def _hint_timing(hs: list[Item], wtimes: list[int]) -> Timing | None:
    clocks = sorted({int(h) * 60 + int(m) for x in hs for h, m in re.findall(r"(\d{1,2})[:.](\d{2})", x.text)})
    ws = sorted(wtimes)
    if not clocks or not ws:
        return None
    offs = {w: c - w for w, c in zip(ws, clocks)} if len(clocks) == len(ws) else {w: clocks[0] - ws[0] for w in ws}
    first = offs[ws[0]]
    return Timing("webinar", first, by_branch={w: o for w, o in offs.items() if o != first},
                  text=" / ".join(" ".join(x.text.split()) for x in hs), source="стикеры веток")


def parse_branch_rule(text: str) -> list[BranchRule]:
    out: list[BranchRule] = []
    prev_to = 0
    for m in BRANCH_RULE_RE.finditer(" ".join((text or "").split())):
        frm = _hm(m.group("f"), m.group("fm")) if m.group("a") else prev_to
        to = _hm(m.group("t"), m.group("tm"))
        w = _hm(m.group("w"), m.group("wm"))
        r = _hm(m.group("r"), m.group("rm"))
        rep = r - w if r > w else r + 1440 - w
        out.append(BranchRule(webinar=w, repeat_offset=rep, windows=[(frm, to - 1, "today")]))
        prev_to = to
    return out


@dataclass
class Row:
    idx: int
    day: int | None             # None – полоса входа (регистрация)
    y0: float
    y1: float
    marker: Item | None = None
    repeat_x: float | None = None
    missed_x: float | None = None   # «если не был на эфире дня и его повторе…» – дожим пропустившим
    missed_after: int | None = None  # «…то через 3 часа после повтора» – минуты от повтора
    msgs: list[str] = field(default_factory=list)       # слева направо

    @property
    def title(self) -> str:
        return f"День {self.day}" if self.day else "Вход и регистрация"


@dataclass
class Timeline:
    rows: list[Row]
    row_of: dict[str, Row]                    # сообщение → полоса
    timing: dict[str, Timing]                 # сообщение → разобранная подпись
    label_of: dict[str, list[Item]]           # сообщение → его подписи (и стикеры-подсказки)
    branches: list[BranchRule]
    branch_items: list[str]                   # стикеры с правилом веток
    used: dict[str, str]                      # элемент → как учтён («день», «повтор», «подсказка»…)
    windows: dict[str, list[str]] = field(default_factory=dict)      # сообщение → окна регистрации
    timer: dict[str, int] = field(default_factory=dict)              # сообщение → «поменять таймер на N»
    hint_mismatch: list[tuple[str, Item, str]] = field(default_factory=list)

    def day_rows(self) -> list[Row]:
        return [r for r in self.rows if r.day]


def _gap(intervals: list[tuple[float, float]], lo: float, hi: float) -> float:
    """Середина самой широкой щели в [lo, hi], не закрытой ни одним интервалом."""
    iv = sorted((max(a, lo), min(b, hi)) for a, b in intervals if b > lo and a < hi)
    best, where, cur = -1.0, (lo + hi) / 2, lo
    for a, b in iv:
        if a - cur > best:
            best, where = a - cur, (cur + a) / 2
        cur = max(cur, b)
    if hi - cur > best:
        where = (cur + hi) / 2
    return where


def _dist(it: Item, m: Message) -> float:
    """Расстояние подписи до колонки: подпись стоит под колонкой или слева от неё, над
    стрелкой (так считал и человек – extract_avtoveb_miro.py `_dist`): колонка справа
    получает фору, колонка целиком левее – штраф."""
    x0, x1 = m.left, m.right
    y0, y1 = m.top, m.bottom
    if it.x < x0:
        dx = (x0 - it.x) / 2
    elif it.x > x1:
        dx = (it.x - x1) * 3
    else:
        dx = 0.0
    dy = max(0.0, y0 - it.y, it.y - y1)
    return (dx * dx + dy * dy) ** 0.5


def detect(board: Board) -> Timeline | None:
    items = board.items
    days: dict[int, Item] = {}
    for it in items.values():
        m = DAY_RE.match(it.text or "")
        if m and it.role in ("sticky", "offbot", "note", "button", "timing") and int(m.group(1)) not in days:
            days[int(m.group(1))] = it
    if len(days) < 2 or sorted(days) != list(range(1, len(days) + 1)):
        return None
    msgs = board.messages
    cols = [(m.top, m.bottom) for m in msgs.values()]
    marks = [days[d] for d in sorted(days)]
    top = min([m.top for m in msgs.values()] + [marks[0].top]) - 1
    bounds = [top]
    for a, b in zip([None] + marks, marks):
        lo = a.y if a is not None else top
        bounds.append(_gap(cols, lo, b.y))
    bounds.append(max([m.bottom for m in msgs.values()] + [marks[-1].bottom]) + 1)
    rows = [Row(0, None, bounds[0], bounds[1])]
    for n, d in enumerate(sorted(days), 1):
        rows.append(Row(n, d, bounds[n], bounds[n + 1], marker=days[d]))
    used: dict[str, str] = {days[d].id: f"начало полосы «День {d}»" for d in days}

    def row_at(y: float) -> Row:
        for r in rows:
            if r.y0 <= y < r.y1:
                return r
        return rows[-1] if y >= rows[-1].y1 else rows[0]

    row_of: dict[str, Row] = {}
    for mid, m in msgs.items():
        r = row_at(m.top)
        row_of[mid] = r
    for r in rows:
        r.msgs = sorted((mid for mid, rr in row_of.items() if rr is r),
                        key=lambda mid: (msgs[mid].item.x, msgs[mid].item.y))

    # правило веток и «повтор»
    branches: list[BranchRule] = []
    branch_items: list[str] = []
    for it in sorted(items.values(), key=lambda i: (i.y, i.x)):
        if it.role not in ("sticky", "offbot", "note"):
            continue
        t = " ".join(it.text.split())
        rule = parse_branch_rule(t)
        if rule:
            branch_items.append(it.id)
            if not branches:
                branches = rule
        if it.role == "sticky" and MISSED_RE.match(t):
            r = row_at(it.y)
            if r.day and (r.missed_x is None or it.x < r.missed_x):
                r.missed_x = it.x
                d, _ = _durations(t.lower())
                r.missed_after = round(d / 60) if d is not None else None
            used[it.id] = f"дожим пропустившим эфир и повтор ({r.title})"
            continue
        if REPEAT_RE.match(t):
            r = row_at(it.y)
            if r.day and (r.repeat_x is None or it.x < r.repeat_x):
                r.repeat_x = it.x
            used[it.id] = f"начало блока повтора ({r.title})"
        elif rule:
            used[it.id] = "правило веток: время эфира и повтора по времени регистрации"
    wtimes = [b.webinar for b in branches]

    # подписи-тайминги: на стрелке – к её цели, иначе – к ближайшей колонке своей полосы
    label_of: dict[str, list[Item]] = {}
    windows_st: list[tuple[Item, str, list[str], list]] = []
    on_arrow: dict[str, str] = {}
    for c in board.conns + board.headless:
        for st in c.stickies:
            if st.role == "timing" and c.end in msgs:
                on_arrow.setdefault(st.id, c.end)
    for it in items.values():
        if it.role != "timing":
            continue
        r = row_at(it.y)
        if not r.day:
            continue
        tgt = on_arrow.get(it.id)
        if tgt is None or row_of.get(tgt) is not r:
            pool = [mid for mid in r.msgs]
            if not pool:
                continue
            tgt = min(pool, key=lambda mid: _dist(it, msgs[mid]))
        label_of.setdefault(tgt, []).append(it)
        used[it.id] = "подпись-тайминг сообщения"
    # стикеры полос: «в 13:05» / «в 18:05» под колонкой – время по веткам (поздняя правка
    # копирайтера, ей верим больше, чем подписи), «СООБЩЕНИЕ В 9 50, а также в 16 30» – время
    # колонки без подписи, окна регистрации, «поменять таймер»
    hints: dict[str, list[Item]] = {}
    msg_times: list[tuple[Item, list[int]]] = []
    windows: dict[str, list[str]] = {}
    timer: dict[str, int] = {}
    for it in sorted(items.values(), key=lambda i: (i.y, i.x)):
        if it.role != "sticky" or it.id in used:
            continue
        t = " ".join(it.text.split())
        r = row_at(it.y)
        pool = r.msgs or list(msgs)
        if HINT_TIME_RE.match(t) and r.day:
            mid = min(pool, key=lambda x: _dist(it, msgs[x]))
            hints.setdefault(mid, []).append(it)
            label_of.setdefault(mid, []).append(it)
            used[it.id] = "время колонки по веткам («в 13:05» / «в 18:05»)"
            continue
        if r.day and MSG_AT_RE.match(t):
            tms = [int(h) * 60 + int(m) for h, m in re.findall(r"(\d{1,2})[:. ](\d{2})\b", t)]
            tms = [x for x in dict.fromkeys(tms) if x not in wtimes]
            # одно сообщение на две ветки – ровно по времени на ветку; «добавить сообщения в 9 50
            # и 11 30 … а также в 15 50 и 16 30» – это про несколько колонок, у них свои подписи
            if tms and len(tms) == max(1, len(wtimes)):
                msg_times.append((it, tms))
                continue
        wins = WINDOW_RE.findall(t)
        if wins and re.search(r"пропуска|огранич", t, re.I):
            windows_st.append((it, t, pool, wins))
    timing: dict[str, Timing] = {}
    for r in rows:
        if not r.day:
            continue
        for mid in r.msgs:
            m = msgs[mid]
            cand = [parse_timing(x.text, wtimes) for x in sorted(label_of.get(mid, []), key=lambda i: i.y)
                    if x.role == "timing"]
            if m.call:
                cand.append(parse_timing(m.item.text, wtimes))
            # несколько подписей – последняя осмысленная (так решил и человек: «за 5 мин» и
            # «за 15 мин» под одной колонкой не складываются)
            good = [c for c in cand if c.kind != "none"]
            t = good[-1] if good else Timing("none")
            t.source = "подпись" if good else ""
            if m.call and t.kind == "none":
                t = Timing("webinar", -60, text="+ автозвонок", source="правило: звонок за час до эфира")
            hs = hints.get(mid, [])
            if hs and wtimes:
                ht = _hint_timing(hs, wtimes)
                if ht is not None:
                    if t.kind not in ("none", "relative") and (t.minutes, t.by_branch) != (ht.minutes, ht.by_branch):
                        ht.overrides = t.text
                    t = ht
            timing[mid] = t
        # «СООБЩЕНИЕ В 9 50, а также в 16 30» – ближайшей колонке без времени
        for it, tms in msg_times:
            if row_at(it.y) is not r:
                continue
            free = [mid for mid in r.msgs if timing[mid].kind == "none"] or r.msgs
            mid = min(free, key=lambda x: _dist(it, msgs[x]))
            ws = sorted(wtimes)
            if not ws:
                continue
            offs = {w: c - w for w, c in zip(ws, tms)} if len(tms) >= len(ws) else {w: tms[0] - ws[0] for w in ws}
            first = offs[ws[0]]
            timing[mid] = Timing("webinar", first, by_branch={w: o for w, o in offs.items() if o != first},
                                 text=" ".join(it.text.split()), source="стикер «сообщение в …»")
            label_of.setdefault(mid, []).append(it)
            used[it.id] = "время колонки («сообщение в 9:50 …»)"
        # без подписи – время из текста («ЧЕРЕЗ 1 ЧАС НАЧНЁМ ЭФИР», «осталось 15 минут»)
        for mid in r.msgs:
            if timing[mid].kind == "none" and not msgs[mid].circle:
                tt = text_timing(msgs[mid].item.text)
                if tt is not None:
                    timing[mid] = tt
        # «через N» без слова «эфир» (правило, выбранное по практике переносов): после шага по часам –
        # от него (9:00 → «через 5 минут» = 9:05); после шага от эфира короткая пауза (≤ 10 мин) –
        # от него, длинная – от начала эфира («через 2 часа 45 минут» = +165)
        prev: Timing | None = None
        for mid in r.msgs:
            t = timing[mid]
            if t.kind == "relative":
                mins = round(t.minutes / 60)
                if prev is not None and prev.kind == "clock":
                    timing[mid] = Timing("clock", prev.minutes + mins, text=t.text, source="подпись (от предыдущего)")
                elif prev is not None and prev.kind == "webinar" and mins <= 10:
                    # короткая пауза – от предыдущего
                    timing[mid] = Timing("webinar", prev.minutes + mins, anchor=prev.anchor,
                                         by_branch={w: o + mins for w, o in prev.by_branch.items()},
                                         text=t.text, source="подпись (от предыдущего)")
                elif prev is not None and prev.kind == "webinar":
                    timing[mid] = Timing("webinar", mins, anchor=prev.anchor, text=t.text,
                                         source="подпись (от начала эфира)")
                t = timing[mid]
            x = msgs[mid].item.x
            if r.missed_x is not None and x >= r.missed_x:
                if t.kind == "none" and r.missed_after is not None:
                    t = timing[mid] = Timing("webinar", r.missed_after, text="если не был на эфире и повторе",
                                             source="стикер «если не был…»")
                if t.kind == "webinar":
                    t.anchor = "repeat"
            elif r.repeat_x is not None and x >= r.repeat_x and t.kind == "webinar":
                t.anchor = "repeat"
            if t.kind != "none":
                prev = t

        # порядок в дне – по времени, при равенстве и без времени – по колонкам
        def base(tm: Timing) -> float | None:
            if tm.kind == "clock":
                return float(tm.minutes)
            if tm.kind == "webinar" and branches:
                return branches[0].webinar + tm.minutes + (branches[0].repeat_offset if tm.anchor == "repeat" else 0)
            return None
        key: dict[str, float] = {}
        last = -1e9
        for n, mid in enumerate(r.msgs):
            b = base(timing[mid])
            key[mid] = (b if b is not None else last) + n * 1e-6
            if b is not None:
                last = b
        r.msgs.sort(key=lambda mid: key[mid])
        # два шага на одно время (звонок и сообщение «за час», два пуша на 14:43) – второй на
        # минуту позже: так порядок не зависит от того, чей таймер сработает первым (так же
        # поступил человек: -60 / -59)
        for a, b2 in zip(r.msgs, r.msgs[1:]):
            ta, tb = timing[a], timing[b2]
            if tb.kind in ("webinar", "clock") and (ta.kind, ta.minutes, ta.anchor) == (tb.kind, tb.minutes, tb.anchor):
                tb.minutes += 1
                tb.by_branch = {w: o + 1 for w, o in tb.by_branch.items()}
                tb.bumped = True

    mismatch: list[tuple[str, Item, str]] = []
    for mid, t in timing.items():
        if t.overrides:
            mismatch.append((mid, hints[mid][0], t.overrides))
    for it, t, pool, wins in windows_st:
        mid = _window_target(it, t, pool, msgs, timing, wtimes)
        windows.setdefault(mid, [])
        for h1, m1, h2, m2 in wins:
            w = f"{int(h1):02d}:{m1}-{int(h2):02d}:{m2}"
            if w not in windows[mid]:
                windows[mid].append(w)
        label_of.setdefault(mid, []).append(it)
        used[it.id] = "окна регистрации: кому сообщение не шлём"
        d, _ = _durations(t)
        if TIMER_RE.search(t) and d is not None:
            timer[mid] = d
    return Timeline(rows=rows, row_of=row_of, timing=timing, label_of=label_of, branches=branches,
                    branch_items=branch_items, used=used, windows=windows, timer=timer,
                    hint_mismatch=mismatch)


def _window_target(st: Item, text: str, pool: list[str], msgs: dict[str, Message],
                   timing: dict[str, Timing], wtimes: list[int]) -> str:
    """К какому сообщению стикер «ограничения … пропускаем сообщение 9:50»: по названному
    времени среди колонок полосы, иначе – ближайшая колонка."""
    named = re.findall(r"сообщени\w*\s*(\d{1,2})[:.](\d{2})", text, re.I)
    if named and wtimes:
        want = int(named[0][0]) * 60 + int(named[0][1])
        for mid in sorted(pool, key=lambda x: _dist(st, msgs[x])):
            t = timing.get(mid)
            if not t:
                continue
            if t.kind == "clock" and t.minutes == want:
                return mid
            if t.kind == "webinar" and t.anchor == "webinar" and any(
                    w + t.by_branch.get(w, t.minutes) == want for w in wtimes):
                return mid
    return min(pool, key=lambda x: _dist(st, msgs[x]))


def windows_uncovered(branches: list[BranchRule]) -> list[tuple[int, int]]:
    cov = sorted((a, b) for br in branches for a, b, _ in br.windows)
    out, cur = [], 0
    for a, b in cov:
        if a > cur:
            out.append((cur, a - 1))
        cur = max(cur, b + 1)
    if cur <= 1439:
        out.append((cur, 1439))
    return out


def _dur(sec: int) -> str:
    """Секунды – словами: «10 мин», «1 ч 30 мин»."""
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    out = ([f"{h} ч"] if h else []) + ([f"{m} мин"] if m else []) + ([f"{s} с"] if s else [])
    return " ".join(out) or "0 с"
