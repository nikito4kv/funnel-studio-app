"""Повторный импорт той же доски: обновить черновик, не потеряв ручные правки.

Три версии каждого поля, которое даёт доска (`build.BOARD_PATHS`):
  * база – как шаг выглядел на доске при прошлом импорте (`Step.miro.board`);
  * «моё» – что сейчас в черновике Студии;
  * «доска» – что пришло сейчас.

  моё == база            → берём доску (разработчик поле не трогал);
  доска == база          → оставляем моё (доска не менялась, поле правил разработчик);
  моё == доска           → ничего не меняется;
  иначе                  → берём доску и пишем сомнение «поменяли и там, и там».

Поля, которых на доске нет (бот, раскладка, заметки, ролик кружка, текст MAX, key,
расширенные…), не трогаются вовсе. Шаги сопоставляются по элементу Miro: новые на доске
– добавляются, исчезнувшие с доски – убираются (если на удалённом шаге стоят люди,
это ловит публикация – «исчез без надгробия»), шаги, созданные в Студии руками, остаются.
Стрелки – так же, по базе `MiroSource.edges_base`.
"""
from __future__ import annotations

import uuid
from typing import Any

from ..model.funnel import Edge, EdgeKind, Funnel, MiroDoubt, Position, Step
from .build import BOARD_PATHS

_MISSING = object()


def _get(d: dict, path: tuple[str, ...]) -> Any:
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _set(d: dict, path: tuple[str, ...], value: Any) -> None:
    cur = d
    for p in path[:-1]:
        cur = cur.setdefault(p, {})
    cur[path[-1]] = value


def _short(v: Any) -> str:
    s = str(v if v is not None else "–")
    s = " ".join(s.split())
    return s if len(s) <= 60 else s[:59] + "…"


FIELD_LABEL = {
    "type": "тип", "title": "название", "content.tg.text": "текст", "content.tg.buttons": "кнопки",
    "content.tg.claim_button_text": "кнопка-действие", "media.image": "картинка",
    "conditions.channel": "условие «канал»", "conditions.not_paid": "условие «не оплатил ВИП»",
    "conditions.paid": "условие «оплатил ВИП»", "conditions.survey_done": "условие «опрос»",
    "contact.contact_button": "кнопка контакта", "contact.skip_if_known": "«телефон уже известен»",
    "email.skip_if_known": "«почта уже известна»", "survey.qid": "вопрос опроса",
    "survey.multi": "несколько ответов", "survey.final": "последний вопрос",
    "survey.done_button": "кнопка «Готово»", "survey.edit": "правка сообщения опроса",
}


def merge_reimport(current: Funnel, fresh: Funnel) -> tuple[Funnel, dict]:
    report: dict[str, list] = {"added": [], "removed": [], "updated": [], "kept": [], "conflicts": [],
                               "edges_added": [], "edges_removed": []}
    cur_by_item = {s.miro.item_id: s for s in current.steps if s.miro}
    fresh_items = {s.miro.item_id for s in fresh.steps if s.miro}
    # key убранного шага новому не отдаём: в движке таймеры людей привязаны к имени шага
    keys_used = {s.key for s in current.steps}

    def free_key(k: str) -> str:
        if k not in keys_used:
            return k
        n = 1
        while f"{k}_{n}" in keys_used:
            n += 1
        return f"{k}_{n}"

    new_steps: list[Step] = []
    id_map: dict[uuid.UUID, uuid.UUID] = {}          # id шага свежего импорта → id в черновике
    for f in fresh.steps:
        item = f.miro.item_id if f.miro else None
        c = cur_by_item.get(item) if item else None
        if c is None:
            k = free_key(f.key)
            keys_used.add(k)
            s = f.model_copy(update={"key": k}, deep=True)
            new_steps.append(s)
            id_map[f.id] = s.id
            report["added"].append(k)
            continue
        cd = c.model_dump(mode="json", by_alias=True)
        fd = f.model_dump(mode="json", by_alias=True)
        base = (c.miro.board if c.miro else {}) or {}
        extra_doubts: list[MiroDoubt] = []
        changed = []
        for path in BOARD_PATHS:
            key = ".".join(path)
            mine, theirs = _get(cd, path), _get(fd, path)
            b = base.get(key, _MISSING)
            if mine == theirs:
                continue
            if b is _MISSING or mine == b:
                _set(cd, path, theirs)
                changed.append(key)
            elif theirs == b:
                report["kept"].append({"key": c.key, "field": key})
            else:
                _set(cd, path, theirs)
                changed.append(key)
                report["conflicts"].append({"key": c.key, "field": key})
                extra_doubts.append(MiroDoubt(
                    code="merge_conflict", item_id=item,
                    text=f"{FIELD_LABEL.get(key, key)}: поменяли и на доске, и в Студии – взято с доски; "
                         f"в Студии было: {_short(mine)}"))
        miro = f.miro.model_copy(deep=True) if f.miro else None
        if miro is not None:
            gone = set(c.miro.dismissed) if c.miro else set()
            miro.dismissed = sorted(gone)
            miro.doubts = [d for d in miro.doubts + extra_doubts if doubt_key(d) not in gone]
        cd["miro"] = miro.model_dump(mode="json") if miro else None
        s = Step.model_validate(cd)
        new_steps.append(s)
        id_map[f.id] = s.id
        if changed:
            report["updated"].append({"key": s.key, "fields": changed})
    manual = [s for s in current.steps if not s.miro]
    removed = [s for s in current.steps if s.miro and s.miro.item_id not in fresh_items]
    report["removed"] = [s.key for s in removed]
    removed_ids = {s.id for s in removed}

    # вход: не трогали – как на доске, иначе – как в Студии
    cur_first = current.steps[0] if current.steps else None
    base_entry = current.miro.entry_item if current.miro else None
    steps = new_steps + manual
    if cur_first is not None and not (cur_first.miro and cur_first.miro.item_id == base_entry) \
            and cur_first.id not in removed_ids:
        steps = [x for x in steps if x.id != cur_first.id]
        steps.insert(0, next(x for x in new_steps + manual if x.id == cur_first.id))
    all_ids = {s.id for s in steps}

    # ── стрелки ──
    item_of = {s.id: (s.miro.item_id if s.miro else f"id:{s.id}") for s in current.steps}
    item_of.update({s.id: (s.miro.item_id if s.miro else f"id:{s.id}") for s in steps})
    by_item = {v: k for k, v in item_of.items() if k in all_ids}

    def ekey_cur(e: Edge) -> tuple:
        return (item_of.get(e.from_step), e.kind.value,
                item_of.get(e.to_step) if e.to_step else f"funnel:{e.to_funnel}")

    fresh_item = {s.id: s.miro.item_id for s in fresh.steps if s.miro}

    def ekey_fresh(e: Edge) -> tuple:
        return (fresh_item.get(e.from_step), e.kind.value,
                fresh_item.get(e.to_step) if e.to_step else f"funnel:{e.to_funnel}")

    base_edges = {(b["from"], b["kind"], b["to"]): b.get("delay")
                  for b in (current.miro.edges_base if current.miro else [])}
    cur_edges = {ekey_cur(e): e for e in current.edges}
    out: list[Edge] = []
    seen: set[tuple] = set()
    for fe in fresh.edges:
        k = ekey_fresh(fe)
        if k in seen:
            continue
        seen.add(k)
        ce = cur_edges.get(k)
        if ce is not None:
            bd = base_edges.get(k, _MISSING)
            keep_mine = bd is not _MISSING and ce.delay_seconds != bd and fe.delay_seconds == bd
            out.append(ce if keep_mine else ce.model_copy(update={"delay_seconds": fe.delay_seconds}))
        elif k in base_edges and base_edges[k] == fe.delay_seconds:
            continue                     # разработчик убрал стрелку, на доске она не менялась
        else:
            frm, to = by_item.get(k[0]), by_item.get(k[2]) if fe.to_step else None
            if frm is None or (fe.to_step and to is None):
                continue
            out.append(fe.model_copy(update={"from_step": frm, "to_step": to}))
            report["edges_added"].append(f"{k[1]}")
    for k, ce in cur_edges.items():
        if k in seen:
            continue
        if k in base_edges:
            report["edges_removed"].append(k[1])
            continue                     # была на доске, с доски убрали
        if ce.from_step in all_ids and (ce.to_step is None or ce.to_step in all_ids):
            out.append(ce)               # проведена в Студии руками
    # у движка одна стрелка каждого вида из шага (кроме параллельных)
    uniq: dict[tuple, Edge] = {}
    for e in out:
        kk = (e.from_step, e.kind.value) if e.kind not in (EdgeKind.also_fire,) else (e.from_step, e.kind.value, e.to_step)
        uniq.setdefault(kk, e)
    edges = list(uniq.values())

    # ── раскладка, полосы, заметки ──
    layout = current.layout.model_copy(deep=True)
    for rid in removed_ids:
        layout.positions.pop(rid, None)
    if layout.positions:
        right = max(p.x for p in layout.positions.values())
        k = 0
        for s in steps:
            if s.id in layout.positions:
                continue
            pred = next((e.from_step for e in edges if e.to_step == s.id and e.from_step in layout.positions), None)
            if s.id in fresh.layout.positions:
                # место на доске известно – туда же (раскладка импорта повторяет доску)
                layout.positions[s.id] = fresh.layout.positions[s.id]
            elif pred is not None:
                p = layout.positions[pred]
                layout.positions[s.id] = Position(x=p.x + 380, y=p.y + 40 * k)
            else:
                layout.positions[s.id] = Position(x=right + 400, y=40 * k)
            k += 1
    if not current.lanes and fresh.lanes:
        # полосы дней появились на доске – берём их вместе с местами
        for ln in fresh.lanes:
            if ln.id in fresh.layout.positions:
                layout.positions[ln.id] = fresh.layout.positions[ln.id]
    lanes = []
    for ln in (current.lanes or fresh.lanes):
        ids = [i for i in ln.step_ids if i not in removed_ids]
        lanes.append(ln.model_copy(update={"step_ids": ids}))
    if lanes:
        for s in steps:
            if any(s.id in ln.step_ids for ln in lanes):
                continue
            pred = next((e.from_step for e in edges if e.to_step == s.id), None)
            for ln in lanes:
                if pred is not None and pred in ln.step_ids:
                    ln.step_ids.append(s.id)
                    break
    fresh_notes = {n.id: n for n in fresh.notes}
    notes = []
    for n in current.notes:
        if n.step_id in removed_ids and n.author == "Miro":
            continue
        notes.append(fresh_notes.pop(n.id) if n.id in fresh_notes and n.author == "Miro" else n)
    notes += [n for n in fresh_notes.values() if n.step_id in all_ids or (n.step_id is None and n.position)]

    router = current.branch_router or fresh.branch_router
    if router is not None and steps:
        router = router.model_copy(deep=True)
        for b in router.branches:
            if b.entry_step not in all_ids:
                b.entry_step = steps[0].id
    variants = [v for v in current.entry_variants if v.entry_step in all_ids]

    fmiro = fresh.miro.model_copy(deep=True) if fresh.miro else None
    if fmiro is not None and current.miro is not None:
        fmiro.dismissed = list(current.miro.dismissed)
        fmiro.doubts = [d for d in fmiro.doubts if doubt_key(d) not in set(fmiro.dismissed)]
    merged = current.model_copy(update={
        "steps": steps, "edges": edges, "layout": layout, "lanes": lanes, "notes": notes,
        "branch_router": router, "entry_variants": variants, "miro": fmiro,
        "unresolved": [u for u in current.unresolved],
    }, deep=True)
    merged = Funnel.model_validate(merged.model_dump(mode="json", by_alias=True))
    return merged, report


def doubt_key(d: MiroDoubt) -> str:
    """Ключ сомнения для «проверено»: вид + текст (текст включает то, что именно не понято)."""
    return f"{d.code}:{d.text}"


def report_text(report: dict) -> list[str]:
    out = []
    if report["added"]:
        out.append(f"добавлено сообщений: {len(report['added'])} ({', '.join(report['added'])})")
    if report["removed"]:
        out.append(f"убрано с доски: {len(report['removed'])} ({', '.join(report['removed'])})")
    if report["updated"]:
        out.append(f"обновлено с доски: {len(report['updated'])}")
    if report["kept"]:
        out.append("ручные правки сохранены: " + ", ".join(
            f"{x['key']} – {FIELD_LABEL.get(x['field'], x['field'])}" for x in report["kept"]))
    if report["conflicts"]:
        out.append("поменяли и на доске, и в Студии (взята доска): " + ", ".join(
            f"{x['key']} – {FIELD_LABEL.get(x['field'], x['field'])}" for x in report["conflicts"]))
    return out
