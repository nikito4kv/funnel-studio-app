"""Сырой снимок фрейма Miro → «доска»: элементы по ролям, сообщения, стрелки, стикеры.

Как нарисованы доски запусков (проверено на фреймах «ЗАПУСК 24-25 СЕНТЯБРЯ», «17 СЕНТЯБРЯ»,
«9-10 СЕНТЯБРЯ», «С ТЕЙХАНОМ», «Запуск 6 августа»):

  * сообщение – зелёный прямоугольник (#adf0c7) с текстом; кружок – зелёный круг, в нём
    сценарий ролика; короткое сообщение – зелёный скруглённый прямоугольник;
  * кнопки – белые прямоугольники прямо под сообщением, одна под другой; ссылка – второй
    строкой в кнопке;
  * картинка – над сообщением в той же колонке;
  * стрелки (коннекторы) – от сообщения или от его кнопки к следующему сообщению;
  * время и условие – синий (иногда зелёный) стикер у стрелки: «через 3 минуты если не
    перешёл в канал»; жёлтый стикер у сообщения – условие самого шага («если контакт ранее
    оставлял – пропускаем этот этап»);
  * первая колонка – описание бота с кнопкой «СТАРТ / НАЧАТЬ», её стрелка ведёт в первое
    сообщение;
  * длинные цветные полосы – заголовки разделов («вход в воронку», «ОПЛАТИЛИ ВИП БИЛЕТ»);
  * жёлтые / оранжевые / розовые прямоугольники и стикеры – страницы сайта, письма,
    заметки для людей – в бот не идут.

Здесь только геометрия и роли, без смысла воронки (он – в build.py).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from .dialect import DIALECTS, GREEN, Dialect, fill_matches
from .dialect import detect as detect_dialect
from .html import plain

MESSAGE_FILLS = {"#adf0c7"}
# Подпись-тайминг фигурой (не стикером): «за 30 мин до эфира», «через 17 мин эфира», «старт эфира»,
# «в 9:00 по МСК». Так размечен фрейм «Автовеб 5-ти дневник» (evergreen): жёлтый прямоугольник под
# колонкой сообщения или на стрелке между колонками.
TIMING_LABEL_RE = re.compile(
    r"^\s*(за\s+(\d|час|полчаса|пол\s*часа|минуту)|через\s|старт\s+эфира|время\s+(эфира|повтора)|"
    r"в\s+основное|основное\s+время|в\s+\d{1,2}[:.]\d{2}|сразу\b)", re.I)
CALL_RE = re.compile(r"авто\s*звон", re.I)
URL_RE = re.compile(r"https?://\S+")
BUTTON_FILLS = {"#ffffff", "#fff", "white", ""}
LANE_MIN_WIDTH = 4000
MESSAGE_SHAPES = {"rectangle", "round_rectangle", "circle", "ellipse", "wedge_round_rectangle_callout",
                  "can", "flow_chart_process", "flow_chart_terminator"}
START_RE = re.compile(r"^\s*(/?start|старт|начать)\b", re.I)
VIA_ROLES = ("sticky", "timing")       # через них стрелка идёт насквозь


def _hex_to_rgb(h: str) -> tuple[int, int, int] | None:
    h = (h or "").strip().lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", h):
        return None
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def is_message_fill(fill: str) -> bool:
    """Зелёный фон сообщения: #adf0c7 и близкие оттенки (постановщик мог взять соседний)."""
    f = (fill or "").strip().lower()
    if f in MESSAGE_FILLS:
        return True
    rgb = _hex_to_rgb(f)
    if not rgb:
        return f in ("light_green", "green")
    r, g, b = rgb
    return g > 180 and g - r > 40 and g - b > 20


@dataclass
class Item:
    id: str
    type: str                  # shape / sticky_note / image / text / card / …
    shape: str
    fill: str
    x: float
    y: float
    w: float
    h: float
    html: str
    raw: dict[str, Any]
    role: str = ""             # message / button / image / sticky / lane / offbot / note / start / decision

    @property
    def text(self) -> str:
        return plain(self.html)

    @property
    def left(self) -> float:
        return self.x - self.w / 2

    @property
    def right(self) -> float:
        return self.x + self.w / 2

    @property
    def top(self) -> float:
        return self.y - self.h / 2

    @property
    def bottom(self) -> float:
        return self.y + self.h / 2

    def hoverlap(self, o: "Item") -> float:
        """Доля пересечения по горизонтали от меньшей ширины."""
        inter = min(self.right, o.right) - max(self.left, o.left)
        return max(0.0, inter) / max(1.0, min(self.w, o.w))

    def box_dist(self, px: float, py: float) -> float:
        dx = max(self.left - px, 0, px - self.right)
        dy = max(self.top - py, 0, py - self.bottom)
        return math.hypot(dx, dy)


@dataclass
class Message:
    item: Item
    buttons: list[Item] = field(default_factory=list)
    image: Item | None = None
    stickies: list[Item] = field(default_factory=list)     # стикеры шага (не у стрелки)
    album: list[Item] = field(default_factory=list)        # ещё крупные картинки над сообщением
    labels: list[Item] = field(default_factory=list)       # подписи-тайминги колонки (фигуры)

    @property
    def id(self) -> str:
        return self.item.id

    @property
    def circle(self) -> bool:
        return self.item.shape in ("circle", "ellipse")

    @property
    def call(self) -> bool:
        return self.item.shape in ("rhombus", "diamond")

    @property
    def top(self) -> float:
        """Верх колонки: картинка над сообщением или само сообщение."""
        return min([self.item.top] + [i.top for i in ([self.image] if self.image else []) + self.album])

    @property
    def bottom(self) -> float:
        return max([self.item.bottom] + [b.bottom for b in self.buttons])

    @property
    def left(self) -> float:
        return min([self.item.left] + [i.left for i in ([self.image] if self.image else []) + self.buttons])

    @property
    def right(self) -> float:
        return max([self.item.right] + [i.right for i in ([self.image] if self.image else []) + self.buttons])

    def item_ids(self) -> list[str]:
        ids = [self.item.id] + [b.id for b in self.buttons]
        if self.image:
            ids.append(self.image.id)
        return ids + [a.id for a in self.album] + [s.id for s in self.stickies] + [x.id for x in self.labels]


@dataclass
class Conn:
    id: str
    start: str | None
    end: str | None
    caption: str
    points: list[tuple[float, float]]            # ломаная для поиска ближайшего стикера
    stickies: list[Item] = field(default_factory=list)
    via: list[str] = field(default_factory=list)  # id стикеров, через которые шла стрелка


@dataclass
class Board:
    board_id: str
    frame_id: str
    frame_title: str
    items: dict[str, Item]
    messages: dict[str, Message]
    owner: dict[str, str]                 # id элемента → id сообщения, которому он принадлежит
    conns: list[Conn]
    start_buttons: list[Item]
    near: dict[str, dict]                 # стикер → ближайшие стрелка и сообщение (с расстояниями)
    lanes: list[Item]
    others: list[Item]                    # страницы, письма, ромбы, заметки – в бот не идут
    outside: list[str] = field(default_factory=list)   # стрелки, ведущие за фрейм
    dialect: Dialect | None = None                     # диалект доски (dialect.py)
    conn_items: dict[str, list[str]] = field(default_factory=dict)   # стрелка → куски-коннекторы пути
    headless: list[Conn] = field(default_factory=list)   # путь начался со стикера / подписи
    dangling: list[Conn] = field(default_factory=list)   # путь оборвался на стикере / подписи
    frame_w: float = 0.0
    frame_h: float = 0.0
    n_conns: int = 0                      # коннекторов в снимке


def _item(d: dict) -> Item:
    data = d.get("data") or {}
    style = d.get("style") or {}
    geo = d.get("geometry") or {}
    pos = d.get("position") or {}
    html_ = data.get("content") or data.get("title") or ""
    if d.get("type") == "card" and data.get("description"):
        html_ = (html_ or "") + "<p>" + data["description"] + "</p>"
    return Item(
        id=str(d["id"]), type=d.get("type", ""), shape=data.get("shape", "") or "",
        fill=(style.get("fillColor") or "").lower(),
        x=float(pos.get("x", 0)), y=float(pos.get("y", 0)),
        w=float(geo.get("width") or 0), h=float(geo.get("height") or geo.get("width") or 0),
        html=html_, raw=d,
    )


def _body_len(it: Item) -> int:
    """Длина текста без ссылок – чтобы кнопка «ЗАБРАТЬ / https://…длинная…» не считалась телом."""
    return len(" ".join(URL_RE.sub(" ", it.text).split()))


TIME_WORDS_RE = re.compile(r"\d+\s*(сек|мин|час|ч\b)|\bсразу\b|\bчерез\b|\bза\s+\d", re.I)


def _classify(it: Item, dia: Dialect | None = None) -> str:
    """Роль элемента по таблице диалекта доски (dialect.py)."""
    dia = dia or DIALECTS["A"]
    if it.type == "sticky_note":
        return "sticky"
    if it.type == "image":
        return "image"
    if it.type in ("text", "card", "app_card", "document", "embed"):
        return "note"
    if it.type != "shape":
        return "note"
    if it.w >= LANE_MIN_WIDTH and it.h < 1200:
        return "lane"
    if it.shape in ("rhombus", "diamond"):
        # «+ автозвонок за час до эфира» – шаг-звонок в цепочке дня, а не развилка
        return "message" if CALL_RE.search(it.text) else "decision"
    blen = _body_len(it)
    fill = (it.fill or "").lower()
    txt = it.text
    if txt and len(txt) <= 70 and TIMING_LABEL_RE.match(txt):
        return "timing"
    # плашка времени своего цвета («1 минута», «за 30 мин до эфира», «через 2 часа …»)
    if (fill in dia.timing_shapes and txt and len(txt) <= 200 and blen < 120
            and it.shape not in ("circle", "ellipse") and TIME_WORDS_RE.search(txt)):
        return "timing"
    if fill in dia.buttons and txt.strip():
        return "start" if START_RE.match(txt) else "button"
    white = fill in BUTTON_FILLS
    spec = next((sp for sp in dia.message if fill_matches(sp[0], fill) and it.shape in sp[1]), None)
    if spec is not None and txt:
        if not white:
            own = spec[0] != GREEN
            # короткая плашка цвета сообщения под ним – кнопка («ЗАБРАТЬ ПОДАРОК»; в «Пятидневке»
            # кнопки того же голубого, «👉 ЗАНЯТЬ МЕСТО» в две строки – короткий текст без ссылки)
            if it.shape not in ("circle", "ellipse") and (
                    it.h <= 170 or (own and dia.buttons_like_message and blen < 60 and it.h <= 400)):
                return "start" if START_RE.match(txt) else "button"
            return "message"
        if it.shape in ("circle", "ellipse") or (blen >= 40 and it.h >= 250):
            return "message"
    if white and txt and it.shape in MESSAGE_SHAPES and (
            (it.shape == "round_rectangle" and it.h >= 250 and blen >= 40)
            or (it.h > 400 and blen >= 150)):
        # белое сообщение: скруглённое («[Имя], подтвердите…») или большое с текстом
        # (кнопка со ссылкой длинная только за счёт ссылки – её отсекает blen)
        return "message"
    if white and it.h <= 400 and txt.strip():
        return "start" if START_RE.match(txt) else "button"
    return "offbot"


def _attach_point(it: Item, pos: dict | None) -> tuple[float, float]:
    if not pos:
        return it.x, it.y

    def pct(v: Any) -> float:
        try:
            return float(str(v).rstrip("%")) / 100.0
        except ValueError:
            return 0.5
    return it.left + pct(pos.get("x", "50%")) * it.w, it.top + pct(pos.get("y", "50%")) * it.h


def _elbow(p0, p1, horizontal_first: bool) -> list[tuple[float, float]]:
    (x0, y0), (x1, y1) = p0, p1
    if horizontal_first:
        mx = (x0 + x1) / 2
        return [(x0, y0), (mx, y0), (mx, y1), (x1, y1)]
    my = (y0 + y1) / 2
    return [(x0, y0), (x0, my), (x1, my), (x1, y1)]


def seg_dist(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def poly_dist(px: float, py: float, pts: list[tuple[float, float]]) -> float:
    if len(pts) == 1:
        return math.hypot(px - pts[0][0], py - pts[0][1])
    return min(seg_dist(px, py, pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def parse_board(snap: dict) -> Board:
    frame = snap["frame"]
    items = {i.id: i for i in (_item(d) for d in snap.get("items", []))}
    dialect = detect_dialect(list(items.values()))
    for it in items.values():
        it.role = _classify(it, dialect)

    messages = {i.id: Message(i) for i in items.values() if i.role == "message"}
    owner: dict[str, str] = {m: m for m in messages}

    # ── кнопки: колонка под сообщением, сверху вниз, без больших разрывов ──
    buttons = sorted((i for i in items.values() if i.role in ("button", "start")), key=lambda i: i.y)
    taken: set[str] = set()
    for m in sorted(messages.values(), key=lambda m: m.item.y):
        if m.call:
            continue
        bottom = m.item.bottom
        for b in buttons:
            if b.id in taken or b.hoverlap(m.item) < 0.5 or b.top < m.item.top:
                continue
            gap = b.top - bottom
            if gap > 700:
                continue
            if gap > 300 and any(o is not m and o.item.top < b.top and o.item.bottom > bottom
                                 and o.item.hoverlap(b) >= 0.5 for o in messages.values()):
                # дальше 300 – только если между сообщением и кнопкой ничего нет
                continue
            # кнопка не должна быть ближе к другому сообщению ниже неё
            m.buttons.append(b)
            taken.add(b.id)
            owner[b.id] = m.id
            bottom = max(bottom, b.bottom)
    start_buttons = [b for b in buttons if b.role == "start"]

    # ── картинки: над сообщением в той же колонке ──
    # ближняя к сообщению – его картинка; ещё крупные картинки прямо над ней – альбом
    # («Посмотрите на скриншоты выше» – четыре скриншота над текстом)
    for img in sorted((i for i in items.values() if i.role == "image"), key=lambda i: -i.bottom):
        best, gap_best = None, 1e9
        for m in messages.values():
            if m.call or img.hoverlap(m.item) < 0.5:
                continue
            gap = m.item.top - img.bottom
            if -200 <= gap <= 1500 and gap < gap_best and m.image is None:
                best, gap_best = m, gap
        if best is not None:
            best.image = img
            owner[img.id] = best.id
    for img in sorted((i for i in items.values() if i.role == "image" and i.id not in owner), key=lambda i: -i.bottom):
        if img.w < 250 or img.h < 250:
            continue
        for m in messages.values():
            if m.image is None or m.call:
                continue
            # альбом: крупная картинка над текстом (рядом с первой или выше неё), в полосе колонки
            if (m.item.left - 300 <= img.x <= m.item.right + 300 and img.bottom <= m.item.top + 150
                    and m.item.top - img.bottom <= 1500):
                m.album.append(img)
                owner[img.id] = m.id
                break

    # ── стрелки: сквозь стикеры («ИГРАТЬ» → стикер «СРАЗУ» → сообщение) ──
    raw_conns = snap.get("connectors", [])
    conns: list[Conn] = []
    outside: list[str] = []
    pieces: list[Conn] = []
    for c in raw_conns:
        s = str((c.get("startItem") or {}).get("id") or "") or None
        e = str((c.get("endItem") or {}).get("id") or "") or None
        cap = " ".join(plain(x.get("content")) for x in c.get("captions") or []).strip()
        if (s and s not in items) or (e and e not in items) or not s or not e:
            outside.append(str(c["id"]))
            continue
        si, ei = items[s], items[e]
        p0 = _attach_point(si, (c.get("startItem") or {}).get("position"))
        p1 = _attach_point(ei, (c.get("endItem") or {}).get("position"))
        sx = str(((c.get("startItem") or {}).get("position") or {}).get("x", "50%"))
        horiz = sx in ("0%", "100%", "0.0%", "100.0%")
        pts = _elbow(p0, p1, horiz) if c.get("shape", "elbowed") == "elbowed" else [p0, p1]
        conn = Conn(id=str(c["id"]), start=s, end=e, caption=cap, points=pts)
        pieces.append(conn)
    # стрелка может идти через несколько стикеров и подписей подряд: «конец дня» →
    # стикер «2 день» → подпись «в 9:00 по МСК» → кружок. Собираем сквозной путь.
    by_start: dict[str, list[Conn]] = {}
    for c in pieces:
        by_start.setdefault(c.start, []).append(c)
    via_stickies: set[str] = set()
    headless: list[Conn] = []            # путь начинается со стикера / подписи, а не с сообщения
    dangling: list[Conn] = []            # путь обрывается на стикере / подписи
    conn_items: dict[str, list[str]] = {}

    def walk(first: Conn) -> None:
        stack = [(first, [], [], [first.id], first.caption, first.points)]
        while stack:
            c, sts, via, ids, cap, pts = stack.pop()
            if items[c.end].role not in VIA_ROLES:
                conn = Conn(id=first.id, start=first.start, end=c.end, caption=cap.strip(), points=pts,
                            stickies=list(sts), via=list(via))
                (headless if items[first.start].role in VIA_ROLES else conns).append(conn)
                conn_items.setdefault(first.id, []).extend(ids)
                via_stickies.update(via)
                continue
            nxt = [x for x in by_start.get(c.end, []) if x.id not in ids]
            if not nxt:
                conn_items.setdefault("dangling:" + first.id, []).extend(ids)
                dangling.append(Conn(id=first.id, start=first.start, end=c.end, caption=cap.strip(),
                                     points=pts, stickies=list(sts) + [items[c.end]], via=list(via) + [c.end]))
                continue
            for x in nxt:
                stack.append((x, sts + [items[c.end]], via + [c.end], ids + [x.id],
                              cap + " " + x.caption, pts + x.points))

    for c in pieces:
        if items[c.start].role in VIA_ROLES:
            # цепочка без сообщения в начале – только если в этот стикер ничего не входит
            if not any(x.end == c.start for x in pieces):
                walk(c)
            continue
        walk(c)

    # ── стикеры: ближайшая стрелка между сообщениями и ближайшее сообщение ──
    # Решение «к чему стикер» принимает build.py: понятое время/условие – к стрелке,
    # непонятое – заметкой к сообщению.
    def is_bot(iid: str | None) -> bool:
        return bool(iid) and (iid in owner or items[iid].role == "start")

    bot_conns = [c for c in conns if is_bot(c.start) and is_bot(c.end)]
    near: dict[str, dict] = {}
    for st in (i for i in items.values() if i.role in VIA_ROLES and i.id not in via_stickies):
        best_c, dc = None, 1e18
        for c in bot_conns:
            d = poly_dist(st.x, st.y, c.points)
            if d < dc:
                best_c, dc = c, d
        best_m, dm = None, 1e18
        for m in messages.values():
            d = min([m.item.box_dist(st.x, st.y)] + [b.box_dist(st.x, st.y) for b in m.buttons])
            if d < dm:
                best_m, dm = m, d
        near[st.id] = {"conn": best_c, "conn_dist": dc, "msg": best_m, "msg_dist": dm,
                       "reach": max(700.0, 1.2 * max(st.w, st.h))}

    lanes = [i for i in items.values() if i.role == "lane"]
    others = [i for i in items.values()
              if i.role in ("offbot", "decision", "note") or (i.role in ("button", "image") and i.id not in owner)]
    return Board(
        board_id=snap.get("board_id", ""), frame_id=str(frame["id"]),
        frame_title=(frame.get("data") or {}).get("title") or "",
        items=items, messages=messages, owner=owner, conns=conns,
        start_buttons=start_buttons, near=near, lanes=lanes, others=others,
        outside=outside, dialect=dialect, conn_items=conn_items,
        frame_w=float((frame.get("geometry") or {}).get("width") or 0),
        frame_h=float((frame.get("geometry") or {}).get("height") or 0),
        headless=headless, dangling=dangling, n_conns=len(raw_conns),
    )
