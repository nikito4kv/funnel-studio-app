// Раскладка: elkjs слева направо по реальным размерам карточек, затем карточки
// раскладываются по полосам (каждая полоса – своя горизонтальная лента, x от ELK сохраняется).
// Верх полосы хранится в layout.positions[<id полосы>].y; низ – до следующей полосы.
import type { ELK as ElkApi } from "elkjs/lib/elk-api";
import type { Funnel, Lane, Position } from "../model/types";

let elkPromise: Promise<ElkApi> | null = null;
function getElk(): Promise<ElkApi> {
  elkPromise ??= import("elkjs/lib/elk.bundled.js").then((m) => new m.default());
  return elkPromise;
}

const OPTIONS: Record<string, string> = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.spacing.nodeNode": "50",
  "elk.layered.spacing.nodeNodeBetweenLayers": "280",
  "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
  "elk.edgeRouting": "POLYLINE",
};

export type Size = { width: number; height: number };
export const DEFAULT_SIZE: Size = { width: 320, height: 260 };
export const LANE_PAD = 56;        // отступ карточек от верха полосы (место под заголовок)
export const LANE_GAP = 40;
export const LANE_MIN_H = 280;

export async function elkPositions(g: Funnel, sizes: Record<string, Size>): Promise<Record<string, Position>> {
  const elk = await getElk();
  const ids = new Set(g.steps.map((s) => s.id));
  const res = await elk.layout({
    id: "root",
    layoutOptions: OPTIONS,
    children: g.steps.map((s) => ({ id: s.id, ...(sizes[s.id] ?? DEFAULT_SIZE) })),
    edges: g.edges
      .filter((e) => e.to_step && ids.has(e.to_step) && e.to_step !== e.from_step)
      .map((e) => ({ id: e.id, sources: [e.from_step], targets: [e.to_step!] })),
  });
  const out: Record<string, Position> = {};
  for (const c of res.children ?? []) out[c.id] = { x: c.x ?? 0, y: c.y ?? 0 };
  return out;
}

/** Разложить по полосам: y внутри полосы – как у ELK, полосы друг под другом. */
export function stackLanes(g: Funnel, pos: Record<string, Position>, sizes: Record<string, Size>): Record<string, Position> {
  const lanes = [...g.lanes].sort((a, b) => a.order - b.order);
  if (!lanes.length) return pos;
  const out: Record<string, Position> = { ...pos };
  const inLane = new Set<string>();
  let top = 0;
  for (const lane of lanes) {
    const members = lane.step_ids.filter((id) => pos[id]);
    members.forEach((id) => inLane.add(id));
    const minY = members.length ? Math.min(...members.map((id) => pos[id].y)) : 0;
    let bottom = 0;
    for (const id of members) {
      const y = pos[id].y - minY + LANE_PAD;
      out[id] = { x: pos[id].x, y: top + y };
      bottom = Math.max(bottom, y + (sizes[id] ?? DEFAULT_SIZE).height);
    }
    out[lane.id] = { x: 0, y: top };
    top += Math.max(LANE_MIN_H, bottom + LANE_PAD) + LANE_GAP;
  }
  // без полосы – под всеми
  const rest = g.steps.filter((s) => !inLane.has(s.id) && pos[s.id]);
  if (rest.length) {
    const minY = Math.min(...rest.map((s) => pos[s.id].y));
    for (const s of rest) out[s.id] = { x: pos[s.id].x, y: top + pos[s.id].y - minY };
  }
  return out;
}

export interface LaneBand {
  lane: Lane;
  top: number;
  bottom: number;
}

/** Полосы на холсте: [верх, низ) по сохранённым верхам; последняя – до нижней карточки. */
export function laneBands(g: Funnel, positions: Record<string, Position>, sizes: Record<string, Size>): LaneBand[] {
  const withTop = g.lanes
    .map((lane) => ({ lane, top: positions[lane.id]?.y ?? null }))
    .filter((x): x is { lane: Lane; top: number } => x.top !== null)
    .sort((a, b) => a.top - b.top);
  return withTop.map((x, i) => {
    const next = withTop[i + 1];
    let bottom: number;
    if (next) bottom = next.top - LANE_GAP / 2;
    else {
      const members = x.lane.step_ids.filter((id) => positions[id]);
      const maxB = Math.max(x.top + LANE_MIN_H, ...members.map((id) => positions[id].y + (sizes[id] ?? DEFAULT_SIZE).height + LANE_PAD));
      bottom = maxB;
    }
    return { lane: x.lane, top: x.top, bottom };
  });
}

/** Полоса, в которую попадает точка y (по сохранённым верхам). */
export function laneAt(bands: LaneBand[], y: number): Lane | null {
  let found: Lane | null = null;
  for (const b of bands) if (y >= b.top) found = b.lane;
  return found;
}
