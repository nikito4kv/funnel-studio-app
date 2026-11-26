"""yaml движка → граф Студии."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from ..engine_ref import loader as eng
from ..ids import edge_id, step_id
from ..model.funnel import (
    Branch, BranchRouter, BranchWindow, Button, CohortMatch, Edge, EdgeKind,
    EntryVariant, Funnel, SourceInfo, Step, StepVariant, UnresolvedRef, WebinarRoom,
)
from .mapping import (
    BUTTON_FIELDS, COHORT_FIELDS, DEAD_FIELDS, DELAY_FIELDS, EDGE_SPECIAL_FIELDS,
    IDENTITY_FIELDS, SINGLE_EDGE_FIELDS, STEP_FIELD_PATHS,
)


def engine_default(field: str) -> Any:
    return eng.FunnelStep.model_fields[field].get_default(call_default_factory=True)


def _button(b: eng.ButtonConfig) -> Button:
    return Button(**{f: getattr(b, f) for f in BUTTON_FIELDS})


def _buttons(bs: list[eng.ButtonConfig] | None) -> list[Button] | None:
    return None if bs is None else [_button(b) for b in bs]


def _cohort(c: eng.CohortMatch) -> CohortMatch:
    return CohortMatch(**{studio: copy.deepcopy(getattr(c, engine))
                          for engine, studio in COHORT_FIELDS.items()})


def _set_path(tree: dict, path: tuple[str, ...], value: Any) -> None:
    node = tree
    for p in path[:-1]:
        node = node.setdefault(p, {})
    node[path[-1]] = value


class _Refs:
    """Ключ шага → id; ссылки на несуществующие шаги копятся в unresolved."""

    def __init__(self, launch: str, funnel_id: str, keys: list[str]):
        self.launch, self.funnel_id = launch, funnel_id
        self.first: dict[str, Any] = {}
        self.step_ids: list[Any] = []
        seen: dict[str, int] = {}
        for k in keys:
            seen[k] = seen.get(k, 0) + 1
            sid = step_id(launch, funnel_id, k, seen[k])
            self.step_ids.append(sid)
            self.first.setdefault(k, sid)
        self.unresolved: dict[str, Any] = {}

    def __call__(self, key: str):
        if key in self.first:
            return self.first[key]
        if key not in self.unresolved:
            self.unresolved[key] = step_id(self.launch, self.funnel_id, key)
        return self.unresolved[key]


def _step(s: eng.FunnelStep, sid, refs: _Refs, edges: list[Edge]) -> Step:
    tree: dict[str, Any] = {}
    advanced: dict[str, Any] = {}

    for field, path in STEP_FIELD_PATHS.items():
        val = getattr(s, field)
        if field in ("buttons", "buttons_max"):
            val = _buttons(val)
        elif field == "variants":
            val = [StepVariant(when=_cohort(v),
                               text=v.text, text_max=v.text_max,
                               buttons=_buttons(v.buttons),
                               claim_button_text=v.claim_button_text) for v in val]
        else:
            val = copy.deepcopy(val)
        _set_path(tree, path, val)

    if s.parse_mode != engine_default("parse_mode"):
        advanced["parse_mode"] = s.parse_mode

    # ── стрелки ──
    def add(kind: EdgeKind, target_key: str | None = None, *, funnel: str | None = None,
            delay: int | None = None, index: int = 0, typing: bool = False):
        to = refs(target_key) if target_key is not None else None
        edges.append(Edge(
            id=edge_id(sid, kind.value, target_key if funnel is None else f"funnel:{funnel}", index),
            from_step=sid, kind=kind, to_step=to, to_funnel=funnel,
            delay_seconds=delay, typing_before=typing,
        ))

    for field, (kind, delay_field) in SINGLE_EDGE_FIELDS.items():
        target = getattr(s, field)
        delay = getattr(s, delay_field) if delay_field else None
        if target:
            add(kind, target, delay=delay)
        elif delay_field and field != "on_done" and delay != engine_default(delay_field):
            # задержка без цели – законного места нет
            advanced[delay_field] = delay

    if s.chain_to_funnel:
        add(EdgeKind.chain_to_funnel, funnel=s.chain_to_funnel,
            delay=s.on_done_after_seconds, typing=s.typing_before_chain)
    else:
        if s.typing_before_chain:
            advanced["typing_before_chain"] = True
    if not s.on_done and not s.chain_to_funnel and s.on_done_after_seconds != 0:
        advanced["on_done_after_seconds"] = s.on_done_after_seconds

    for i, (k, sec) in enumerate(s.also_fire.items()):
        add(EdgeKind.also_fire, k, delay=sec, index=i)

    # страховка: поле движка, про которое модель не знает
    known = (set(STEP_FIELD_PATHS) | set(SINGLE_EDGE_FIELDS) | DELAY_FIELDS
             | EDGE_SPECIAL_FIELDS | IDENTITY_FIELDS | DEAD_FIELDS)
    for field in type(s).model_fields:
        if field not in known:
            val = getattr(s, field)
            if val != engine_default(field):
                advanced[field] = copy.deepcopy(val)

    return Step(id=sid, key=s.key, type=s.type, advanced=advanced, **tree)


def import_data(data: dict[str, Any], launch: str, *,
                source: SourceInfo | None = None) -> Funnel:
    """Словарь yaml воронки (с уже раскрытым extends) → граф Студии.

    Сначала прогоняем данные через модель движка: так импорт принимает ровно
    то, что принимает бот, и получает те же значения по умолчанию. Разворот
    `webinar_room` и `max_video_note_caption` НЕ делаем – это настройки
    воронки, они остаются настройками и применяются движком при загрузке.
    """
    cfg = eng.FunnelConfig(**copy.deepcopy(data))
    refs = _Refs(launch, cfg.id, [s.key for s in cfg.steps])

    edges: list[Edge] = []
    steps = [_step(s, sid, refs, edges) for s, sid in zip(cfg.steps, refs.step_ids)]

    router = None
    if cfg.branch_router is not None:
        router = BranchRouter(type=cfg.branch_router.type, branches=[
            Branch(id=b.id, entry_step=refs(b.entry_step), webinar_time=b.webinar_time,
                   repeat_offset_minutes=b.repeat_offset_minutes,
                   windows=[BranchWindow(from_=w.from_, to=w.to, day=w.day, date=w.date)
                            for w in b.windows])
            for b in cfg.branch_router.branches])

    entry_variants = [EntryVariant(when=_cohort(ev), entry_step=refs(ev.entry_step))
                      for ev in cfg.entry_variants]

    room = None
    if cfg.webinar_room is not None:
        room = WebinarRoom(**cfg.webinar_room.model_dump())

    return Funnel(
        id=cfg.id, source=source,
        branch_router=router, entry_variants=entry_variants, webinar_room=room,
        air_email_profile=cfg.air_email_profile,
        max_video_note_caption=cfg.max_video_note_caption,
        resend_entry_on_repeat=cfg.resend_entry_on_repeat,
        steps=steps, edges=edges,
        unresolved=[UnresolvedRef(id=i, key=k) for k, i in refs.unresolved.items()],
    )


def import_file(path: str | Path, *, repo_root: str | Path | None = None,
                launch: str | None = None) -> Funnel:
    """Файл воронки → граф. `extends` разворачивается так же, как в движке
    (`_read_funnel_data`), но в `source` запоминаем родителя и свои ключи."""
    p = Path(path)
    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    data = eng._read_funnel_data(p)
    launch = launch or p.parent.name
    rel = str(p)
    if repo_root is not None:
        try:
            rel = p.resolve().relative_to(Path(repo_root).resolve()).as_posix()
        except ValueError:
            pass
    src = SourceInfo(launch=launch, path=rel,
                     extends=raw.get("extends"),
                     own_keys=[k for k in raw if k != "extends"] if raw.get("extends") else [])
    return import_data(data, launch, source=src)
