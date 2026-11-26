"""Сверка импорта из Miro с эталоном – воронкой, которую разработчик перенёс руками.

Эталон читается существующим импортом yaml → граф (`studio.convert.import_file`), так что
сравниваются два графа Студии. Шаги сопоставляются по смыслу, а не по key (у импорта
свои имена `m01…`): по тексту (нормализованно), типу и порядку; кружки без текста – по
порядку среди кружков.

Метрики (доля от сопоставленных шагов эталона, если не сказано иное):
  * шаги – сколько шагов эталона нашлось в импорте; лишние – шаги импорта без пары;
  * тип, текст (после нормализации: без разметки, эмодзи, регистра и пунктуации),
    картинка (есть / нет), кнопки (подписи и вид цели), условия;
  * стрелки – стрелки эталона между сопоставленными шагами, которые импорт провёл так же
    (те же концы; отдельно – тот же вид и та же задержка);
  * время – «когда придёт от входа» (минимум по путям, секунды) совпадает с эталоном.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..model.funnel import EdgeKind, Funnel, Step
from .html import html_plain

_PH = re.compile(r"\{[^}]+\}")


def norm_text(s: str | None) -> str:
    t = html_plain(s or "")
    t = _PH.sub(" ", t)
    t = "".join(ch for ch in t if unicodedata.category(ch)[0] in ("L", "N", "Z") or ch in " \n")
    t = t.lower().replace("ё", "е")
    return " ".join(t.split())


AIR_URL_RE = re.compile(r"/room/|bizon|\{stream_url\}", re.I)


def _btn_class(url: str | None, cb: str | None) -> str:
    if cb:
        return "callback"
    if not url:
        return "нет"
    # ссылка на эфир: у человека – зал Bizon дня, в импорте – {stream_url}
    if AIR_URL_RE.search(url):
        return "эфир"
    if url.startswith("{"):
        return url
    return "ссылка"


def _sched(s: Step) -> tuple:
    """Расписание шага для сверки: режим и числа (у шага «по стрелке» – пусто)."""
    c = s.schedule
    if c.mode == "webinar":
        base = c.webinar_offset_minutes
        return ("от эфира", base, c.anchor, c.day_offset,
                tuple(sorted(v for v in (c.webinar_offset_by_branch or {}).values() if v != base)))
    if c.mode == "clock":
        return ("часы", c.absolute_time_msk, c.day_offset)
    return ("по стрелке",)


def _buttons(s: Step) -> list[tuple[str, str]]:
    out = [(norm_text(b.text), _btn_class(b.url, b.callback_data)) for b in s.content.tg.buttons]
    if s.content.tg.claim_button_text:
        out.append((norm_text(s.content.tg.claim_button_text), "claim"))
    return out


def arrival_times(f: Funnel) -> dict[uuid.UUID, int]:
    """Когда шаг придёт от входа: минимум суммы задержек по путям (клик/ответ – 0)."""
    if not f.steps:
        return {}
    best = {f.steps[0].id: 0}
    kinds = {EdgeKind.done, EdgeKind.also_fire, EdgeKind.contact, EdgeKind.skip_if_phone_known,
             EdgeKind.email, EdgeKind.skip_if_email_known, EdgeKind.callback, EdgeKind.survey_next}
    for _ in range(len(f.steps) + 2):
        changed = False
        for e in f.edges:
            if e.kind not in kinds or e.to_step is None or e.from_step not in best:
                continue
            t = best[e.from_step] + (e.delay_seconds or 0)
            if t < best.get(e.to_step, 1 << 62):
                best[e.to_step] = t
                changed = True
        if not changed:
            break
    return best


@dataclass
class Compare:
    ref_name: str
    pairs: list[tuple[Step, Step, float]] = field(default_factory=list)   # (эталон, импорт, сходство текста)
    missing: list[Step] = field(default_factory=list)
    extra: list[Step] = field(default_factory=list)
    metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"ref": self.ref_name, "metrics": self.metrics, "issues": self.issues,
                "missing": [s.key for s in self.missing], "extra": [s.key for s in self.extra]}

    def text(self) -> str:
        lines = [f"Сверка с эталоном {self.ref_name}", ""]
        for name, m in self.metrics.items():
            pct = f"{100 * m['ok'] / m['of']:.0f} %" if m["of"] else "–"
            lines.append(f"  {name:<28} {m['ok']:>3} из {m['of']:<3} {pct:>6}")
        if self.missing:
            lines.append("")
            lines.append("Нет в импорте (есть в эталоне): " + ", ".join(
                f"{s.key} ({s.type.value})" for s in self.missing))
        if self.extra:
            lines.append("Лишние в импорте (нет в эталоне): " + ", ".join(
                f"{s.key} ({s.type.value}: {_short(s)})" for s in self.extra))
        if self.issues:
            lines.append("")
            lines.append("Расхождения:")
            lines += [f"  • {x}" for x in self.issues]
        return "\n".join(lines)


def _short(s: Step) -> str:
    t = html_plain(s.content.tg.text) or s.title
    t = " ".join(t.split())
    return t[:40] + ("…" if len(t) > 40 else "")


def match_steps(ref: Funnel, imp: Funnel) -> tuple[list[tuple[Step, Step, float]], list[Step], list[Step]]:
    def txt(s: Step) -> str:
        return "" if s.type.value == "video_note" else norm_text(s.content.tg.text)
    rn = {s.id: txt(s) for s in ref.steps}
    im = {s.id: txt(s) for s in imp.steps}
    cands = []
    for i, r in enumerate(ref.steps):
        for j, x in enumerate(imp.steps):
            a, b = rn[r.id], im[x.id]
            if not a or not b:
                continue
            sim = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
            if sim < 0.45:
                continue
            score = sim + (0.15 if r.type == x.type else 0) + (0.05 if bool(r.media.image) == bool(x.media.image) else 0)
            # одинаковые тексты эфира и повтора (пятидневка) – различаются днём и якорем
            if (r.schedule.anchor, r.schedule.day_offset) == (x.schedule.anchor, x.schedule.day_offset):
                score += 0.1
            cands.append((score, sim, i, j))
    cands.sort(reverse=True)
    used_r, used_i = set(), set()
    pairs = []
    for score, sim, i, j in cands:
        if i in used_r or j in used_i:
            continue
        used_r.add(i)
        used_i.add(j)
        pairs.append((ref.steps[i], imp.steps[j], sim))
    # шаги без текста (кружки, закрепы) – по порядку внутри типа
    for t in {s.type for s in ref.steps if not rn[s.id]}:
        rs = [i for i, s in enumerate(ref.steps) if s.type == t and not rn[s.id] and i not in used_r]
        xs = [j for j, s in enumerate(imp.steps) if s.type == t and not im[s.id] and j not in used_i]
        for i, j in zip(rs, xs):
            used_r.add(i)
            used_i.add(j)
            pairs.append((ref.steps[i], imp.steps[j], 1.0))
    order = {s.id: n for n, s in enumerate(ref.steps)}
    pairs.sort(key=lambda p: order[p[0].id])
    missing = [s for i, s in enumerate(ref.steps) if i not in used_r]
    extra = [s for j, s in enumerate(imp.steps) if j not in used_i]
    return pairs, missing, extra


def compare(ref: Funnel, imp: Funnel, ref_name: str = "") -> Compare:
    # надгробия движка (jump без текста – «шаг снят, таймер ведём дальше») – не с доски
    tomb = [s for s in ref.steps if s.type.value == "jump"]
    if tomb:
        ids = {s.id for s in tomb}
        ref = ref.model_copy(update={"steps": [s for s in ref.steps if s.id not in ids],
                                     "edges": [e for e in ref.edges if e.from_step not in ids and e.to_step not in ids]})
    pairs, missing, extra = match_steps(ref, imp)
    c = Compare(ref_name=ref_name, pairs=pairs, missing=missing, extra=extra)
    if tomb:
        c.issues.append(f"в эталоне {len(tomb)} надгробий (jump – снятые шаги движка) – не сравниваются: "
                        + ", ".join(s.key for s in tomb))
    M = c.metrics

    def m(name: str, ok: int, of: int) -> None:
        M[name] = {"ok": ok, "of": of}

    m("шаги найдены", len(pairs), len(ref.steps))
    m("лишних шагов нет", len(imp.steps) - len(extra), len(imp.steps))
    ok_type = [p for p in pairs if p[0].type == p[1].type]
    m("тип шага", len(ok_type), len(pairs))
    for r, x, _ in pairs:
        if r.type != x.type:
            c.issues.append(f"{r.key}: тип {r.type.value} в эталоне, {x.type.value} в импорте ({x.key})")
    # у кружка текст – сценарий спикера, в бот он не уходит: не сравниваем
    texted = [p for p in pairs if p[0].type.value != "video_note"
              and (norm_text(p[0].content.tg.text) or norm_text(p[1].content.tg.text))]
    exact = 0
    for r, x, sim in texted:
        a, b = norm_text(r.content.tg.text), norm_text(x.content.tg.text)
        if a.replace(" ", "") == b.replace(" ", ""):
            exact += 1
        else:
            c.issues.append(f"{r.key}: текст совпал на {100 * sim:.0f} % ({x.key}) – {_first_diff(a, b)}")
    m("текст (нормализованно)", exact, len(texted))
    m("текст ≥ 90 % похож", sum(1 for p in texted if p[2] >= 0.9), len(texted))
    img = sum(1 for r, x, _ in pairs if bool(r.media.image) == bool(x.media.image))
    m("картинка есть/нет", img, len(pairs))
    for r, x, _ in pairs:
        if bool(r.media.image) != bool(x.media.image):
            c.issues.append(f"{r.key}: картинка {'есть' if r.media.image else 'нет'} в эталоне, "
                            f"{'есть' if x.media.image else 'нет'} в импорте")
    vn = [(r, x) for r, x, _ in pairs if r.type.value == "video_note"]
    btn_ok = 0
    for r, x, _ in pairs:
        br, bx = _buttons(r), _buttons(x)
        if br == bx:
            btn_ok += 1
        else:
            c.issues.append(f"{r.key}: кнопки {br} ≠ {bx}")
    m("кнопки (подпись и цель)", btn_ok, len(pairs))
    cond_ok = 0
    for r, x, _ in pairs:
        a, b = sorted(r.conditions.active()), sorted(x.conditions.active())
        if a == b and r.conditions.channel == x.conditions.channel:
            cond_ok += 1
        else:
            c.issues.append(f"{r.key}: условия {a or '–'} в эталоне, {b or '–'} в импорте")
    m("условия (гейты)", cond_ok, len(pairs))
    timed = [(r, x) for r, x, _ in pairs if _sched(r) != ("по стрелке",) or _sched(x) != ("по стрелке",)]
    if timed:
        ok = 0
        for r, x in timed:
            if _sched(r) == _sched(x):
                ok += 1
            else:
                c.issues.append(f"{r.key}: расписание {_sched(r)} в эталоне, {_sched(x)} в импорте ({x.key})")
        m("расписание (часы / от эфира)", ok, len(timed))
    # стрелки
    r2x = {r.id: x.id for r, x, _ in pairs}
    key_r = {s.id: s.key for s in ref.steps}
    key_x = {s.id: s.key for s in imp.steps}
    ref_edges = [e for e in ref.edges if e.to_step in r2x and e.from_step in r2x]
    ends = kind_ok = delay_ok = 0
    for e in ref_edges:
        same = [x for x in imp.edges if x.from_step == r2x[e.from_step] and x.to_step == r2x[e.to_step]]
        if not same:
            c.issues.append(f"стрелки {key_r[e.from_step]} → {key_r[e.to_step]} ({e.kind.value}, "
                            f"{e.delay_seconds}) нет в импорте")
            continue
        ends += 1
        k = [x for x in same if x.kind == e.kind]
        if k:
            kind_ok += 1
            if any((x.delay_seconds or 0) == (e.delay_seconds or 0) for x in k):
                delay_ok += 1
            else:
                c.issues.append(f"стрелка {key_r[e.from_step]} → {key_r[e.to_step]} ({e.kind.value}): "
                                f"задержка {e.delay_seconds} в эталоне, {k[0].delay_seconds} в импорте")
        else:
            c.issues.append(f"стрелка {key_r[e.from_step]} → {key_r[e.to_step]}: вид {e.kind.value} в эталоне, "
                            f"{'/'.join(x.kind.value for x in same)} в импорте")
    m("стрелки: те же концы", ends, len(ref_edges))
    m("стрелки: тот же вид", kind_ok, len(ref_edges))
    m("стрелки: та же задержка", delay_ok, len(ref_edges))
    x2r = {v: k for k, v in r2x.items()}
    for e in imp.edges:
        if e.from_step in x2r and e.to_step in x2r:
            if not any(y.from_step == x2r[e.from_step] and y.to_step == x2r[e.to_step] for y in ref.edges):
                c.issues.append(f"лишняя стрелка в импорте {key_x[e.from_step]} → {key_x[e.to_step]} "
                                f"({e.kind.value}, {e.delay_seconds})")
    tr, tx = arrival_times(ref), arrival_times(imp)
    both = [(r, x) for r, x, _ in pairs if r.id in tr]
    t_ok = 0
    for r, x in both:
        a, b = tr[r.id], tx.get(x.id)
        if b is not None and abs(a - b) <= 1:
            t_ok += 1
        else:
            c.issues.append(f"{r.key}: придёт через {_hms(a)} от входа в эталоне, "
                            f"{'не придёт' if b is None else 'через ' + _hms(b)} в импорте")
    m("время от входа", t_ok, len(both))
    del vn
    return c


def _hms(sec: int) -> str:
    h, rem = divmod(int(sec), 3600)
    mi, s = divmod(rem, 60)
    out = []
    if h:
        out.append(f"{h} ч")
    if mi:
        out.append(f"{mi} мин")
    if s or not out:
        out.append(f"{s} с")
    return " ".join(out)


def _first_diff(a: str, b: str) -> str:
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        x, y = a[i1:i2][:70], b[j1:j2][:70]
        if tag == "delete":
            return f"в импорте нет: «{x}»"
        if tag == "insert":
            return f"лишнее в импорте: «{y}»"
        return f"«{x}» → «{y}»"
    return ""
