"""Отчёт по всем воронкам: шаги, стрелки по видам, режимы времени, условия, «мешок».

    uv run python -m studio.report            # сводка + таблица по файлам
    uv run python -m studio.report --brief    # только сводка
"""
from __future__ import annotations

import argparse
import collections
import sys

from .convert import import_file
from .model.funnel import EdgeKind
from .paths import funnel_files, bot_repo_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m studio.report")
    ap.add_argument("--brief", action="store_true", help="только сводка")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    files = funnel_files()
    total_steps = 0
    edges = collections.Counter()
    types = collections.Counter()
    modes = collections.Counter()
    conds = collections.Counter()
    bag = collections.Counter()
    bag_where: dict[str, list[str]] = collections.defaultdict(list)
    unresolved = 0
    rows = []
    for p in files:
        g = import_file(p, repo_root=bot_repo_path())
        ek = collections.Counter(e.kind.value for e in g.edges)
        edges.update(ek)
        total_steps += len(g.steps)
        unresolved += len(g.unresolved)
        nbag = 0
        for s in g.steps:
            types[s.type.value] += 1
            modes[s.schedule.mode] += 1
            conds.update(s.conditions.active())
            for k in s.advanced:
                bag[k] += 1
                nbag += 1
                bag_where[k].append(f"{g.source.launch}/{p.name}:{s.key}")
        rows.append((f"{p.parent.name}/{p.name}", len(g.steps), len(g.edges), dict(ek), nbag))

    print(f"Воронок: {len(files)}   шагов: {total_steps}   стрелок: {sum(edges.values())}")
    print("Стрелки по видам:")
    for k in EdgeKind:
        print(f"  {k.value:22} {edges.get(k.value, 0)}")
    print("Типы шагов:", dict(types.most_common()))
    print("Время шага (schedule.mode):", dict(modes.most_common()))
    print("Условия:", dict(conds.most_common()))
    print(f"Висячих ссылок (unresolved): {unresolved}")
    print(f"Мешок advanced: {sum(bag.values())} значений" + (":" if bag else " – пусто"))
    for k, n in bag.most_common():
        print(f"  {k}: {n}  ({', '.join(bag_where[k][:5])}{' …' if n > 5 else ''})")
    if not args.brief:
        print()
        print(f"{'файл':48} {'шаги':>5} {'стрелки':>8}  мешок  виды стрелок")
        for name, ns, ne, ek, nb in rows:
            kinds = " ".join(f"{k}={v}" for k, v in sorted(ek.items()))
            print(f"{name:48} {ns:>5} {ne:>8}  {nb:>5}  {kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
