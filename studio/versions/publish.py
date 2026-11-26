"""Публикация черновика: блокеры, выгрузка в data/exports, запись версии.

На этапе 2 публикация НЕ трогает bot-engine: она фиксирует неизменяемую версию и
складывает выгрузку рядом – `data/exports/<запуск>/<воронка>/v<N>/`:
    funnel.yaml   что прочитает движок
    check.json    отчёт круговой проверки движком + сводка «было/стало»
    static/       загруженные в Студию файлы, на которые ссылается yaml

Блокеры (публикация недоступна, пока не исправлено):
    validation  ошибки панели «Проверка» (считает браузер – правила в validate.ts)
    removed     шаг опубликованной версии удалён, а не превращён в переход –
                у людей могут стоять таймеры на него (исследование §6.1–6.2)
    empty       шаг пустой – бот ничего не отправит (studio/model/emptiness.py)
    model       черновик не проходит проверку модели
    engine      выгрузка для bot-engine не получается или bot-engine её не прочитает – только когда
                у Студии НЕТ своего движка (версия – это и есть выгрузка для bot-engine). С
                движком Студии бот работает по графу, а не по yaml: тогда это предупреждение
                `repo_warnings`, «Запустить в боте» оно не блокирует
    nothing     изменений нет

Любая ошибка выгрузки или сверки с bot-engine – текстом в отчёте, не исключением: диалог
публикации не должен получать HTTP 500 (24.09.2026 так вышло из-за новых полей bot-engine).
`engine_drift` – совпадает ли копия движка Студии с bot-engine (studio/engine_ref/drift.py).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..engine_ref.drift import engine_drift
from ..model.emptiness import empty_issues
from ..model.funnel import Funnel
from ..server.errors import translate
from ..server.roundtrip import export_and_check
from .diff import diff_graphs, removed_without_tombstone
from .phrases import STEP_TYPE_LABEL
from .store import VersionedStorage, VersionError

log = logging.getLogger("studio.publish")

_STATIC = re.compile(r"\./static/([A-Za-z0-9_.\-]+)")


def normalize(raw: dict) -> dict:
    """Граф со всеми полями по умолчанию – чтобы старая и новая запись сравнивались честно."""
    try:
        return Funnel.model_validate(raw).model_dump(mode="json", by_alias=True)
    except ValidationError:
        return raw


def graph_of(store: VersionedStorage, name: str, which: str | int | None) -> dict | None:
    """draft / published / номер версии → граф (None – опубликованной ещё нет)."""
    if which in (None, "", "published"):
        n = store.versions.published_number(name)
        return store.versions.version_graph(name, n) if n else None
    if which == "draft":
        return store.load_raw(name)
    return store.versions.version_graph(name, int(which))


def export_report(res: dict) -> dict:
    cmp = res.get("compare") or None
    return {
        "ok": res.get("ok", False),
        "export_error": res.get("export_error"),
        "engine": res.get("engine"),
        "compare": ({"path": cmp["path"], "same": cmp["same"], "differences": len(cmp["differences"]),
                     "error": cmp.get("error")}
                    if cmp else None),
    }


def check_publish(store: VersionedStorage, name: str, repo_root: Path, *, validation_errors: int = 0,
                  repo_blocking: bool = True) -> dict:
    """repo_blocking – проблемы выгрузки для bot-engine блокируют публикацию (Студия без своего
    движка); False – только предупреждение `repo_warnings` (бот Студии работает по графу)."""
    draft = store.load_raw(name)
    pub_n = store.versions.published_number(name)
    published = store.versions.version_graph(name, pub_n) if pub_n else None
    diff = diff_graphs(normalize(published) if published else None, normalize(draft))
    blockers: list[dict[str, Any]] = []

    if validation_errors:
        blockers.append({"kind": "validation", "text":
                         f"Ошибки в воронке: {validation_errors} – исправьте их (клик по строке – к шагу)"})

    pos = ((published or {}).get("layout") or {}).get("positions") or {}
    for s in removed_without_tombstone(published, draft):
        blockers.append({
            "kind": "removed", "step_id": s["id"], "step_key": s["key"],
            "text": f"Шаг «{s['key']}» был в версии {pub_n} и удалён – у людей могут стоять таймеры на "
                    "него. Верните его переходом: выберите, на какой шаг вести этих людей.",
            "step": {"id": s["id"], "key": s["key"], "type": s["type"], "title": s.get("title", ""),
                     "type_label": STEP_TYPE_LABEL.get(s["type"], s["type"]), "position": pos.get(s["id"])},
        })

    export: dict[str, Any] | None = None
    step_warnings: list[dict[str, Any]] = []
    repo_warnings: list[str] = []
    try:
        f = Funnel.model_validate(draft)
    except ValidationError as e:
        errs = translate(e, draft)
        blockers.append({"kind": "model", "text": "Черновик не проходит проверку модели: " +
                         "; ".join(x["message"] for x in errs[:3]),
                         "step_id": next((x["step_id"] for x in errs if x["step_id"]), None)})
        f = None
    if f is not None:
        for i in empty_issues(f):
            if i.level == "error":
                blockers.append({"kind": "empty", "text": i.text, "step_id": i.step_id, "step_key": i.key})
            else:
                step_warnings.append({"step_id": i.step_id, "text": i.text})
        try:
            res = export_and_check(f, repo_root)
        except Exception as e:  # noqa: BLE001 – проверка для bot-engine не должна ронять диалог
            log.exception("выгрузка для bot-engine упала: %s", name)
            res = {"ok": False, "yaml": "", "engine": {"ok": False, "error": None, "steps": 0},
                   "compare": None,
                   "export_error": f"Студия не смогла проверить выгрузку для bot-engine: {type(e).__name__}: {e}"}
        export = {**export_report(res), "yaml": res.get("yaml", "")}
        problem = None
        if res.get("export_error"):
            problem = f"Выгрузить для bot-engine нельзя: {res['export_error']}"
        elif not res["engine"]["ok"]:
            problem = f"Старый бот (bot-engine) не прочитает эту воронку: {res['engine']['error']}"
        if problem and repo_blocking:
            blockers.append({"kind": "engine", "text": problem})
        elif problem:
            repo_warnings.append(problem + ". На запуск в боте Студии это не влияет")
        cmp = res.get("compare") or {}
        if cmp.get("error"):
            repo_warnings.append(cmp["error"])

    if published is not None and diff["summary"]["total"] == 0:
        blockers.append({"kind": "nothing", "text": f"Изменений нет – черновик совпадает с версией {pub_n}"})

    return {
        "name": name,
        "number": store.versions.next_number(name),
        "published": pub_n,
        "base_version": (store.versions.draft_row(name) or {"base_version": None})["base_version"],
        "diff": diff,
        "blockers": blockers,
        "warnings": diff["warnings"],
        "step_warnings": step_warnings,
        "repo_warnings": repo_warnings,
        "engine_drift": _drift(repo_root),
        "export": export,
    }


def _drift(repo_root: Path) -> dict:
    try:
        d = engine_drift(repo_root)
    except Exception as e:  # noqa: BLE001
        return {"same": None, "text": f"сверить копию движка с bot-engine не удалось: {e}"}
    return {k: d.get(k) for k in ("same", "text", "new", "gone", "studio_commit", "engine_commit")}


def write_export(store: VersionedStorage, name: str, number: int, yaml_text: str, report: dict) -> Path:
    launch, funnel = name.split("__", 1)
    out = store.root / "exports" / launch / funnel / f"v{number}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "funnel.yaml").write_text(yaml_text, encoding="utf-8", newline="\n")
    (out / "check.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8", newline="\n")
    files = sorted(set(_STATIC.findall(yaml_text)))
    own = [f for f in files if (store.media / f).is_file()]
    if own:
        (out / "static").mkdir(exist_ok=True)
        for fname in own:
            shutil.copy2(store.media / fname, out / "static" / fname)
    return out


class PublishBlocked(Exception):
    def __init__(self, check: dict):
        super().__init__("публикация заблокирована")
        self.check = check


def publish(store: VersionedStorage, name: str, repo_root: Path, *, comment: str, author: str = "я",
            validation_errors: int = 0, number: int | None = None, chk: dict | None = None,
            repo_blocking: bool = True) -> dict:
    """chk – уже посчитанная `check_publish` (под тем же `store.versions.lock`), чтобы не гонять
    круговую проверку дважды, когда перед публикацией ещё спрашивали движок."""
    with store.versions.lock:
        chk = chk or check_publish(store, name, repo_root, validation_errors=validation_errors,
                                   repo_blocking=repo_blocking)
        if chk["blockers"]:
            raise PublishBlocked(chk)
        n = chk["number"]
        if number is not None and number != n:
            raise VersionError(f"пока готовили публикацию, появилась v{n - 1} – откройте диалог заново")
        report = {
            "version": n, "funnel": name, "author": author, "comment": comment,
            "parent": chk["base_version"], "previous_published": chk["published"],
            "summary": chk["diff"]["summary"],
            "changes": [c["text"] for c in chk["diff"]["changes"]],
            "warnings": [w["text"] for w in chk["warnings"]],
            "export": {k: v for k, v in (chk["export"] or {}).items() if k != "yaml"},
            "source_file": (store.load_raw(name).get("source") or {}).get("path", ""),
        }
        out = write_export(store, name, n, (chk["export"] or {}).get("yaml", ""), report)
        rel = out.relative_to(store.root).as_posix()
        got = store.versions.publish(name, author=author, comment=comment, export_dir=rel, number=n,
                                     report={"summary": report["summary"], "export": report["export"],
                                             "previous": chk["published"], "warnings": len(report["warnings"])})
        return {"number": got, "export_dir": rel, "summary": report["summary"],
                "archived": chk["published"]}
