// Справки по графу: входы, входящие стрелки.
import type { Edge, Funnel } from "./types";

/** Шаги-входы: входы веток, входы когорт; без веток – первый шаг. */
export function findEntries(g: Funnel): Set<string> {
  const out = new Set<string>();
  if (g.branch_router) for (const b of g.branch_router.branches) out.add(b.entry_step);
  else if (g.steps.length) out.add(g.steps[0].id);
  for (const v of g.entry_variants) out.add(v.entry_step);
  return out;
}

export function incomingMap(g: Funnel): Map<string, Edge[]> {
  const m = new Map<string, Edge[]>();
  for (const e of g.edges) {
    if (!e.to_step) continue;
    const arr = m.get(e.to_step);
    if (arr) arr.push(e);
    else m.set(e.to_step, [e]);
  }
  return m;
}
