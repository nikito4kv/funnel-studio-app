"""Выгрузка yaml и круговая проверка движком.

1. граф → yaml (`export_yaml`);
2. yaml → `load_funnel` движка: читается ли вообще;
3. если воронка импортирована из bot-engine – `load_funnel(оригинал)` против
   `load_funnel(выгрузка)`: список отличий по шагам и полям.

Сверка с оригиналом – справочная: оригинал лежит в bot-engine, который сервер Студии
тянет свежим при каждой выкатке, а копия модели движка в Студии обновляется руками.
Не прочитался оригинал (в bot-engine новые поля) – это не ошибка выгрузки: `compare`
получает `error` с понятным текстом, исключение наружу не уходит.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..convert import ExportError, export_yaml
from ..convert.to_engine import studio_only_notes
from ..engine_ref.drift import engine_drift
from ..engine_ref.loader import load_funnel
from ..model.funnel import Funnel

MAX_DIFFS = 200


def _short(v: Any, limit: int = 160) -> Any:
    if isinstance(v, str) and len(v) > limit:
        return v[:limit] + "…"
    return v


def compare_dumps(orig: dict, new: dict) -> list[dict]:
    """model_dump() двух FunnelConfig → отличия [{step, field, original, exported, text}]."""
    diffs: list[dict] = []
    for k in orig:
        if k == "steps":
            continue
        if orig.get(k) != new.get(k):
            diffs.append({"step": None, "field": k, "original": orig.get(k), "exported": new.get(k),
                          "text": f"настройка воронки «{k}» отличается"})
    o_steps = orig.get("steps", [])
    n_steps = new.get("steps", [])
    o_by = {}
    for s in o_steps:
        o_by.setdefault(s["key"], s)
    n_by = {}
    for s in n_steps:
        n_by.setdefault(s["key"], s)
    if o_steps and n_steps and o_steps[0]["key"] != n_steps[0]["key"]:
        diffs.append({"step": n_steps[0]["key"], "field": "(порядок)",
                      "original": o_steps[0]["key"], "exported": n_steps[0]["key"],
                      "text": f"первый шаг воронки: был «{o_steps[0]['key']}», стал «{n_steps[0]['key']}»"})
    for k in o_by:
        if k not in n_by:
            diffs.append({"step": k, "field": None, "original": None, "exported": None,
                          "text": f"шаг «{k}» есть в оригинале, в выгрузке его нет"})
    for k in n_by:
        if k not in o_by:
            diffs.append({"step": k, "field": None, "original": None, "exported": None,
                          "text": f"шаг «{k}» новый – в оригинале его нет"})
    for k, os_ in o_by.items():
        ns = n_by.get(k)
        if ns is None:
            continue
        for field in os_:
            if os_[field] != ns.get(field):
                diffs.append({"step": k, "field": field,
                              "original": _short(os_[field]), "exported": _short(ns.get(field)),
                              "text": f"шаг «{k}»: поле {field} отличается"})
    if [s["key"] for s in o_steps] != [s["key"] for s in n_steps] and not any(
            d["field"] is None for d in diffs):
        diffs.append({"step": None, "field": "(порядок)", "original": None, "exported": None,
                      "text": "порядок шагов в файле изменился (на поведение движка влияет только первый шаг)"})
    return diffs[:MAX_DIFFS]


def original_unreadable(e: Exception, repo_root: Path) -> str:
    """Почему не вышло сверить с оригиналом – для человека."""
    d = engine_drift(repo_root)
    if d.get("same") is False:
        return (f"Студия не смогла сверить воронку с bot-engine: {d['text'][:1].lower()}{d['text'][1:]}. "
                "На запуск в боте Студии это не влияет – обновите копию движка (scripts/vendor_engine.py)")
    msg = str(e).splitlines()
    return (f"Студия не смогла прочитать исходный файл bot-engine своей копией движка: {type(e).__name__}: "
            f"{' '.join(x.strip() for x in msg[:3])}")


def export_and_check(f: Funnel, repo_root: Path, *, compact: bool = False) -> dict:
    res: dict[str, Any] = {"ok": False, "yaml": "", "export_error": None,
                           "engine": {"ok": False, "error": None, "steps": 0},
                           "compare": None, "studio_only": studio_only_notes(f)}
    try:
        text = export_yaml(f, compact=compact)
    except ExportError as e:
        res["export_error"] = str(e)
        return res
    res["yaml"] = text

    with tempfile.TemporaryDirectory(prefix="studio-export-") as tmp:
        # компактная форма ссылается на родителя относительным путём – кладём
        # файл рядом с оригиналом нельзя (bot-engine только читаем), поэтому для
        # проверки компактной формы подставляем абсолютный путь родителя.
        out = Path(tmp) / "funnel.yaml"
        check_text = text
        orig_path = None
        if f.source and f.source.path:
            p = Path(f.source.path)
            orig_path = p if p.is_absolute() else repo_root / p
        if compact and f.source and f.source.extends and orig_path is not None:
            import yaml
            data = yaml.safe_load(text)
            data["extends"] = str((orig_path.parent / data["extends"]).resolve())
            check_text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        out.write_text(check_text, encoding="utf-8")
        try:
            new_cfg = load_funnel(out)
        except Exception as e:  # noqa: BLE001 – показываем человеку как есть
            res["engine"] = {"ok": False, "error": f"{type(e).__name__}: {e}", "steps": 0}
            return res
        res["engine"] = {"ok": True, "error": None, "steps": len(new_cfg.steps)}

        if orig_path is not None and orig_path.exists():
            rel = f.source.path
            try:
                orig_cfg = load_funnel(orig_path)
            except Exception as e:  # noqa: BLE001 – оригинал не читается нашей копией движка
                res["compare"] = {"path": rel, "same": None, "differences": [],
                                  "error": original_unreadable(e, repo_root)}
            else:
                diffs = compare_dumps(orig_cfg.model_dump(), new_cfg.model_dump())
                res["compare"] = {"path": rel, "same": not diffs, "differences": diffs}
    res["ok"] = True
    return res
