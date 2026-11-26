"""Доска Miro → граф Студии.

Правила переноса – те же, что разработчик применяет руками (bot-engine:
`.claude/skills/new-launch/SKILL.md`, `docs/new_launch_playbook.md`):

  * первая колонка с кнопкой «СТАРТ / НАЧАТЬ» – описание бота, не шаг; её стрелка ведёт
    в первое сообщение;
  * круг – кружок (`video_note`), его текст – сценарий: в шаг не идёт (в MAX человек
    услышал бы и тут же прочитал), сохраняется заметкой на карточке, ролик нужно загрузить;
  * сообщение с кнопкой «ОТПРАВИТЬ КОНТАКТ» – запрос телефона, «введите e-mail» – запрос
    почты, сообщение с кнопками-ответами без ссылок – вопрос опроса;
  * порядок и развилки – по стрелкам, время – со стикеров у стрелок (от предыдущего
    сообщения); несколько стрелок из одного сообщения – одна основная (`done`, ближайшая
    по времени), остальные параллельные (`also_fire`);
  * ссылка на канал и ВИП – `{channel.link}` / `{vip_link}`; длинное тире – «–»;
    «Имя,» в начале строки – `{Имя}`.

Каждый шаг помнит свои элементы Miro и «сомнения» – места, где импорт не уверен.
"""
from __future__ import annotations

import html as _html
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ..ids import STUDIO_NS, edge_id
from ..model.funnel import (
    Branch, BranchRouter, BranchWindow, Button, Edge, EdgeKind, Funnel, Lane, Layout, MiroDoubt,
    MiroSource, MiroStep, Note, Position, SourceInfo, Step,
)
from . import rows as _rows
from .board import Board, Item, Message, parse_board
from .client import FrameRef
from .html import html_plain, links, to_tg_html
from .stickers import RuleParser, StickyMeaning, StickyParser

CONTACT_BTN_RE = re.compile(r"контакт|телефон|номер", re.I)
EMAIL_ASK_RE = re.compile(r"(введите|укажите|пришлите|напишите|оставьте|отправьте)[^.?!]{0,40}(e-?mail|почт|мейл|мэйл|имейл)", re.I)
DONE_BTN_RE = re.compile(r"^\W*(✅\s*)?(готово|дальше|далее)\b", re.I)
MULTI_RE = re.compile(r"несколько", re.I)
CHANNEL_URL_RE = re.compile(r"^https?://(t\.me|telegram\.me)/(\+|joinchat/)", re.I)
VIP_LABEL_RE = re.compile(r"\b(вип|vip)", re.I)
LINK_WORD_RE = re.compile(r"\((ссылка|link)\)", re.I)
BRACKET_LINE_RE = re.compile(r"^\s*(<[^>]+>)*\s*\[[^\]\n]{2,60}\]\s*(<[^>]+>)*\s*$")
LETTER_RE = re.compile(r"^\s*тема(\s+письма)?\s*:", re.I)
ACTION_BTN_RE = re.compile(r"^\W*([сc]мотреть|перейти|забрать|получить|подключ|занять|узнать|открыть|скачать|"
                           r"присоедин|активир)", re.I)
PLATFORM_STICKY_RE = re.compile(r"^\W*(для\s+|канал\s+)?(ма[хx]|max|tg|тг|телеграм\w*)\W*$", re.I)
# записка копирайтера разработчику (так же её узнавал человек – extract_avtoveb_miro.py INSTR)
INSTR_RE = re.compile(r"^\s*(?:https?://\S+\s*)?(правка\b|заменить|добавить|добавляем|поменять|убрать|убираем|"
                      r"перенести|меняем|по\s+кнопке|нужно|здесь\s+должна|время\s+отправки)", re.I)
ENTRY_SECTION_RE = re.compile(r"вход|регистрац|\bрег", re.I)
AIR_BTN_RE = re.compile(r"подключ|занять\s+мест|смотреть\s+эфир|присоедин|на\s+эфир|в\s+эфир", re.I)
PAID_TEXT_RE = re.compile(r"оплат\w*\s+прош|спасибо\s+за\s+(оплат|покупк)|вы\s+оплатил", re.I)
PAID_RE = re.compile(r"оплат\w*\s+вип|после\s+оплат|оплатил\w*\s+(вип|vip)", re.I)
# масштаб «доска → холст»: колонка Miro ~1150 единиц, карточка Студии – 320 px
LAYOUT_SCALE = 0.32


def miro_step_id(board_id: str, item_id: str) -> uuid.UUID:
    """id шага из элемента Miro: повторный импорт попадает в ту же карточку."""
    return uuid.uuid5(STUDIO_NS, f"miro:{board_id}:{item_id}")


def _cut(s: str, n: int = 60) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class _Arrow:
    src: str                       # сообщение-источник
    dst: str                       # сообщение-цель
    parts: set[str] = field(default_factory=set)     # «body» или id кнопки источника
    stickies: list[Item] = field(default_factory=list)
    captions: list[str] = field(default_factory=list)
    conn_ids: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    funnel: Funnel
    board: Board
    stats: dict[str, Any]
    images: dict[str, str] = field(default_factory=dict)     # item_id картинки → ref

    def doubts(self) -> list[dict]:
        """Все сомнения: [{step_key, step_id, level, code, text, item_id}]."""
        out = []
        for s in self.funnel.steps:
            for d in (s.miro.doubts if s.miro else []):
                out.append({"step_key": s.key, "step_id": str(s.id), **d.model_dump()})
        for d in (self.funnel.miro.doubts if self.funnel.miro else []):
            out.append({"step_key": None, "step_id": None, **d.model_dump()})
        return out


def _merge_meaning(ms: list[StickyMeaning]) -> StickyMeaning:
    out = StickyMeaning(text=" / ".join(m.text for m in ms))
    for m in ms:
        if out.delay_seconds is None and m.delay_seconds is not None:
            out.delay_seconds = m.delay_seconds
        out.event = out.event or m.event
        out.conditions.update(m.conditions)
        out.skip_if_known = out.skip_if_known or m.skip_if_known
        out.unsupported += m.unsupported
        out.confidence = min(out.confidence, m.confidence)
    if not ms:
        out.confidence = 1.0
    return out


def _button_parts(b: Item) -> tuple[str, str | None, list[str]]:
    """Кнопка доски → (подпись, ссылка, заметки). Ссылка – та, что видна текстом; спрятанная
    в пустой ссылке (старый адрес из копии прошлого запуска) – не берётся."""
    notes: list[str] = []
    ls = links(b.html)
    url = None
    hidden = []
    for href, vis in ls:
        if not href:
            continue
        if vis.strip():
            url = url or href
        else:
            hidden.append(href)
    if url is None and hidden:
        url = hidden[-1]
        notes.append(f"ссылка кнопки спрятана в пустом тексте – взята {hidden[-1]}")
    elif hidden:
        notes.append("в кнопке есть скрытая вторая ссылка " + ", ".join(hidden) + " – не взята")
    label = b.text
    for href, _ in ls:
        label = label.replace(href, " ")
    label = LINK_WORD_RE.sub(" ", label)
    label = " ".join(label.split())
    if LINK_WORD_RE.search(b.text) and not url:
        notes.append("вместо ссылки на доске написано «(ссылка)»")
    return label, url, notes


class _Builder:
    def __init__(self, snap: dict, *, launch: str, funnel_id: str, parser: StickyParser,
                 fetch_image: Callable[[Item], str] | None, link: str,
                 launch_cfg: dict | None):
        self.board = parse_board(snap)
        self.launch, self.funnel_id = launch, funnel_id
        self.parser = parser
        self.fetch_image = fetch_image
        self.link = link or FrameRef(self.board.board_id, self.board.frame_id).item_url()
        self.launch_cfg = launch_cfg or {}
        self.fdoubts: list[MiroDoubt] = []
        self.sdoubts: dict[str, list[MiroDoubt]] = defaultdict(list)    # message id → сомнения
        self.meaning_cache: dict[str, StickyMeaning] = {}
        self.images: dict[str, str] = {}
        # фрейм-«расписание» (полосы дней, подписи-тайминги) – см. rows.py
        self.tl = _rows.detect(self.board)
        self.day_conns: list = []          # стрелки внутри дней: порядок и время там – по колонкам
        self.consumed: dict[str, str] = {}  # элемент доски → как учтён (не шагом и не заметкой)
        self.as_note: dict[str, str] = {}   # сообщение, ставшее заметкой → почему
        self.air_btns = 0
        self.edge_stickies: set[str] = set()   # стикеры и подписи на стрелках, ставших переходами
        self.accounting: dict[str, Any] = {}
        ws = sorted(b.webinar for b in self.tl.branches) if self.tl is not None else []
        names = ["DAY", "EVE"] if len(ws) == 2 else [f"B{i + 1}" for i in range(len(ws))]
        self.branch_id: dict[int, str] = dict(zip(ws, names))

    # ── мелочи ──
    def doubt(self, mid: str | None, code: str, text: str, item_id: str | None = None,
              level: str = "check") -> None:
        d = MiroDoubt(code=code, text=text, item_id=item_id or mid, level=level)
        if mid is None:
            if all(x.text != d.text for x in self.fdoubts):
                self.fdoubts.append(d)
        elif all(x.text != d.text for x in self.sdoubts[mid]):
            self.sdoubts[mid].append(d)

    def meaning(self, st: Item, context: str) -> StickyMeaning:
        if st.id not in self.meaning_cache:
            self.meaning_cache[st.id] = self.parser.parse(st.text, context)
        return self.meaning_cache[st.id]

    # ── стикеры: к стрелке, к сообщению или «в стороне» ──
    def _place_stickies(self) -> None:
        b = self.board
        self.loose: list[Item] = []
        self.inferred: list[tuple[str, str, Item]] = []      # стрелки, которых нет, но есть стикер
        for st_id, nr in b.near.items():
            st = b.items[st_id]
            if self.tl is not None and st_id in self.tl.used:
                continue
            c, m = nr["conn"], nr["msg"]
            ctx = "стикер у стрелки" if c is not None and nr["conn_dist"] <= nr["reach"] else "стикер на доске"
            mm = self.meaning(st, ctx)
            to_conn = c is not None and nr["conn_dist"] <= nr["reach"] and nr["conn_dist"] <= nr["msg_dist"] + 150
            if mm.empty or mm.skip_if_known:
                if m is not None and nr["msg_dist"] <= 1500:
                    m.stickies.append(st)
                    b.owner.setdefault(st.id, m.id)
                else:
                    self.loose.append(st)
                continue
            if to_conn:
                c.stickies.append(st)
            elif m is not None and nr["msg_dist"] <= 300:
                m.stickies.append(st)
                b.owner.setdefault(st.id, m.id)
            elif c is not None and nr["conn_dist"] <= nr["reach"]:
                c.stickies.append(st)
            else:
                pair = self._between(st)
                if pair and mm.delay_seconds is not None and not mm.unsupported and mm.event is None:
                    self.inferred.append((pair[0], pair[1], st))
                else:
                    self.loose.append(st)

    def _between(self, st: Item) -> tuple[str, str] | None:
        """Стикер со временем, у которого нет стрелки: слева – сообщение на его высоте,
        справа – ближайшее сообщение. Так на досках пропускали стрелку «ВИП-дожим → опрос»."""
        msgs = self.board.messages
        left = [m for m in msgs.values() if m.item.right <= st.x + 50
                and min([m.item.top] + [x.top for x in m.buttons]) - 200 <= st.y
                <= max([m.item.bottom] + [x.bottom for x in m.buttons]) + 200
                and st.x - m.item.right < 2500]
        right = [m for m in msgs.values() if m.item.left >= st.x - 50 and m.item.left - st.x < 2500]
        if not left or not right:
            return None
        a = min(left, key=lambda m: st.x - m.item.right)
        bb = min(right, key=lambda m: min([m.item.box_dist(st.x, st.y)] + [x.box_dist(st.x, st.y) for x in m.buttons]))
        return (a.id, bb.id) if a.id != bb.id else None

    # ── основной проход ──
    def run(self) -> ImportResult:
        b = self.board
        msgs = b.messages
        owner = b.owner
        self._place_stickies()

        if b.dialect is not None:
            self.doubt(None, "dialect", f"доска нарисована в диалекте {b.dialect.code} {b.dialect.title}: "
                       f"{b.dialect.notes}", level="info")
        # 1. описание бота и вход
        desc_ids = {owner[s.id] for s in b.start_buttons if s.id in owner}
        start_items = {s.id for s in b.start_buttons} | desc_ids
        bot_description = ""
        for d in desc_ids:
            bot_description = to_tg_html(msgs[d].item.html)
        entry_targets: list[str] = []
        start_of: dict[str, str] = {}           # цель стрелки «СТАРТ» → кнопка «СТАРТ»
        arrows: dict[tuple[str, str], _Arrow] = {}
        offbot_in: Counter = Counter()
        bot_in: Counter = Counter()
        for c in b.conns:
            s_own = owner.get(c.start) if c.start not in start_items else "__start__"
            e_own = owner.get(c.end)
            if self.tl is not None and (self._in_day(s_own) or self._in_day(e_own)):
                # в днях порядок – по колонкам, время – по подписям; стрелка лишь подтверждает
                self.day_conns.append(c)
                continue
            if c.end in start_items or e_own in desc_ids:
                continue
            if e_own is None:
                continue
            if s_own is None:
                offbot_in[e_own] += 1
                continue
            if s_own == "__start__":
                if e_own not in entry_targets:
                    entry_targets.append(e_own)
                    start_of[e_own] = c.start
                continue
            if s_own == e_own:
                continue
            a = arrows.setdefault((s_own, e_own), _Arrow(s_own, e_own))
            src_item = b.items[c.start]
            a.parts.add(c.start if src_item.role == "button" else "body")
            a.stickies += c.stickies
            if c.caption:
                a.captions.append(c.caption)
            a.conn_ids.append(c.id)
            bot_in[e_own] += 1
        for (s_m, e_m, st) in self.inferred:
            if (s_m, e_m) in arrows or s_m in desc_ids or e_m in desc_ids:
                continue
            a = arrows.setdefault((s_m, e_m), _Arrow(s_m, e_m))
            a.parts.add("body")
            a.stickies.append(st)
            bot_in[e_m] += 1
            self.doubt(e_m, "edge_inferred", f"стрелки на доске нет – проведена по стикеру «{st.text}» "
                       f"от «{_cut(msgs[s_m].item.text, 30)}»", st.id)
        for cid in b.outside:
            self.doubt(None, "edge_outside", f"стрелка {cid} ведёт за пределы фрейма – не учтена", cid)
        for m, msg in msgs.items():
            why = self._paid_note(m)
            if why and m not in desc_ids:
                self.as_note[m] = why
        if self.tl is not None:
            self._entry_columns(arrows, entry_targets, desc_ids, bot_in)

        # 2. какие сообщения – шаги
        entry_targets.sort(key=lambda m: msgs[m].item.x)
        starts = {start_of[m] for m in entry_targets}
        if len(starts) > 1:
            # на фрейме несколько ботов (регистрация, мини-курс, посевы): воронка – бот регистрации
            # (раздел «вход в воронку» / «бот регистраций»; разделов нет – самый левый «СТАРТ», как раньше)
            def start_key(sid: str) -> tuple:
                sec = self._section(b.items[sid])
                title = sec.text if sec is not None else ""
                return (0 if ENTRY_SECTION_RE.search(title) else 1, b.items[sid].x)
            main = min(starts, key=start_key)
            others = [m for m in entry_targets if start_of[m] != main]
            entry_targets = [m for m in entry_targets if start_of[m] == main]
            self.doubt(None, "many_bots", f"на фрейме {len(starts)} кнопки «СТАРТ» – взят бот «"
                       f"{_cut(msgs[entry_targets[0]].item.text, 40)}»; входы других ботов ("
                       + ", ".join(f"«{_cut(msgs[m].item.text, 30)}»" for m in others) + ") – не в этой воронке",
                       level="info")
        out_of: dict[str, list[str]] = defaultdict(list)
        for (s, e) in arrows:
            out_of[s].append(e)
        if entry_targets:
            entry = entry_targets[0]
        else:
            cands = [m for m in msgs if m not in desc_ids and not bot_in[m] and not offbot_in[m]]
            if not cands:
                cands = [m for m in msgs if m not in desc_ids]
            entry = min(cands, key=lambda m: (msgs[m].item.x, msgs[m].item.y)) if cands else None
            if entry:
                self.doubt(None, "no_start", "на доске нет кнопки «СТАРТ» со стрелкой – первым "
                           f"взято самое левое сообщение «{_cut(msgs[entry].item.text, 40)}»", entry)
        # разделы доски (цветные полосы-заголовки): берём только раздел, где вход
        entry_sec = self._section(msgs[entry].item) if entry else None
        foreign: dict[str, list[str]] = defaultdict(list)
        for m, msg in msgs.items():
            sec = self._section(msg.item)
            if entry and m not in desc_ids and sec is not None and sec is not entry_sec:
                foreign[sec.text or "без названия"].append(m)
        skip_foreign = {m for lst in foreign.values() for m in lst}
        reach: set[str] = set()
        stack = [entry] + entry_targets[1:] if entry else []
        stack = [m for m in stack if m not in self.as_note]
        while stack:
            m = stack.pop()
            if m in reach:
                continue
            reach.add(m)
            for e in out_of.get(m, []):
                # в чужой раздел идём только прямой стрелкой от сообщения (опрос → подарок),
                # не от кнопки: кнопка «ИГРАТЬ» ведёт в механику игры
                if e in skip_foreign and (m in skip_foreign or "body" not in arrows[(m, e)].parts):
                    continue
                stack.append(e)
        skip_foreign -= reach
        if self.tl is not None:
            # сообщения полос дней – шаги по колонкам (дни армируются из входа, см. ниже)
            reach |= {m for m in msgs if self._in_day(m)}
        for title in list(foreign):
            foreign[title] = [m for m in foreign[title] if m not in reach]
            lst = foreign[title]
            if not lst:
                continue
            self.doubt(None, "section", f"раздел «{_cut(title, 40)}» ({len(lst)} сообщ.) не перенесён – он "
                       f"не в разделе входа («{_cut(entry_sec.text if entry_sec else '', 40)}»); это механика "
                       "другого модуля (игра, страницы после оплаты). Нужен – перенесите вручную", lst[0], level="info")
        steps_ids: list[str] = [m for m in msgs if m in reach and m not in self.as_note]
        for m, msg in msgs.items():
            if m in reach or m in desc_ids or m in skip_foreign:
                continue
            if m in self.as_note:
                continue
            t = msg.item.text
            if offbot_in[m] and not bot_in[m] or LETTER_RE.match(t):
                self.doubt(None, "not_bot", f"«{_cut(t, 50)}» – не сообщение бота (письмо или "
                           "страница сайта), не взято – стоит на холсте заметкой", m, level="info")
                self.as_note[m] = "не сообщение бота: письмо или страница сайта"
                continue
            steps_ids.append(m)
        # сообщения, куда ведут только стрелки невзятых, – тоже вне цепочки
        steps_set = set(steps_ids)
        for m in steps_ids:
            if m not in reach:
                self.doubt(m, "orphan", "в сообщение не ведёт ни одна стрелка от входа – его "
                           "шлёт внешний модуль? Проверьте, нужно ли оно в воронке")

        # 3. типы шагов и кнопки
        types: dict[str, str] = {}
        is_q: dict[str, bool] = {}
        blabel: dict[str, tuple[str, str | None, list[str]]] = {}
        arrow_btns = {p for a in arrows.values() for p in a.parts if p != "body"}
        for m in steps_ids:
            msg = msgs[m]
            for bt in msg.buttons:
                blabel[bt.id] = _button_parts(bt)
            no_url = [bt for bt in msg.buttons if not blabel[bt.id][1]]
            if msg.call:
                types[m] = "call"
            elif msg.circle:
                types[m] = "video_note"
            elif any(CONTACT_BTN_RE.search(blabel[bt.id][0]) for bt in no_url):
                types[m] = "phone_request"
            elif EMAIL_ASK_RE.search(msg.item.text):
                types[m] = "email_request"
            elif msg.image is not None:
                types[m] = "photo"
            else:
                types[m] = "text"
            answers = [bt for bt in no_url if bt.id not in arrow_btns and not ACTION_BTN_RE.match(blabel[bt.id][0])]
            is_q[m] = (types[m] == "text" and len(answers) >= 2 and len(no_url) == len(msg.buttons))

        # 4. стрелки → виды
        edges: list[dict] = []           # {src, dst, kind, delay, meaning, arrow}
        conds: dict[str, dict] = defaultdict(dict)
        claim: dict[str, tuple[str, str]] = {}           # шаг → (подпись, id кнопки)
        back_buttons: set[str] = set()
        chain: dict[str, list[dict]] = defaultdict(list)
        # кнопки без ссылки, ведущие в два и больше вопросов, – «вернуть в опрос»
        btn_targets: dict[str, set[str]] = defaultdict(set)
        for a in arrows.values():
            for p in a.parts:
                if p != "body":
                    btn_targets[p].add(a.dst)
        for bid, ts in btn_targets.items():
            if len([t for t in ts if is_q.get(t)]) >= 2:
                back_buttons.add(bid)

        nudges = {owner[bid] for bid in back_buttons if bid in owner}
        # «если НЕ нажал» у одной стрелки → стрелка без стикера от кнопки-ссылки = «если нажал»
        not_clicked_src = {s for (s, e), a in arrows.items()
                           if any(self.meaning(st, "").event == "not_clicked"
                                  or self.meaning(st, "").conditions.get("not_paid") for st in a.stickies)}
        for (s, e), a in sorted(arrows.items(), key=lambda kv: (msgs[kv[0][0]].item.x, msgs[kv[0][1]].item.x)):
            if s not in steps_set or e not in steps_set:
                continue
            if e in nudges and not is_q.get(e):
                self.doubt(e, "nudge", f"дожим по стрелке из «{_cut(msgs[s].item.text, 30)}» («через N минут, "
                           "если остановился / не нажал») – движок так не умеет, в прошлых запусках его слал "
                           "game-модуль; стрелка не перенесена")
                continue
            if (s in not_clicked_src and not a.stickies and "body" not in a.parts
                    and all(blabel.get(p, ("", None, []))[1] for p in a.parts)):
                self.doubt(e, "on_click", f"стрелка от кнопки-ссылки «{blabel[next(iter(a.parts))][0]}» без "
                           "стикера – «если нажал»: нажатие на ссылку движок не отслеживает, стрелка не перенесена")
                continue
            self.edge_stickies.update(st.id for st in a.stickies)
            ctx = f"стрелка от «{_cut(msgs[s].item.text, 40)}» к «{_cut(msgs[e].item.text, 40)}»"
            mean = _merge_meaning([self.meaning(st, ctx) for st in a.stickies])
            ts, te = types[s], types[e]
            btn_parts = [p for p in a.parts if p != "body"]
            no_url_btns = [p for p in btn_parts if not blabel.get(p, ("", None, []))[1]]
            if any(p in back_buttons for p in btn_parts):
                self.doubt(s, "back_to_survey", "кнопка «" + blabel[btn_parts[0]][0] + "» ведёт обратно в опрос "
                           "(на вопрос, где человек остановился) – это callback game-модуля "
                           "(как `gm:svq` в прошлых запусках), стрелки не перенесены")
                continue
            for st in a.stickies:
                mm = self.meaning(st, ctx)
                if mm.confidence < 1.0:
                    what = "не поняла стикер" if mm.empty else "не всё поняла в стикере"
                    self.doubt(e, "sticker_unclear", f"{what}: «{st.text}»", st.id)
            for cap in a.captions:
                self.doubt(e, "caption", f"подпись стрелки «{_cut(cap, 50)}» – проверьте", level="info")
            if (no_url_btns and s not in claim and not is_q[s] and ts not in ("phone_request", "email_request")
                    and "body" not in a.parts):
                # стрелка от кнопки без ссылки – по нажатию; «если не нажал» тут не про неё
                claim[s] = (blabel[no_url_btns[0]][0], no_url_btns[0])
                edges.append({"src": s, "dst": e, "kind": "callback", "delay": None, "mean": mean, "arrow": a})
                if mean.delay_seconds or mean.event or mean.conditions:
                    self.doubt(e, "claim_sticker", f"стрелка от кнопки «{claim[s][0]}» подписана «{mean.text}» – "
                               "по нажатию сообщение уходит сразу, стикер не учтён")
                continue
            if mean.event in ("stopped", "not_clicked") or mean.unsupported:
                why = "; ".join(mean.unsupported) or f"«{mean.text}» – у движка нет такого условия"
                self.doubt(e, "unsupported", f"стрелка из «{_cut(msgs[s].item.text, 30)}»: {why}. "
                           "Не перенесена – дожим посреди опроса в прошлых запусках слал game-модуль")
                continue
            if ts == "phone_request":
                # от кнопки «ОТПРАВИТЬ КОНТАКТ» – «дал номер», от самого сообщения – «не дал»;
                # стикеры раскладываются по своему исходу
                ms = [self.meaning(st, ctx) for st in a.stickies]
                c_m = [x for x in ms if x.event == "contact"]
                s_m = [x for x in ms if x.event == "no_contact"]
                rest = [x for x in ms if x.event not in ("contact", "no_contact")]
                if no_url_btns or c_m:
                    cm = _merge_meaning(c_m or rest)
                    edges.append({"src": s, "dst": e, "kind": "contact", "delay": cm.delay_seconds, "mean": cm, "arrow": a})
                if "body" in a.parts or s_m:
                    sm = _merge_meaning(s_m or ([] if c_m else rest))
                    edges.append({"src": s, "dst": e, "kind": "skip", "delay": sm.delay_seconds, "mean": sm, "arrow": a})
                self._conds(e, mean, conds)
                continue
            if ts == "email_request":
                edges.append({"src": s, "dst": e, "kind": "email", "delay": mean.delay_seconds or 0, "mean": mean, "arrow": a})
                continue
            if is_q[s]:
                edges.append({"src": s, "dst": e, "kind": "survey_next", "delay": None, "mean": mean, "arrow": a})
                continue
            if no_url_btns and s not in claim:
                claim[s] = (blabel[no_url_btns[0]][0], no_url_btns[0])
                edges.append({"src": s, "dst": e, "kind": "callback", "delay": None, "mean": mean, "arrow": a})
                if mean.delay_seconds:
                    self.doubt(e, "claim_delay", f"стрелка от кнопки «{claim[s][0]}» подписана «{mean.text}» "
                               "– по нажатию сообщение уходит сразу", level="info")
                continue
            self._conds(e, mean, conds)
            if mean.delay_seconds is None and not a.stickies:
                self.doubt(e, "edge_no_caption", f"стрелка из «{_cut(msgs[s].item.text, 30)}» без стикера "
                           "со временем – поставлено «сразу»")
            elif mean.delay_seconds is None:
                self.doubt(e, "time_unclear", f"не поняла время: «{mean.text}» – поставлено «сразу»")
            delay = mean.delay_seconds or 0
            if self.tl is not None and e in self.tl.timer:
                if delay != self.tl.timer[e]:
                    self.doubt(e, "timer_note", f"время по записке у сообщения: через "
                               f"{_rows._dur(self.tl.timer[e])} (на стрелке – «{mean.text or 'без стикера'}»)",
                               level="info")
                delay = self.tl.timer[e]
            chain[s].append({"src": s, "dst": e, "delay": delay, "mean": mean, "arrow": a})

        # одна стрелка – «дальше по времени»; несколько из одного сообщения – все параллельные
        # (каждая отсчитывает своё время от этого сообщения – так делали и руками)
        for s, lst in chain.items():
            lst.sort(key=lambda d: (d["delay"], msgs[d["dst"]].item.x))
            for d in lst:
                d["kind"] = "done" if len(lst) == 1 else "also_fire"
                edges.append(d)
        if self.tl is not None:
            self._day_edges(entry, steps_set, edges, conds)

        # 5. условия шага: стикеры у сообщения + правило ВИП
        skip_known: dict[str, str] = {}
        for m in steps_ids:
            for st in msgs[m].stickies:
                mm = self.meaning(st, f"стикер у сообщения «{_cut(msgs[m].item.text, 40)}»")
                if mm.skip_if_known:
                    skip_known[m] = mm.skip_if_known
                if mm.conditions:
                    conds[m].update(mm.conditions)
                if mm.empty and INSTR_RE.match(st.text):
                    self.doubt(m, "note_instruction", f"записка разработчику у сообщения: «{_cut(st.text, 120)}» – "
                               "стоит заметкой на карточке, проверьте, учтена ли", st.id)
                elif mm.empty:
                    self.doubt(m, "note_sticker", f"стикер у сообщения: «{_cut(st.text, 120)}» – "
                               "сохранён заметкой", st.id, level="info")
                elif mm.confidence < 1:
                    self.doubt(m, "sticker_unclear", f"не всё поняла в стикере: «{st.text}»", st.id)

        # ссылки: канал и ВИП
        channel_url, vip_url = self._links(steps_ids, blabel)
        for m in steps_ids:
            if "not_paid" in conds[m] or "paid" in conds[m]:
                continue
            msg = msgs[m]
            btn_vip = any(VIP_LABEL_RE.search(blabel[bt.id][0]) or (vip_url and blabel[bt.id][1] == vip_url)
                          for bt in msg.buttons)
            txt_vip = bool(re.search(r"(vip|вип)[-\s]?(билет|условия|доступ)", msg.item.text, re.I))
            if btn_vip or txt_vip:
                conds[m]["not_paid"] = True
                self.doubt(m, "rule_vip", "ВИП-сообщение не шлём купившим ВИП (условие «не оплатил ВИП» "
                           "– по правилу, на доске его может не быть)", level="info")

        # 6. шаги
        order = self._order(entry, steps_ids, edges)
        if self.tl is not None:
            # вход – по стрелкам, дни – по колонкам своей полосы (время по подписям)
            day_order = [m for r in self.tl.day_rows() for m in r.msgs if m in steps_set]
            order = [m for m in order if m not in set(day_order)] + day_order
        keys = self._keys(order, types, is_q) if self.tl is None else self._row_keys(order, types, is_q)
        sid = {m: miro_step_id(b.board_id, m) for m in steps_ids}
        steps: list[Step] = []
        notes: list[Note] = []
        qn = 0
        q_final = {}
        for d in edges:
            if d["kind"] == "survey_next":
                q_final[d["src"]] = not is_q.get(d["dst"], False)
        for m in order:
            msg = msgs[m]
            t = types[m]
            step: dict[str, Any] = {"id": sid[m], "key": keys[m], "type": t}
            content_btns: list[Button] = []
            done_btn = None
            for bt in msg.buttons:
                label, url, bnotes = blabel[bt.id]
                for n in bnotes:
                    self.doubt(m, "button_link", f"кнопка «{label}»: {n}", bt.id, level="info")
                if t == "phone_request" and not url and CONTACT_BTN_RE.search(label):
                    step.setdefault("contact", {})["contact_button"] = label
                    continue
                if claim.get(m, ("", ""))[1] == bt.id:
                    step.setdefault("content", {}).setdefault("tg", {})["claim_button_text"] = label
                    continue
                if is_q[m] and DONE_BTN_RE.match(label):
                    done_btn = label
                    continue
                if bt.id in back_buttons:
                    content_btns.append(Button(text=label))
                    continue
                if not url and not is_q[m] and VIP_LABEL_RE.search(label):
                    url = "{vip_link}"
                    self.doubt(m, "vip_no_url", f"у кнопки «{label}» на доске нет ссылки – поставлена "
                               "{vip_link} (страница ВИП запуска)", bt.id, level="info")
                if not url and self.tl is not None and self._in_day(m) and AIR_BTN_RE.search(label):
                    # «Подключиться к эфиру» без ссылки в дне – ссылка на эфир (у человека – зал
                    # Bizon этого дня; в Студии – {stream_url}, зал задаётся в запуске)
                    url = "{stream_url}"
                    self.air_btns += 1
                if url:
                    url = self._placeholder(url, channel_url, vip_url)
                    content_btns.append(Button(text=label, url=url))
                else:
                    content_btns.append(Button(text=label))
                    if not is_q[m]:
                        self.doubt(m, "button_no_url", f"кнопка «{label}» без ссылки и без стрелки", bt.id)
            max_only, tg_only = self._platform_buttons(msg)
            if max_only or tg_only:
                def bkey(x: Item) -> tuple:
                    u = blabel[x.id][1]
                    return (blabel[x.id][0], self._placeholder(u, channel_url, vip_url) if u else None)
                mk, tk = {bkey(x) for x in max_only}, {bkey(x) for x in tg_only}
                all_btns = list(content_btns)
                content_btns = [x for x in all_btns if (x.text, x.url) not in mk]
                step.setdefault("content", {})["max"] = {"buttons": [x.model_dump() for x in all_btns
                                                                     if (x.text, x.url) not in tk]}
                self.doubt(m, "platform_buttons", "кнопки по платформам (стикеры «для МАХ» / «КАНАЛ TG» у кнопок): "
                           "в Telegram – " + ", ".join(x.text for x in content_btns) + "; в MAX – "
                           + ", ".join(x.text for x in all_btns if (x.text, x.url) not in tk), level="info")
            # текст
            text = self._text(m, msg, channel_url, vip_url, content_btns)
            step.setdefault("content", {}).setdefault("tg", {})
            step["content"]["tg"]["text"] = text
            step["content"]["tg"]["buttons"] = [x.model_dump() for x in content_btns]
            if t == "video_note":
                step["content"].pop("max", None)
                # сценарий – в «что говорит спикер»: в Telegram у кружка подписи нет, в MAX
                # подпись скрыта – иначе человек услышал бы текст и тут же прочитал (грабли §25)
                script = html_plain(text)
                step["title"] = "Кружок: " + _cut((script.splitlines() or [""])[0], 50)
                step["content"]["max"] = {"text": ""}
                self.doubt(m, "video_note_file", "кружок: на доске только сценарий (он в «что говорит спикер», "
                           "в бот не уходит) – загрузите ролик и запасное видео для тех, у кого кружки запрещены")
            if msg.image is not None:
                step.setdefault("media", {})["image"] = self._image(m, msg.image)
            if is_q[m]:
                qn += 1
                final = q_final.get(m, True)
                nxt = next((d["dst"] for d in edges if d["src"] == m and d["kind"] == "survey_next"), None)
                edit = not (final and nxt and types.get(nxt) in ("email_request", "phone_request"))
                step["survey"] = {"qid": f"q{qn}", "multi": bool(done_btn or MULTI_RE.search(msg.item.text)),
                                  "final": final, "edit": edit}
                if done_btn:
                    step["survey"]["done_button"] = done_btn
            if t == "phone_request":
                if skip_known.get(m) == "phone":
                    step.setdefault("contact", {})["skip_if_known"] = True
            if t == "email_request":
                step["email"] = {"skip_if_known": True}
                self.doubt(m, "rule_email", "почта уже известна – шаг пропускается; не прислал за 10 минут – "
                           "воронка идёт дальше (по правилу, на доске этого нет)", level="info")
            if t == "call":
                step["content"]["tg"]["text"] = ""
                step["title"] = "Автозвонок"
                self.doubt(m, "call_audio", "автозвонок: загрузите голосовое (на доске только ромб "
                           f"«{_cut(msg.item.text, 40)}»)")
            if self.tl is not None and m in self.tl.timing:
                sch = self._schedule(m)
                if sch:
                    step["schedule"] = sch
            if msg.album and msg.image is not None:
                refs = [step["media"]["image"]] + [self._image(m, a) for a in msg.album]
                step["media"]["images"] = [r for r in refs if r]
            if conds[m]:
                step["conditions"] = dict(conds[m])
            steps.append(Step.model_validate(step))

        # 7. стрелки графа
        by_id = {s.key: s for s in steps}
        del by_id
        g_edges: list[Edge] = []
        idx: Counter = Counter()

        def add_edge(src: str, dst: str, kind: str, delay: int | None) -> None:
            k = (src, kind, dst)
            idx[k] += 1
            g_edges.append(Edge(id=edge_id(sid[src], kind, f"miro:{dst}", idx[k] - 1), from_step=sid[src],
                                kind=EdgeKind(kind), to_step=sid[dst],
                                delay_seconds=delay if kind not in ("callback", "survey_next", "jump") else None))

        # телефон: «уже известен» – туда же, куда «дал»; не дал – по стикеру
        for m in order:
            if types[m] != "phone_request":
                continue
            outs = [d for d in edges if d["src"] == m]
            contact = next((d for d in outs if d["kind"] == "contact"), None)
            skip = next((d for d in outs if d["kind"] == "skip"), None)
            if contact is None and skip is not None and skip["mean"].event != "no_contact":
                skip["kind"] = "contact"
                contact, skip = skip, None
            if skip is not None and skip["delay"] is None:
                self.doubt(m, "rule_phone_skip", "не указано, через сколько идти дальше, если человек не дал "
                           "номер, – поставлено 5 минут", level="info")
            tgt = (contact or skip or {}).get("dst")
            if tgt is None:
                self.doubt(m, "phone_no_next", "из запроса телефона не ведёт ни одна стрелка")
                continue
            if skip is None:
                edges.append({"src": m, "dst": tgt, "kind": "skip", "delay": 300, "mean": StickyMeaning(""), "arrow": None})
                self.doubt(m, "rule_phone_skip", "не указано, что делать, если человек не дал номер – "
                           "через 5 минут воронка идёт дальше", level="info")
            second = [x for x in entry_targets[1:] if x != m]
            if skip_known.get(m) == "phone" or (m == entry and second):
                k_tgt = second[0] if (m == entry and second) else tgt
                edges.append({"src": m, "dst": k_tgt, "kind": "skip_if_phone_known", "delay": 0,
                              "mean": StickyMeaning(""), "arrow": None})
                steps[order.index(m)].contact.skip_if_known = True
        for m in order:
            if types[m] != "email_request":
                continue
            nxt = next((d["dst"] for d in edges if d["src"] == m and d["kind"] == "email"), None)
            if nxt:
                edges.append({"src": m, "dst": nxt, "kind": "skip", "delay": 600, "mean": StickyMeaning(""), "arrow": None})
                edges.append({"src": m, "dst": nxt, "kind": "skip_if_email_known", "delay": 0,
                              "mean": StickyMeaning(""), "arrow": None})
        for d in edges:
            if d["kind"] == "contact" and d["delay"] is None:
                d["delay"] = 0
            if d["kind"] == "skip" and d["delay"] is None:
                d["delay"] = 300
            add_edge(d["src"], d["dst"], d["kind"], d["delay"])

        # параллельная стрелка у шага с условием теряется при пропуске – перевешиваем выше
        steps_by = {s.id: s for s in steps}
        self._rehang(g_edges, steps_by, sid)

        # 8. воронка
        for m in order:
            s = steps[order.index(m)]
            board_fields = board_values(s)
            s.miro = MiroStep(item_id=m, item_ids=msgs[m].item_ids(), doubts=self.sdoubts.get(m, []),
                              board=board_fields)
        for st in self.loose:
            if st.id in self.consumed:
                continue
            mm = self.meaning(st, "стикер в стороне от стрелок")
            if st.text.strip():
                self.doubt(None, "loose_sticker", f"стикер «{_cut(st.text, 80)}» не отнесён ни к одному "
                           "сообщению" + (" – похоже на время/условие, проверьте" if not mm.empty else ""),
                           st.id, level="check" if not mm.empty else "info")
        for it in b.others:
            if it.role == "decision" and it.text:
                self.doubt(None, "decision", f"ромб «{_cut(it.text, 60)}» – развилку по условию движок не "
                           "рисует ромбом, проверьте, как она перенесена", it.id)
        if channel_url:
            self.doubt(None, "channel_link", f"ссылка на канал {channel_url} заменена на {{channel.link}} – "
                       "сама ссылка задаётся в launch.yaml запуска", level="info")
            cfg_link = ((self.launch_cfg.get("channel") or {}).get("link") or "").strip()
            if cfg_link and cfg_link.rstrip("/") != channel_url.rstrip("/"):
                self.doubt(None, "channel_mismatch", f"на доске канал {channel_url}, а в launch.yaml "
                           f"запуска «{self.launch}» – {cfg_link}")
        if vip_url:
            self.doubt(None, "vip_link", f"ссылка ВИП {vip_url} заменена на {{vip_link}} (прокси с учётом "
                       "клика) – страница оплаты задаётся в launch.yaml", level="info")
        entry_step = sid.get(entry) if entry else None
        if entry_step and order and order[0] != entry:
            order.remove(entry)
            order.insert(0, entry)
        steps.sort(key=lambda s: order.index(next(m for m in order if sid[m] == s.id)))
        edges_base = [{"from": _item_of(e.from_step, sid), "kind": e.kind.value,
                       "to": _item_of(e.to_step, sid), "delay": e.delay_seconds} for e in g_edges]
        branch_router = self._branch_router(sid.get(entry) if entry else None)
        lanes, layout = self._layout(steps, sid)
        notes += self._notes(steps, sid, g_edges)
        funnel = Funnel(
            id=self.funnel_id, title=b.frame_title, source=SourceInfo(launch=self.launch),
            branch_router=branch_router, lanes=lanes, layout=layout,
            steps=steps, edges=g_edges, notes=notes,
            miro=MiroSource(board_id=b.board_id, frame_id=b.frame_id, frame_title=b.frame_title,
                            link=self.link, imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            doubts=self.fdoubts, entry_item=entry, edges_base=edges_base,
                            bot_description=bot_description),
        )
        stats = {
            "messages": len(steps), "edges": len(g_edges),
            "check": sum(1 for s in steps for d in (s.miro.doubts if s.miro else []) if d.level == "check")
            + sum(1 for d in self.fdoubts if d.level == "check"),
            "steps_to_check": sum(1 for s in steps if s.miro and any(d.level == "check" for d in s.miro.doubts)),
            "images": sum(1 for s in steps if s.media.image),
            "parser": getattr(self.parser, "name", "rules"),
            "notes": sum(1 for n in notes if n.author == "Miro"),
            "days": len(self.tl.day_rows()) if self.tl is not None else 0,
            "dialect": b.dialect.code if b.dialect else "",
            "board": self.accounting,
        }
        return ImportResult(funnel=funnel, board=b, stats=stats, images=self.images)

    # ── фрейм-«расписание»: дни, время от эфира, ветки (rows.py) ──
    def _in_day(self, mid: str | None) -> bool:
        return bool(mid) and self.tl is not None and mid in self.tl.row_of and bool(self.tl.row_of[mid].day)

    def _day_edges(self, entry: str | None, steps_set: set[str], edges: list[dict], conds: dict) -> None:
        tl = self.tl
        msgs = self.board.messages
        order_ok = order_bad = 0
        for r in tl.day_rows():
            ms = [m for m in r.msgs if m in steps_set]
            if not ms:
                continue
            if entry and entry not in ms:
                edges.append({"src": entry, "dst": ms[0], "kind": "also_fire", "delay": 0,
                              "mean": StickyMeaning(""), "arrow": None})
            pos = {m: i for i, m in enumerate(ms)}
            for a, b2 in zip(ms, ms[1:]):
                edges.append({"src": a, "dst": b2, "kind": "done", "delay": 0, "mean": StickyMeaning(""), "arrow": None})
            rep_first = next((m for m in ms if tl.timing.get(m) and tl.timing[m].anchor == "repeat"), None)
            for m in ms:
                t = tl.timing.get(m)
                if t is not None and t.anchor == "repeat":
                    conds[m]["attendance"] = "was_not"
                    conds[m]["gate_window_hours"] = 24
            if rep_first:
                self.doubt(rep_first, "rule_repeat", f"{r.title}: блок повтора (справа от стикера «повтор») – только "
                           "тем, кто не был на эфире этого дня (по правилу прошлого переноса); зал Bizon дня "
                           "задайте в условии шагов", level="info")
            for m in ms:
                t = tl.timing.get(m)
                if t is None or t.kind == "none":
                    self.doubt(m, "no_time", f"{r.title}: у колонки нет подписи времени – шаг уйдёт сразу за "
                               "предыдущим; проверьте, когда его слать")
                elif t.source == "текст сообщения":
                    self.doubt(m, "time_from_text", f"подписи времени нет – время взято из текста «{_cut(t.text, 40)}»",
                               level="info")
                elif t.overrides:
                    self.doubt(m, "time_hint", f"подпись «{t.overrides}» расходится со стикерами веток "
                               f"«{t.text}» – взяты стикеры (поздняя правка копирайтера)", level="info")
                elif t.source.startswith("правило"):
                    self.doubt(m, "time_rule", f"время: {t.source}", level="info")
                if t is not None and t.bumped:
                    self.doubt(m, "time_bump", "у предыдущего шага то же время – этот на минуту позже, чтобы "
                               "порядок не зависел от таймеров", level="info")
            # стрелки дня сверяем с порядком колонок
            for c in self.day_conns:
                a, b2 = self.board.owner.get(c.start), self.board.owner.get(c.end)
                if a in pos and b2 in pos:
                    if pos[b2] > pos[a]:
                        order_ok += 1
                    else:
                        order_bad += 1
                        self.doubt(b2, "day_arrow", f"стрелка из «{_cut(msgs[a].item.text, 30)}» идёт против "
                                   "порядка по времени – шаги связаны по времени подписей")
        for m, ws in tl.windows.items():
            if m in steps_set:
                conds[m]["registered_windows"] = list(ws)
                self.doubt(m, "reg_windows", "не шлём зарегистрировавшимся в " + ", ".join(ws)
                           + " (по записке на доске)", level="info")
        days = tl.day_rows()
        if entry and days:
            self.doubt(None, "days_armed", f"{len(days)} дн.: каждый день – своя цепочка по колонкам, все дни "
                       "армируются разом из входа (как в прошлом переносе: сквозная цепочка теряет утро "
                       "следующего дня у вечерней ветки); стрелки между днями не перенесены", level="info")
        if self.day_conns:
            self.doubt(None, "day_arrows", f"стрелок внутри дней: {len(self.day_conns)} – порядок взят по колонкам "
                       f"и времени подписей ({order_ok} стрелок с ним совпали)", level="info")

    def _schedule(self, m: str) -> dict | None:
        t = self.tl.timing.get(m)
        r = self.tl.row_of[m]
        day_off = (r.day or 1) - 1
        if t is None:
            return None
        if t.kind == "clock":
            return {"absolute_time_msk": _rows._fmt(t.minutes), "day_offset": day_off + t.minutes // 1440}
        if t.kind == "webinar":
            d: dict[str, Any] = {"webinar_offset_minutes": t.minutes, "anchor": t.anchor, "day_offset": day_off}
            byb = {self.branch_id[w]: o for w, o in t.by_branch.items() if w in self.branch_id}
            if byb:
                d["webinar_offset_by_branch"] = byb
            return d
        return None

    def _row_keys(self, order: list[str], types: dict, is_q: dict) -> dict[str, str]:
        out, n, q = {}, Counter(), 0
        for m in order:
            if is_q.get(m):
                q += 1
                out[m] = f"q{q}"
                continue
            r = self.tl.row_of.get(m)
            pre = f"d{r.day}" if r is not None and r.day else "reg"
            n[pre] += 1
            out[m] = f"{pre}_{n[pre]:02d}" + ("_call" if types.get(m) == "call" else "")
        return out

    def _branch_router(self, entry_sid: uuid.UUID | None) -> BranchRouter | None:
        if self.tl is None or not self.tl.branches or entry_sid is None:
            if self.tl is not None and self.tl.day_rows():
                self.doubt(None, "no_branches", "на доске нет правила «если зарегался до … – стартует в … с "
                           "повтором в …» – время эфира задайте в настройках воронки")
            return None
        rules = sorted(self.tl.branches, key=lambda b: b.webinar)
        windows = {b.webinar: [(a, z, d) for a, z, d in b.windows] for b in rules}
        gaps = _rows.windows_uncovered(rules)
        for a, z in gaps:
            windows[rules[0].webinar].append((a, z, "tomorrow"))
            self.doubt(None, "branch_gap", f"на доске нет правила для регистрации с {_rows._fmt(a)} до "
                       f"{_rows._fmt(z)} – отправлены на эфир {_rows._fmt(rules[0].webinar)} следующего дня "
                       "(как в прошлом переносе)")
        brs = []
        for b in rules:
            brs.append(Branch(id=self.branch_id[b.webinar], entry_step=entry_sid, webinar_time=_rows._fmt(b.webinar),
                              repeat_offset_minutes=b.repeat_offset,
                              windows=[BranchWindow.model_validate({"from": _rows._fmt(a), "to": _rows._fmt(z), "day": d})
                                       for a, z, d in sorted(windows[b.webinar])]))
        self.doubt(None, "branches", "ветки по времени регистрации: " + "; ".join(
            f"{x.id} – эфир {x.webinar_time}, повтор через {x.repeat_offset_minutes // 60} ч {x.repeat_offset_minutes % 60} мин"
            for x in brs) + " (со стикера правила на доске)", level="info")
        if self.air_btns:
            self.doubt(None, "air_buttons", f"{self.air_btns} кнопок «Подключиться / Занять место» без ссылки – "
                       "поставлена ссылка на эфир {stream_url}; залы Bizon по дням задайте в запуске", level="info")
        return BranchRouter(branches=brs)

    # ── раскладка: как на доске ──
    def _layout(self, steps: list[Step], sid: dict[str, uuid.UUID]) -> tuple[list[Lane], Layout]:
        S = LAYOUT_SCALE
        msgs = self.board.messages
        pos: dict[uuid.UUID, Position] = {}
        item_of = {v: k for k, v in sid.items()}
        for s in steps:
            m = msgs[item_of[s.id]]
            pos[s.id] = Position(x=round(m.left * S, 1), y=round(m.top * S, 1))
        lanes: list[Lane] = []
        if self.tl is not None:
            for r in self.tl.rows:
                ids = [sid[m] for m in r.msgs if m in sid]
                if not ids:
                    continue
                lid = uuid.uuid5(STUDIO_NS, f"miro-lane:{self.board.board_id}:{self.board.frame_id}:{r.idx}")
                lanes.append(Lane(id=lid, title=r.title, kind="day" if r.day else "entry", day=r.day,
                                  order=r.idx, step_ids=ids))
                pos[lid] = Position(x=0, y=round(r.y0 * S, 1))
        return lanes, Layout(positions=pos)

    # ── всё, что не стало шагом, – заметкой на холсте; счёт элементов доски ──
    def _notes(self, steps: list[Step], sid: dict[str, uuid.UUID], g_edges: list[Edge]) -> list[Note]:
        b = self.board
        S = LAYOUT_SCALE
        status: dict[str, tuple[str, str]] = {}      # элемент → (категория, причина)
        notes: list[Note] = []
        item_of = {v: k for k, v in sid.items()}

        def note_id(iid: str) -> uuid.UUID:
            return uuid.uuid5(STUDIO_NS, f"miro-note:{b.board_id}:{iid}")

        def mark(iid: str, cat: str, why: str) -> None:
            if iid not in status:
                status[iid] = (cat, why)

        # шаги и всё, что в них вошло; стикеры шага без смысла – заметкой на карточке
        for s in steps:
            m = item_of[s.id]
            msg = b.messages[m]
            for st in msg.stickies:
                if st.id in self.consumed or (self.tl is not None and st.id in self.tl.used):
                    continue
                mm = self.meaning(st, "")
                if mm.empty and st.text.strip():
                    notes.append(Note(id=note_id(st.id), text=st.text, color=_note_color(st), step_id=s.id,
                                      author="Miro"))
                    mark(st.id, "note", "стикер у сообщения – заметка на карточке")
            for iid in msg.item_ids():
                mark(iid, "step", "часть сообщения")
            if self.tl is not None:
                for it in self.tl.label_of.get(m, []):
                    mark(it.id, "step", "время / условие шага")
        # стикеры и подписи на стрелках между шагами
        for c in b.conns:
            if b.owner.get(c.start) in sid or c.start in {x.id for x in b.start_buttons}:
                if b.owner.get(c.end) in sid:
                    for st in c.stickies:
                        mark(st.id, "step", "время / условие стрелки")
        for iid in self.edge_stickies:
            mark(iid, "step", "время / условие стрелки")
        for c in self.day_conns:
            for st in c.stickies:
                mark(st.id, "settings", "подпись на стрелке дня (порядок – по колонкам)")
        for iid, why in self.consumed.items():
            mark(iid, "settings", why)
        if self.tl is not None:
            for iid, why in self.tl.used.items():
                mark(iid, "settings", why)
        # описание бота и «СТАРТ»
        for st in b.start_buttons:
            mark(st.id, "settings", "кнопка «СТАРТ» – вход в воронку")
            dm = b.owner.get(st.id)
            if dm in b.messages:
                for iid in b.messages[dm].item_ids():
                    mark(iid, "settings", "описание бота («Что умеет этот бот?»)")
        for ln in b.lanes:
            mark(ln.id, "settings", f"заголовок раздела «{_cut(ln.text, 30)}»")
        # стикеры, которые остались в стороне
        loose = {st.id for st in getattr(self, "loose", [])}
        # сообщения, не ставшие шагами, – заметкой целиком (с кнопками и картинкой)
        for m, msg in b.messages.items():
            if m in sid or msg.item.id in status:
                continue
            why = self.as_note.get(m) or self._not_step_reason(m)
            txt = msg.item.text
            if msg.buttons:
                txt += "\n\nКнопки: " + " · ".join(" ".join(x.text.split()) for x in msg.buttons)
            if msg.image is not None:
                txt = "🖼 картинка над сообщением\n\n" + txt
            notes.append(self._free_note(msg.item, txt, why, msg.left, msg.top))
            for iid in msg.item_ids():
                mark(iid, "note", why)
        for it in sorted(b.items.values(), key=lambda i: (i.y, i.x)):
            if it.id in status:
                continue
            if it.role == "image":
                why = "картинка без сообщения под ней"
                notes.append(self._free_note(it, "🖼 картинка с доски", why, it.left, it.top))
                mark(it.id, "note", why)
                self.doubt(None, "note_image", f"{why} – стоит на холсте заметкой, проверьте", it.id)
                continue
            if not it.text.strip():
                mark(it.id, "skipped", "пустая фигура без текста")
                continue
            why = _NOTE_WHY.get(it.role, "не распознано")
            if it.id in loose:
                why = "стикер не отнесён ни к сообщению, ни к стрелке"
            notes.append(self._free_note(it, it.text, why, it.left, it.top))
            mark(it.id, "note", why)
            if it.id not in loose and it.role in ("sticky", "button", "timing", "decision", "note"):
                self.doubt(None, "note_unclear", f"«{_cut(it.text, 60)}» – {why}; стоит на холсте заметкой, "
                           "проверьте", it.id)
        # стрелки
        used_conn = {c.id for c in b.conns if b.owner.get(c.end) in sid}
        for c in b.dangling:
            end = b.items[c.end]
            txt = "стрелка ведёт в «" + _cut(end.text, 40) + "» и дальше никуда"
            if c.id in used_conn:
                continue
            if end.id in status and status[end.id][0] in ("note", "settings", "step"):
                continue
            notes.append(Note(id=note_id("conn:" + c.id), text=txt + "\n\n⚠ с доски Miro: стрелка в пустоту",
                              color="#ffe0e0", position=Position(x=round(end.left * S, 1), y=round(end.top * S, 1)),
                              author="Miro"))
            self.doubt(None, "arrow_nowhere", txt + " – проверьте", c.id)
        self._status = status
        cats = Counter(v[0] for v in status.values())
        reasons = Counter(v[1] for v in status.values() if v[0] == "skipped")
        total = len(b.items)
        self.accounting = {
            "items": total, "in_steps": cats["step"], "settings": cats["settings"], "notes": cats["note"],
            "skipped": cats["skipped"], "skipped_reasons": dict(reasons),
            "unaccounted": total - sum(cats.values()),
            "connectors": self._conn_accounting(sid),
        }
        return notes

    def _conn_accounting(self, sid: dict) -> dict:
        """Коннекторы доски: сколько стали стрелками шагов, сколько в днях (порядок там по
        колонкам), сколько ведут в заметки или в пустоту, сколько за фрейм."""
        b = self.board
        where: dict[str, str] = {}
        day_ids = {c.id for c in self.day_conns}
        for c in b.conns + b.headless:
            s_ok = b.owner.get(c.start) in sid or c.start in {x.id for x in b.start_buttons}
            e_ok = b.owner.get(c.end) in sid
            cat = "в днях" if c.id in day_ids or self._in_day(b.owner.get(c.end)) and c in b.headless                 else ("стрелки шагов" if s_ok and e_ok else "к заметкам")
            for pid in b.conn_items.get(c.id, [c.id]):
                where.setdefault(pid, cat)
        for c in b.dangling:
            end_known = c.end in self._status and self._status[c.end][0] in ("settings", "step")
            for pid in b.conn_items.get("dangling:" + c.id, [c.id]):
                where.setdefault(pid, "к подписям дней" if end_known else "в пустоту (заметка)")
        out = Counter(where.values())
        out["за фреймом"] = len(b.outside)
        return {"total": b.n_conns, **dict(out), "unaccounted": b.n_conns - len(where) - len(b.outside)}

    def _paid_note(self, m: str) -> str | None:
        """Сообщение «оплата прошла» рядом со стикером «если оплатил вип / отбивка после оплаты» –
        его шлёт модуль оплаты (vip_sync), в воронку оно не входит (так и у человека)."""
        msg = self.board.messages[m]
        if not PAID_TEXT_RE.search(msg.item.text):
            return None
        for st in self.board.items.values():
            if st.role == "sticky" and PAID_RE.search(st.text) and msg.item.box_dist(st.x, st.y) < 1500:
                self.consumed.setdefault(st.id, "стикер «после оплаты ВИП» у сообщения модуля оплаты")
                return "сообщение после оплаты ВИП – его шлёт модуль оплаты, в воронку не входит"
        return None

    def _entry_columns(self, arrows: dict, entry_targets: list[str], desc_ids: set[str], bot_in: Counter) -> None:
        """Полоса входа на фрейме-«расписании»: сообщение без входящей стрелки идёт за
        предыдущей колонкой (так читал доску и человек). Подпись, из которой стрелка идёт без
        начала («через 10 секунд отправлять» → сообщение), даёт время."""
        b = self.board
        row = next((r for r in self.tl.rows if not r.day), None)
        if row is None:
            return
        head: dict[str, list[Item]] = defaultdict(list)
        for c in b.headless:
            e = b.owner.get(c.end)
            if e:
                head[e] += [b.items[c.start]] + c.stickies
                for x in [c.start] + [st.id for st in c.stickies]:
                    self.consumed.setdefault(x, "подпись времени у сообщения входа")
        seq = [m for m in row.msgs if m not in desc_ids and m not in self.as_note
               and not LETTER_RE.match(b.messages[m].item.text)]
        # стикеры / подписи времени между колонками: оставшиеся в стороне и прилипшие к сообщению
        free = [st for st in self.loose if self.meaning(st, "").delay_seconds is not None]
        for m in seq:
            for st in b.messages[m].stickies:
                mm = self.meaning(st, "")
                if mm.delay_seconds is not None and not mm.conditions and not mm.skip_if_known and not mm.event:
                    free.append(st)
        prev = None
        for m in seq:
            if prev is not None and m not in entry_targets and not bot_in[m]:
                a = arrows.setdefault((prev, m), _Arrow(prev, m))
                a.parts.add("body")
                a.stickies += head.get(m, [])
                lo, hi = b.messages[prev].item.x, b.messages[m].item.x
                for st in [x for x in free if lo <= x.x <= hi and row.y0 <= x.y < row.y1]:
                    a.stickies.append(st)
                    free.remove(st)
                    if st in self.loose:
                        self.loose.remove(st)
                    for mm_ in b.messages.values():
                        if st in mm_.stickies:
                            mm_.stickies.remove(st)
                bot_in[m] += 1
                self.doubt(m, "edge_columns", "стрелки на доске нет – сообщение идёт за предыдущей колонкой "
                           f"«{_cut(b.messages[prev].item.text, 30)}»", level="info")
            prev = m

    def _platform_buttons(self, msg: Message) -> tuple[list[Item], list[Item]]:
        """Стикер «для МАХ» / «КАНАЛ МАХ» / «КАНАЛ TG» вплотную к кнопке – кнопка только этой платформы."""
        max_only, tg_only = [], []
        sts = [st for st in self.board.items.values()
               if st.role == "sticky" and PLATFORM_STICKY_RE.match(st.text or "")]
        for bt in msg.buttons:
            near = [st for st in sts if bt.box_dist(st.x, st.y) <= 450]
            if not near:
                continue
            st = min(near, key=lambda x: bt.box_dist(x.x, x.y))
            is_max = bool(re.search(r"ма[хx]|max", st.text, re.I))
            (max_only if is_max else tg_only).append(bt)
            self.consumed.setdefault(st.id, "кнопка только для " + ("MAX" if is_max else "Telegram"))
        return max_only, tg_only

    def _not_step_reason(self, m: str) -> str:
        msg = self.board.messages[m]
        if LETTER_RE.match(msg.item.text):
            return "письмо (не сообщение бота)"
        sts = [s for s in self.board.items.values() if s.role == "sticky" and PAID_RE.search(s.text)
               and msg.item.box_dist(s.x, s.y) < 1500]
        if sts:
            return "сообщение после оплаты ВИП – его шлёт модуль оплаты, в воронку не входит"
        return "сообщение вне цепочки (раздел другого модуля)"

    def _free_note(self, it: Item, text: str, why: str, x: float, y: float) -> Note:
        S = LAYOUT_SCALE
        return Note(id=uuid.uuid5(STUDIO_NS, f"miro-note:{self.board.board_id}:{it.id}"),
                    text=(text.strip() + f"\n\n⚠ с доски Miro: {why}").strip(), color=_note_color(it),
                    position=Position(x=round(x * S, 1), y=round(y * S, 1)), author="Miro")

    # ── помощники ──
    def _section(self, it: Item) -> Item | None:
        """Раздел доски: ближайшая сверху полоса-заголовок, накрывающая элемент по горизонтали."""
        best = None
        for ln in self.board.lanes:
            if ln.left <= it.x <= ln.right and ln.y < it.top + 50:
                if best is None or ln.y > best.y:
                    best = ln
        return best

    def _conds(self, m: str, mean: StickyMeaning, conds: dict) -> None:
        if mean.conditions:
            conds[m].update(mean.conditions)

    def _links(self, steps_ids: list[str], blabel: dict) -> tuple[str | None, str | None]:
        msgs = self.board.messages
        ch: Counter = Counter()
        vip: Counter = Counter()
        for m in steps_ids:
            for bt in msgs[m].buttons:
                label, url, _ = blabel[bt.id]
                if not url:
                    continue
                if CHANNEL_URL_RE.match(url) or re.search(r"канал", label, re.I):
                    ch[url] += 2 if re.search(r"канал", label, re.I) else 1
                if VIP_LABEL_RE.search(label):
                    vip[url] += 1
            for href, _ in links(msgs[m].item.html):
                if CHANNEL_URL_RE.match(href):
                    ch[href] += 1
        btn_urls = {blabel[bt.id][1] for m in steps_ids for bt in msgs[m].buttons}
        channel = ch.most_common(1)[0][0] if ch else None
        vip_url = vip.most_common(1)[0][0] if vip else None
        others = [u for u in ch if u != channel and CHANNEL_URL_RE.match(u) and u in btn_urls]
        for u in others:
            self.doubt(None, "second_channel", f"на доске есть вторая ссылка-приглашение {u} – возможно, "
                       "канал прошлого запуска; она оставлена как есть")
        return channel, vip_url

    @staticmethod
    def _placeholder(url: str, channel: str | None, vip: str | None) -> str:
        if channel and url.rstrip("/") == channel.rstrip("/"):
            return "{channel.link}"
        if vip and url.rstrip("/") == vip.rstrip("/"):
            return "{vip_link}"
        return url

    def _text(self, m: str, msg: Message, channel: str | None, vip: str | None,
              buttons: list[Button]) -> str:
        s = to_tg_html(msg.item.html)
        s = s.replace("—", "–")
        # ссылки → плейсхолдеры
        for url, ph in ((channel, "{channel.link}"), (vip, "{vip_link}")):
            if not url:
                continue
            esc = re.escape(_html.escape(url, quote=True))
            s = re.sub(rf'<a href="{esc}/?">\s*{re.escape(_html.escape(url, quote=False))}/?\s*</a>', ph, s)
            s = re.sub(rf'<a href="{esc}/?">', f'<a href="{ph}">', s)
            s = re.sub(rf"(?<![\"=]){re.escape(_html.escape(url, quote=False))}/?", ph, s)
        # {Имя}
        s = re.sub(r"(^|\n|<b>|<i>)([^\w\n<]*?)\s*(\[?ИМЯ\]?|\[?Имя\]?)(?=\s*[,!\s])",
                   lambda mm: mm.group(1) + mm.group(2) + "{Имя}", s)
        s = s.replace("[ИМЯ]", "{Имя}").replace("[Имя]", "{Имя}")
        s = re.sub(r"\{Имя\}\s+,", "{Имя},", s)
        # время эфира словами доски → время человека (у каждого своё: ветка 13:00 или 18:00)
        msk = r"(?:\s*МСК(?![А-ЯA-Z]))?"
        s2 = re.sub(r"\[\s*(?:сегодня\s*/\s*завтра\s+)?ОСНОВНОЕ[_ ]ВРЕМЯ\s*\]|\{ОСНОВНОЕ[_ ]ВРЕМЯ\}" + msk + "|"
                    r"ОСНОВНОЕ[_ ]ВРЕМЯ(?:\s+время)?" + msk,
                    '<tg-time unix="{webinar.main_unix}">{webinar.main_time} МСК</tg-time>', s)
        s2 = re.sub(r"ВРЕМЯ ПОВТОРА" + msk, '<tg-time unix="{webinar.unix}">{webinar.time} МСК</tg-time>', s2)
        # «МСК» за закрывающим тегом – уже в подстановке
        s2 = re.sub(r"</tg-time>((?:\s*</?b>)+)\s*МСК(?![А-ЯA-Z])", r"</tg-time>\1", s2)
        if s2 != s:
            self.doubt(m, "time_placeholder", "«ОСНОВНОЕ ВРЕМЯ» / «ВРЕМЯ ПОВТОРА» в тексте – время эфира человека "
                       "({webinar.main_time} / {webinar.time})", level="info")
            s = s2
        if LINK_WORD_RE.search(s):
            s = re.sub(r"\s*" + LINK_WORD_RE.pattern, "", s, flags=re.I)
            self.doubt(m, "text_link_word", "в тексте вместо ссылки написано «(ссылка)» – убрано; "
                       "проверьте, какая ссылка тут нужна")
        # строки-заглушки в квадратных скобках
        lines = s.split("\n")
        keep = []
        btn_urls = {x.url for x in buttons if x.url}
        for ln in lines:
            if BRACKET_LINE_RE.match(ln):
                self.doubt(m, "placeholder_line", f"строка-заглушка «{html_plain(ln)}» убрана", level="info")
                continue
            bare = html_plain(ln).strip()
            if bare and bare in btn_urls and re.fullmatch(r"(<[^>]+>|\s)*\S+(<[^>]+>|\s)*", ln):
                self.doubt(m, "dup_link_line", "строка только со ссылкой, которая уже есть на кнопке, – "
                           "убрана", level="info")
                continue
            keep.append(ln)
        s = "\n".join(keep)
        s = re.sub(r"\n{3,}", "\n\n", s).strip()
        leftovers = [u for u in re.findall(r"https?://[^\s<>\"]+", s)]
        for u in leftovers:
            if CHANNEL_URL_RE.match(u):
                self.doubt(m, "second_channel", f"в тексте ссылка на другой канал {u} – проверьте "
                           "(часто это канал прошлого запуска)")
        return s

    def _image(self, m: str, img: Item) -> str:
        if img.id in self.images:
            return self.images[img.id]
        ref = f"./static/miro_{img.id}.jpg"
        if self.fetch_image is not None:
            try:
                ref = self.fetch_image(img)
            except Exception as e:  # noqa: BLE001
                self.doubt(m, "image_failed", f"картинка не скачалась из Miro ({e}) – загрузите вручную", img.id)
                ref = ""
        self.images[img.id] = ref
        return ref or None  # type: ignore[return-value]

    def _order(self, entry: str | None, steps_ids: list[str], edges: list[dict]) -> list[str]:
        msgs = self.board.messages
        # время от входа (для порядка): минимум по путям
        best: dict[str, int] = {}
        if entry:
            best[entry] = 0
            changed = True
            for _ in range(len(steps_ids) + 2):
                if not changed:
                    break
                changed = False
                for d in edges:
                    if d["src"] in best:
                        t = best[d["src"]] + (d.get("delay") or 0)
                        if t < best.get(d["dst"], 1 << 60):
                            best[d["dst"]] = t
                            changed = True
        in_chain = sorted((m for m in steps_ids if m in best),
                          key=lambda m: (best[m], msgs[m].item.x, msgs[m].item.y))
        rest = sorted((m for m in steps_ids if m not in best), key=lambda m: (msgs[m].item.x, msgs[m].item.y))
        order = in_chain + rest
        if entry and entry in order:
            order.remove(entry)
            order.insert(0, entry)
        return order

    @staticmethod
    def _keys(order: list[str], types: dict, is_q: dict) -> dict[str, str]:
        out, n, q = {}, 0, 0
        for m in order:
            if is_q.get(m):
                q += 1
                out[m] = f"q{q}"
            else:
                n += 1
                out[m] = f"m{n:02d}"
        return out

    def _rehang(self, g_edges: list[Edge], steps_by: dict, sid: dict) -> None:
        item_of = {v: k for k, v in sid.items()}
        for e in list(g_edges):
            if e.kind != EdgeKind.also_fire:
                continue
            src = steps_by[e.from_step]
            if not src.conditions.active():
                continue
            # вверх по основной стрелке к шагу без условий
            total = e.delay_seconds or 0
            cur = e.from_step
            seen = {cur}
            anc = None
            while True:
                inc = [x for x in g_edges if x.to_step == cur and x.kind in (EdgeKind.done, EdgeKind.also_fire)]
                if len(inc) != 1 or inc[0].from_step in seen:
                    break
                total += inc[0].delay_seconds or 0
                cur = inc[0].from_step
                seen.add(cur)
                if not steps_by[cur].conditions.active():
                    anc = cur
                    break
            if anc is None:
                continue
            g_edges.remove(e)
            g_edges.append(Edge(id=edge_id(anc, "also_fire", f"miro:{item_of[e.to_step]}", 9),
                                from_step=anc, kind=EdgeKind.also_fire, to_step=e.to_step, delay_seconds=total))
            self.doubt(item_of[e.to_step], "rehang", f"параллельная стрелка из «{steps_by[e.from_step].key}» "
                       f"перевешена на «{steps_by[anc].key}» (+{total} с): у шага с условием она потерялась бы "
                       "при пропуске", level="info")



_NOTE_WHY = {
    "sticky": "стикер не отнесён ни к сообщению, ни к стрелке",
    "offbot": "не похоже на сообщение бота (цвет или форма) – заметка для людей, страница сайта или письмо",
    "decision": "ромб-развилка: развилку по условию движок ромбом не рисует",
    "note": "текст на доске",
    "button": "кнопка без сообщения над ней",
    "timing": "подпись времени, которую не удалось отнести к сообщению",
    "start": "кнопка «СТАРТ» без описания бота",
    "message": "сообщение вне цепочки",
}
_STICKY_COLORS = {
    "yellow": "#fff6b6", "light_yellow": "#fff9b1", "orange": "#ffd8a8", "red": "#ffc9c9",
    "violet": "#e5d4ef", "cyan": "#c9f0fb", "blue": "#d0e4fb", "dark_green": "#d8f5c0",
    "light_green": "#e6f9d2", "black": "#e6e6e6", "gray": "#eeeeee", "pink": "#fcd6e6",
    "light_pink": "#fde4f0", "light_blue": "#d0e4fb",
}


def _note_color(it: Item) -> str:
    f = (it.fill or "").lower()
    if f.startswith("#") and len(f) == 7:
        return f
    return _STICKY_COLORS.get(f, "#fff6b6")

def _item_of(step_id: uuid.UUID | None, sid: dict[str, uuid.UUID]) -> str | None:
    if step_id is None:
        return None
    for k, v in sid.items():
        if v == step_id:
            return k
    return None


# Поля шага, которые даёт доска: повторный импорт обновляет их, остальное – ручное
BOARD_PATHS: list[tuple[str, ...]] = [
    ("type",), ("title",),
    ("content", "tg", "text"), ("content", "tg", "buttons"), ("content", "tg", "claim_button_text"),
    ("media", "image"), ("media", "images"), ("content", "max", "buttons"),
    ("conditions", "channel"), ("conditions", "not_paid"), ("conditions", "paid"), ("conditions", "survey_done"),
    ("conditions", "attendance"), ("conditions", "gate_window_hours"), ("conditions", "registered_windows"),
    ("schedule", "absolute_time_msk"), ("schedule", "day_offset"), ("schedule", "webinar_offset_minutes"),
    ("schedule", "webinar_offset_by_branch"), ("schedule", "anchor"),
    ("contact", "contact_button"), ("contact", "skip_if_known"),
    ("email", "skip_if_known"),
    ("survey", "qid"), ("survey", "multi"), ("survey", "final"), ("survey", "done_button"), ("survey", "edit"),
]


def get_path(obj: Any, path: tuple[str, ...]) -> Any:
    cur = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def board_values(step: Step) -> dict[str, Any]:
    d = step.model_dump(mode="json")
    out = {}
    for p in BOARD_PATHS:
        cur: Any = d
        for k in p:
            cur = cur.get(k) if isinstance(cur, dict) else None
        out[".".join(p)] = cur
    return out


def build_funnel(snap: dict, *, launch: str, funnel_id: str, parser: StickyParser | None = None,
                 fetch_image: Callable[[Item], str] | None = None, link: str = "",
                 launch_cfg: dict | None = None) -> ImportResult:
    """Снимок фрейма → граф Студии + сомнения."""
    return _Builder(snap, launch=launch, funnel_id=funnel_id, parser=parser or RuleParser(),
                    fetch_image=fetch_image, link=link, launch_cfg=launch_cfg).run()
