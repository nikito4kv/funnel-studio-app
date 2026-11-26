"""Граф Студии → конфиг движка → прогон; матрица веток.

Симулятор работает не над графом, а над тем, что увидит бот: граф
выгружается в yaml (тот же экспорт, что «Выгрузить yaml») и читается
`load_funnel` движка. Так прогон видит воронку ровно так же, как бот –
с развёрнутым залом воронки и кружками MAX без подписи.
"""
from __future__ import annotations

import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..convert.to_engine import export_yaml
from ..engine_ref.loader import FunnelConfig, load_funnel
from ..model.funnel import Funnel
from .launch import LaunchSettings, load_launch_settings
from .models import Persona, SimOptions
from .simulator import simulate

# «Типовые» моменты регистрации для матрицы (МСК).
MATRIX_TIMES = ["09:00", "12:30", "17:30", "23:30"]


def engine_config(f: Funnel) -> FunnelConfig:
    text = export_yaml(f)
    with tempfile.TemporaryDirectory(prefix="studio-sim-") as tmp:
        p = Path(tmp) / "funnel.yaml"
        p.write_text(text, encoding="utf-8")
        return load_funnel(p)


def key_ids(f: Funnel) -> dict[str, str]:
    return {s.key: str(s.id) for s in f.steps}


def link_clicks(f: Funnel) -> dict[str, str]:
    """Гейт «кликнул ссылку» по шагам – поле Студии, в yaml движка его нет."""
    return {s.key: s.conditions.link_click for s in f.steps if s.conditions.link_click}


def launch_for(f: Funnel, repo_root: Path | None) -> LaunchSettings:
    return load_launch_settings(repo_root, f.source.launch if f.source else None)


def simulate_graph(f: Funnel, opts: SimOptions, repo_root: Path | None = None,
                   launch: LaunchSettings | None = None) -> dict[str, Any]:
    cfg = engine_config(f)
    return simulate(cfg, opts, launch or launch_for(f, repo_root), key_ids(f), link_clicks(f))


def _hm_minus(hm: str, minutes: int) -> str:
    h, m = map(int, hm.split(":"))
    t = (h * 60 + m - minutes) % (24 * 60)
    return f"{t // 60:02d}:{t % 60:02d}"


def matrix_moments(cfg: FunnelConfig, base: date) -> list[dict]:
    """Моменты регистрации: 09:00, 12:30, 17:30, 23:30 + конец каждого окна веток
    (поздняя регистрация теряет больше всех); для фикс-даты – за 3 дня и в день эфира."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(d: date, hm: str, label: str) -> None:
        k = f"{d.isoformat()}T{hm}"
        if k not in seen:
            seen.add(k)
            out.append({"registered_at": k, "label": label})

    for hm in MATRIX_TIMES:
        add(base, hm, f"рег в {hm}")
    router = cfg.branch_router
    fixed: set[str] = set()
    if router:
        for b in router.branches:
            for w in b.windows:
                if w.day == "fixed" and w.date:
                    fixed.add(w.date)
                elif w.to not in ("23:59",):
                    add(base, w.to, f"рег в {w.to} – конец окна ветки {b.id}")
    for ds in sorted(fixed):
        try:
            d = date.fromisoformat(ds)
        except ValueError:
            continue
        for wt in sorted({b.webinar_time for b in router.branches}):  # type: ignore[union-attr]
            add(d - timedelta(days=3), "12:00", f"за 3 дня до эфира {d.strftime('%d.%m')}")
            add(d, "09:00", f"в день эфира {d.strftime('%d.%m')}, утром")
            add(d, _hm_minus(wt, 30), f"в день эфира, за 30 мин до {wt}")
    out.sort(key=lambda m: m["registered_at"])
    return out


def matrix(f: Funnel, base: date, platform: str = "tg", persona: Persona | None = None,
           repo_root: Path | None = None, dnd: bool | None = None) -> dict[str, Any]:
    cfg = engine_config(f)
    launch = launch_for(f, repo_root)
    ids = key_ids(f)
    clicks = link_clicks(f)
    rows = []
    for m in matrix_moments(cfg, base):
        opts = SimOptions(registered_at=m["registered_at"], platform=platform,  # type: ignore[arg-type]
                          persona=persona or Persona(), dnd=dnd)
        res = simulate(cfg, opts, launch, ids, clicks)
        s = res["summary"]
        br = res["branch"] or {}
        rows.append({
            **m,
            "branch": br.get("id"),
            "webinar": br.get("webinar_label", ""),
            "sent": s["sent"],
            "late": s["late"],
            "dropped": s["dropped"],
            "gated": len(s["gated"]),
            "stuck": s["stuck"],
            "truncated": s["truncated"],
        })
    # шаг выпадает у части моментов – сводка «кто где теряет»
    by_step: dict[str, dict] = {}
    for r in rows:
        for d in r["dropped"]:
            x = by_step.setdefault(d["key"], {"key": d["key"], "step_id": d["step_id"], "moments": [],
                                              "reasons": set()})
            if r["label"] not in x["moments"]:
                x["moments"].append(r["label"])
            x["reasons"].add(d["reason"])
    steps = [{**v, "reasons": sorted(v["reasons"])} for v in by_step.values()]
    steps.sort(key=lambda v: (-len(v["moments"]), v["key"]))
    return {"ok": True, "platform": platform, "rows": rows, "steps": steps,
            "launch": {**launch.model_dump(), "notes": launch.notes()}}


def today_msk() -> date:
    import pytz
    return datetime.now(pytz.timezone("Europe/Moscow")).date()
