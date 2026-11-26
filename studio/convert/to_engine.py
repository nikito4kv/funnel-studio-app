"""Граф Студии → словарь yaml движка (и текст yaml).

Экспорт пишет только значения, отличные от умолчаний движка (умолчания берутся
из самой модели движка, а не переписываются руками). Порядок полей шага – как в
`FunnelStep`. Заметки, полосы, раскладка, роли и `source` в yaml не попадают
никогда: у движка для них нет полей, а у экспорта – кода, который бы их читал.
"""

from __future__ import annotations

import copy
import re
from typing import Any

import yaml

from ..engine_ref import loader as eng
from ..model.funnel import Button, CohortMatch, EdgeKind, Funnel, Step
from .from_engine import engine_default
from .mapping import BUTTON_FIELDS, COHORT_FIELDS, SINGLE_EDGE_FIELDS, STEP_FIELD_PATHS


class ExportError(ValueError):
    pass


def _get_path(obj: Any, path: tuple[str, ...]) -> Any:
    for p in path:
        obj = getattr(obj, p)
    return obj


def _button(b: Button) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in BUTTON_FIELDS:
        v = getattr(b, f)
        if f == "text" or v != eng.ButtonConfig.model_fields[f].default:
            out[f] = v
    return out


def _buttons(bs: list[Button] | None) -> list[dict] | None:
    return None if bs is None else [_button(b) for b in bs]


def _cohort(c: CohortMatch) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for engine, studio in COHORT_FIELDS.items():
        v = getattr(c, studio)
        if v != eng.CohortMatch.model_fields[engine].get_default(call_default_factory=True):
            out[engine] = copy.deepcopy(v)
    return out


def _plain(v: Any) -> Any:
    """pydantic/enum → чистые python-типы для yaml."""
    if hasattr(v, "value") and not isinstance(v, (str, int)):
        return v.value
    if isinstance(v, str) and hasattr(v, "value"):   # str-Enum
        return v.value
    return copy.deepcopy(v)


def _step_values(step: Step, f: Funnel, key_of) -> dict[str, Any]:
    vals: dict[str, Any] = {"key": step.key, "type": step.type.value}

    for field, path in STEP_FIELD_PATHS.items():
        v = _get_path(step, path)
        if field in ("buttons", "buttons_max"):
            v = _buttons(v)
        elif field == "variants":
            v = [{**_cohort(var.when),
                  **{k: val for k, val in (("text", var.text), ("text_max", var.text_max),
                                            ("buttons", _buttons(var.buttons)),
                                            ("claim_button_text", var.claim_button_text))
                     if val is not None}}
                 for var in v]
        else:
            v = _plain(v)
        vals[field] = v

    # ── стрелки ──
    seen: set[EdgeKind] = set()
    kind_to_field = {kind: (field, delay) for field, (kind, delay) in SINGLE_EDGE_FIELDS.items()}
    also: dict[str, int] = {}
    chain_delay = None
    for e in f.edges_from(step.id):
        if e.kind == EdgeKind.also_fire:
            k = key_of(e.to_step)
            if k in also:
                raise ExportError(f"{step.key}: две параллельные стрелки в {k}")
            also[k] = e.delay_seconds if e.delay_seconds is not None else 0
            continue
        if e.kind in seen:
            raise ExportError(f"{step.key}: две стрелки вида {e.kind.value}")
        seen.add(e.kind)
        if e.kind == EdgeKind.chain_to_funnel:
            vals["chain_to_funnel"] = e.to_funnel
            vals["typing_before_chain"] = e.typing_before
            chain_delay = e.delay_seconds if e.delay_seconds is not None else 0
            continue
        field, delay_field = kind_to_field[e.kind]
        vals[field] = key_of(e.to_step)
        if delay_field:
            d = e.delay_seconds
            if d is None:
                d = engine_default(delay_field)
            vals[delay_field] = d
    if also:
        vals["also_fire"] = also
    if chain_delay is not None:
        # у движка одна задержка на on_done и chain_to_funnel
        if EdgeKind.done in seen and vals["on_done_after_seconds"] != chain_delay:
            raise ExportError(f"{step.key}: у on_done и chain_to_funnel разные задержки – "
                              "движок хранит одну")
        vals["on_done_after_seconds"] = chain_delay

    for field, v in step.advanced.items():
        vals[field] = copy.deepcopy(v)

    # порядок и отсев умолчаний – по модели движка
    out: dict[str, Any] = {}
    for field in eng.FunnelStep.model_fields:
        if field not in vals:
            continue
        v = vals[field]
        if field in ("key", "type") or v != engine_default(field):
            out[field] = v
    unknown = set(vals) - set(eng.FunnelStep.model_fields)
    if unknown:
        raise ExportError(f"{step.key}: поля, которых нет в движке: {sorted(unknown)}")
    return out


def export_data(f: Funnel, *, compact: bool = False) -> dict[str, Any]:
    """Граф → словарь yaml.

    compact=True для воронки, импортированной из `extends`, пишет только
    `extends` и её собственные ключи (как было в исходнике). Годится, пока
    унаследованные разделы в Студии не правили; иначе – полная форма.
    """
    ids = {s.id: s.key for s in f.steps}
    for u in f.unresolved:
        ids.setdefault(u.id, u.key)

    def key_of(i) -> str:
        try:
            return ids[i]
        except KeyError:
            raise ExportError(f"ссылка на неизвестный шаг {i}") from None

    out: dict[str, Any] = {"id": f.id}
    if f.branch_router is not None:
        out["branch_router"] = {
            "type": f.branch_router.type,
            "branches": [{
                "id": b.id,
                "entry_step": key_of(b.entry_step),
                "webinar_time": b.webinar_time,
                **({"repeat_offset_minutes": b.repeat_offset_minutes}
                   if b.repeat_offset_minutes else {}),
                "windows": [{"from": w.from_, "to": w.to, "day": w.day,
                             **({"date": w.date} if w.date is not None else {})}
                            for w in b.windows],
            } for b in f.branch_router.branches],
        }
    if f.webinar_room is not None:
        out["webinar_room"] = {k: v for k, v in f.webinar_room.model_dump().items() if v}
    if f.entry_variants:
        out["entry_variants"] = [{**_cohort(ev.when), "entry_step": key_of(ev.entry_step)}
                                 for ev in f.entry_variants]
    if f.air_email_profile:
        out["air_email_profile"] = f.air_email_profile
    if not f.max_video_note_caption:
        out["max_video_note_caption"] = False
    if f.resend_entry_on_repeat:
        out["resend_entry_on_repeat"] = True
    out["steps"] = [_step_values(s, f, key_of) for s in f.steps]

    if compact and f.source and f.source.extends:
        own = {"extends": f.source.extends}
        for k in f.source.own_keys:
            if k in out:
                own[k] = out[k]
            elif k == "steps":
                own[k] = []
        return own
    return out


# ── yaml ─────────────────────────────────────────────────────────────────────

_QUOTE_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$|^\d{4}-\d{2}-\d{2}$")


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):  # без якорей &id001 – yaml читают люди
        return True


def _str(dumper: yaml.SafeDumper, s: str):
    if _QUOTE_RE.match(s):
        # "13:00" без кавычек YAML 1.1 читает как 780
        return dumper.represent_scalar("tag:yaml.org,2002:str", s, style='"')
    if "\n" in s:
        return dumper.represent_scalar("tag:yaml.org,2002:str", s, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", s)


_Dumper.add_representer(str, _str)


def dump_yaml(data: dict[str, Any]) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True,
                     width=10_000, default_flow_style=False, indent=2)


def studio_only_notes(f: Funnel) -> list[str]:
    """Что из воронки в yaml bot-engine НЕ уйдёт: поля, которых у его движка нет
    (mapping.STUDIO_ONLY_PATHS). Работают только в движке Студии."""
    from .mapping import STUDIO_ONLY_PATHS
    out = []
    for path, what in STUDIO_ONLY_PATHS.items():
        keys = []
        for s in f.steps:
            v = _get_path(s, path)
            default = type(_get_path(s, path[:-1])).model_fields[path[-1]].default
            if v != default:
                keys.append(s.key)
        if keys:
            names = ", ".join(f"«{k}»" for k in keys[:6]) + (f" и ещё {len(keys) - 6}" if len(keys) > 6 else "")
            out.append(f"{what} у шагов {names} есть только в движке Студии – "
                       f"в yaml bot-engine не выгружается, там шаг уйдёт без этого условия")
    return out


def export_yaml(f: Funnel, *, compact: bool = False) -> str:
    return dump_yaml(export_data(f, compact=compact))
