"""Импорт фрейма Miro из командной строки и сверка с эталоном.

    uv run python -m studio.miro "<ссылка на фрейм>" --compare launches/webinar/funnel.yaml
    uv run python -m studio.miro снимок.json --compare launches/webinar/funnel.yaml --json

Первый аргумент – ссылка Miro (`…/app/board/<доска>/?moveToWidget=<фрейм>`) или сохранённый
снимок фрейма (json). Снимок каждого скачанного фрейма кладётся в кэш
`<STUDIO_DATA>/miro_cache/` (или `--save-snapshot`). Токен – env MIRO_TOKEN.
Путь эталона – относительно BOT_REPO_PATH (или абсолютный).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..convert import import_file
from ..paths import bot_repo_path
from .build import build_funnel
from .client import MiroClient, load_snapshot, parse_link, save_snapshot
from .compare import compare
from .stickers import default_parser

ROOT = Path(__file__).resolve().parents[2]


def cache_dir() -> Path:
    return Path(os.environ.get("STUDIO_DATA") or ROOT / "data") / "miro_cache"


def snapshot_for(arg: str, save: str | None = None) -> tuple[dict, str]:
    p = Path(arg)
    if p.suffix == ".json" and p.exists():
        return load_snapshot(p), arg
    ref = parse_link(arg)
    with MiroClient() as cl:
        snap = cl.fetch_frame(ref)
    out = Path(save) if save else cache_dir() / f"{ref.board_id.rstrip('=')}_{snap['frame']['id']}.json"
    save_snapshot(snap, out)
    print(f"снимок фрейма: {out}", file=sys.stderr)
    return snap, ref.item_url(str(snap["frame"]["id"]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m studio.miro", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="ссылка на фрейм Miro или снимок .json")
    ap.add_argument("--compare", help="эталон: launches/<запуск>/funnel.yaml")
    ap.add_argument("--launch", help="запуск (по умолчанию – из пути эталона)")
    ap.add_argument("--funnel", default="miro", help="id воронки (по умолчанию miro)")
    ap.add_argument("--out", help="записать граф в файл")
    ap.add_argument("--save-snapshot", help="куда сохранить снимок фрейма")
    ap.add_argument("--json", action="store_true", help="вывод в JSON")
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    snap, link = snapshot_for(a.source, a.save_snapshot)
    ref_path = None
    if a.compare:
        ref_path = Path(a.compare)
        if not ref_path.is_absolute():
            ref_path = bot_repo_path() / ref_path
    launch = a.launch or (ref_path.parent.name if ref_path else "miro")
    res = build_funnel(snap, launch=launch, funnel_id=a.funnel, parser=default_parser(), link=link)
    if a.out:
        Path(a.out).write_text(res.funnel.model_dump_json(indent=1, by_alias=True), encoding="utf-8")
    doubts = res.doubts()
    out: dict = {"frame": res.board.frame_title, "stats": res.stats, "doubts": doubts}
    if ref_path:
        ref = import_file(ref_path, repo_root=bot_repo_path())
        cmp = compare(ref, res.funnel, ref_name=str(a.compare))
        out["compare"] = cmp.summary()
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    st = res.stats
    print(f"Фрейм «{res.board.frame_title}»: собрано {st['messages']} сообщений, {st['edges']} стрелок, "
          f"картинок {st['images']}; проверить – {st['check']} мест в {st['steps_to_check']} сообщениях "
          f"(стикеры разбирает: {st['parser']})")
    bd = st.get("board") or {}
    if bd:
        why = ", ".join(f"{k} – {v}" for k, v in bd["skipped_reasons"].items())
        print(f"Доска: {bd['items']} элементов – в шагах {bd['in_steps']}, в настройках {bd['settings']}, "
              f"заметками {bd['notes']}, пропущено {bd['skipped']}" + (f" ({why})" if why else "")
              + (f", НЕ УЧТЕНО {bd['unaccounted']}" if bd["unaccounted"] else ""))
        cn = bd.get("connectors") or {}
        if cn:
            print("Стрелки: " + ", ".join(f"{k} {v}" for k, v in cn.items() if k != "unaccounted" and v))
    if st.get("days"):
        print(f"Дней: {st['days']}, заметок на холсте: {st.get('notes', 0)}")
    print()
    for d in doubts:
        if d["level"] == "check":
            print(f"  ⚠ {d['step_key'] or 'воронка'}: {d['text']}")
    if ref_path:
        print()
        print(cmp.text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
