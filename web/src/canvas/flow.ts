// Граф Студии → узлы и стрелки React Flow (полосы, карточки, заметки, вход, переходы в воронки).
import type { Edge as RFEdge, Node as RFNode } from "@xyflow/react";
import { MarkerType } from "@xyflow/react";
import { findEntries, incomingMap } from "../model/graphinfo";
import type { Edge, Funnel, Platform, Position, Step } from "../model/types";
import type { Issue } from "../model/validate";
import { edgePhrase, schedulePhrase } from "../phrases";
import type { SimMark } from "../sim/types";
import { branchLong, branchNames } from "../simple/labels";
import { stepRef } from "../simple/names";
import type { Selection } from "../store";
import { DEFAULT_SIZE, laneBands, type Size } from "./layout";
import { exitsOf, handleOf, IN_TOP, RIGHT, type LaneNodeData, type NoteNodeData, type StepNodeData } from "./nodes";

export const START = "__start__";
export const LANE_PREFIX = "lane:";
export const NOTE_PREFIX = "note:";
export const FUNNEL_PREFIX = "funnel:";

// цвета – токены (design/tokens.css): в SVG стиль стрелки понимает var(--…)
const EDGE_COLOR: Partial<Record<Edge["kind"], string>> = {
  callback: "var(--edge-callback)",
  survey_next: "var(--edge-survey)",
  skip: "var(--edge-soft)",
  skip_if_phone_known: "var(--edge-soft)",
  skip_if_email_known: "var(--edge-soft)",
  also_fire: "var(--edge-soft)",
  chain_to_funnel: "var(--edge-chain)",
  jump: "var(--edge-jump)",
};

interface Box { x: number; y: number; w: number; h: number }

/** Стороны стрелки по взаимному положению карточек: цель правее – выход справа и вход слева,
 *  цель ниже – выход снизу и вход сверху. Так стрелка не заезжает за карточку и не выныривает
 *  из-под неё. `fixed` – у выхода своя точка (кнопка-действие справа, «дал / не дал» снизу). */
export function edgeSides(s: Box, t: Box, fixed: "right" | "bottom" | null = null): { src: "right" | "bottom"; dst: "left" | "top" } {
  const dx = t.x - (s.x + s.w); // > 0 – цель целиком правее
  const dy = t.y - (s.y + s.h); // > 0 – цель целиком ниже
  if (fixed === "right") return { src: "right", dst: dx >= -40 || dy < 0 ? "left" : "top" };
  if (fixed === "bottom") return { src: "bottom", dst: dy >= -20 ? "top" : "left" };
  if (dx >= 40 && dx >= dy) return { src: "right", dst: "left" };
  if (dy >= 0) return { src: "bottom", dst: "top" };
  return { src: "right", dst: "left" };
}

const DAY: Record<string, string> = { today: "сегодня", tomorrow: "завтра", fixed: "дата" };

function fmtDate(d: string | null): string {
  if (!d) return "";
  const [y, m, day] = d.split("-");
  return `${day}.${m}.${y}`;
}

export interface FlowArgs {
  funnel: Funnel;
  platform: Platform;
  branch: string | null;
  issues: Issue[];
  selection: Selection;
  sizes: Record<string, Size>;
  showNotes: boolean;
  /** Простой режим: слова вместо id веток и технических имён. */
  simple?: boolean;
  /** Кэш data карточек между пересборками: неизменённая карточка получает тот же объект data,
   *  и memo-компонент её не перерисовывает (на 138 шагах иначе лагает каждый клик). */
  dataCache?: Map<string, StepNodeData>;
  /** Отметки прогона симулятора (step id → «уйдёт в …» / «выпадет»). */
  sim?: Map<string, SimMark>;
}

function sameSim(a: SimMark | null, b: SimMark | null): boolean {
  return a === b || (!!a && !!b && a.state === b.state && a.label === b.label && a.title === b.title);
}

function sameData(a: StepNodeData, b: StepNodeData): boolean {
  return a.step === b.step && a.chip === b.chip && a.launch === b.launch && a.platform === b.platform
    && a.level === b.level && a.notes === b.notes && a.revisions === b.revisions && a.entry === b.entry
    && a.simple === b.simple && a.jumpTo === b.jumpTo
    && a.used.length === b.used.length && a.used.every((k, i) => k === b.used[i]) && sameSim(a.sim, b.sim);
}

/** Фраза стрелки для холста. Постановщику «одновременно · через 3 мин» непонятно: у стрелки
 *  «вместе» (also_fire) пишем просто «через 3 мин», а «вместе с «…»» – в подсказке. */
export function edgeWords(e: Edge, from: Step | undefined, simple: boolean): string {
  let p = edgePhrase(e, from, simple);
  if (!simple) return p;
  if (e.kind === "also_fire") p = p.replace(/^одновременно · /, "");
  // те же слова, что у точек выхода на карточке
  p = p.replace(/^дал телефон/, "дал номер").replace(/^телефон известен/, "номер уже есть").replace(/^почта известна/, "почта уже есть");
  return p.replace(/ сек$/, " с");
}

export function chipFor(s: Step, g: Funnel, branchId: string | null, incoming: Map<string, Edge[]>, entries: Set<string>, byId: Map<string, Step>, simple = false): string | null {
  const branch = g.branch_router?.branches.find((b) => b.id === branchId) ?? null;
  const sched = schedulePhrase(s.schedule, branch, branchNames(g.branch_router?.branches, simple), !g.branch_router);
  if (sched) return sched;
  const phrases = [...new Set((incoming.get(s.id) ?? []).map((e) => edgeWords(e, byId.get(e.from_step), simple)))];
  if (phrases.length) return phrases.join(" / ");
  return entries.has(s.id) ? "сразу при входе" : null;
}

export function buildFlow(a: FlowArgs): { nodes: RFNode[]; edges: RFEdge[] } {
  const g = a.funnel;
  const launch = g.source?.launch ?? "";
  const byId = new Map(g.steps.map((s) => [s.id, s]));
  const positions: Record<string, Position> = g.layout.positions;
  const incoming = incomingMap(g);
  const entries = findEntries(g);
  const nodes: RFNode[] = [];
  const edges: RFEdge[] = [];
  const size = (id: string) => a.sizes[id] ?? DEFAULT_SIZE;

  const level = new Map<string, "error" | "warn">();
  for (const i of a.issues) {
    if (!i.stepId) continue;
    if (i.level === "error" || !level.has(i.stepId)) level.set(i.stepId, i.level);
  }
  const notesOn = new Map<string, { notes: number; revisions: number }>();
  for (const n of g.notes) {
    if (!n.step_id) continue;
    const c = notesOn.get(n.step_id) ?? { notes: 0, revisions: 0 };
    if (n.kind === "revision") c.revisions++;
    else c.notes++;
    notesOn.set(n.step_id, c);
  }
  const usedKinds = new Map<string, Edge["kind"][]>();
  for (const e of g.edges) usedKinds.set(e.from_step, [...(usedKinds.get(e.from_step) ?? []), e.kind]);

  // ── полосы (под карточками) ──
  const placed = g.steps.filter((s) => positions[s.id]);
  const minX = placed.length ? Math.min(...placed.map((s) => positions[s.id].x)) : 0;
  const maxX = placed.length ? Math.max(...placed.map((s) => positions[s.id].x + size(s.id).width)) : 1200;
  for (const b of laneBands(g, positions, a.sizes)) {
    nodes.push({
      id: LANE_PREFIX + b.lane.id,
      type: "lane",
      position: { x: minX - 120, y: b.top },
      data: { title: b.lane.title, count: b.lane.step_ids.length, width: maxX - minX + 280, height: b.bottom - b.top } satisfies LaneNodeData,
      draggable: false,
      selectable: false,
      focusable: false,
      zIndex: -10,
    });
  }

  // ── карточки ──
  const seen = new Set<string>();
  for (const s of g.steps) {
    const c = notesOn.get(s.id);
    const jumpEdge = s.type === "jump" ? g.edges.find((e) => e.from_step === s.id && e.kind === "jump") : undefined;
    let data: StepNodeData = {
      step: s, chip: chipFor(s, g, a.branch, incoming, entries, byId, !!a.simple), launch, platform: a.platform,
      level: level.get(s.id) ?? null, notes: c?.notes ?? 0, revisions: c?.revisions ?? 0,
      entry: entries.has(s.id), used: usedKinds.get(s.id) ?? [], sim: a.sim?.get(s.id) ?? null,
      simple: !!a.simple,
      jumpTo: jumpEdge?.to_step ? stepRef(byId.get(jumpEdge.to_step), !!a.simple) : null,
    };
    if (a.dataCache) {
      const prev = a.dataCache.get(s.id);
      if (prev && sameData(prev, data)) data = prev;
      else a.dataCache.set(s.id, data);
      seen.add(s.id);
    }
    nodes.push({
      id: s.id,
      type: s.type === "video_note" ? "circle" : "step",
      position: positions[s.id] ?? { x: 0, y: 0 },
      selected: a.selection.steps.includes(s.id),
      data,
    });
  }
  if (a.dataCache) for (const id of [...a.dataCache.keys()]) if (!seen.has(id)) a.dataCache.delete(id);

  // ── вход ──
  const startLines: string[] = [];
  const entryEdges: { step: string; label: string }[] = [];
  if (g.branch_router) {
    const all = g.branch_router.branches;
    for (const b of all) {
      const win = b.windows.map((w) => `рег ${w.from}–${w.to} → ${w.day === "fixed" ? fmtDate(w.date) : DAY[w.day]}`).join("; ");
      if (a.simple) {
        startLines.push(`${branchLong(b, all)}: ${win}` + (b.repeat_offset_minutes ? `, повтор через ${Math.floor(b.repeat_offset_minutes / 60)} ч` + (b.repeat_offset_minutes % 60 ? ` ${b.repeat_offset_minutes % 60} мин` : "") : ""));
      } else {
        startLines.push(`ветка ${b.id}: ${win}, эфир ${b.webinar_time}` + (b.repeat_offset_minutes ? `, повтор +${b.repeat_offset_minutes} мин` : ""));
      }
      entryEdges.push({ step: b.entry_step, label: a.simple ? branchLong(b, all).replace(" · эфир", ",") : `ветка ${b.id}` });
    }
  } else if (g.steps.length) {
    entryEdges.push({ step: g.steps[0].id, label: "первый шаг" });
  }
  for (const ev of g.entry_variants) {
    const cond = Object.entries(ev.when)
      .filter(([, v]) => v !== null && !(Array.isArray(v) && v.length === 0))
      .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : v}`).join(" ");
    entryEdges.push({ step: ev.entry_step, label: a.simple ? `если ${cond}` : `когорта ${cond}` });
  }
  const firstEntry = entryEdges.find((e) => positions[e.step]);
  if (firstEntry) {
    const p = positions[firstEntry.step];
    nodes.push({
      id: START, type: "start", position: { x: p.x - 380, y: p.y }, data: { lines: startLines },
      draggable: false, selectable: false,
    });
    entryEdges.forEach((e, i) => edges.push({
      id: `start-${i}`, type: "studio", source: START, target: e.step, targetHandle: "in", label: e.label,
      selectable: false, reconnectable: false,
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--edge-strong)" }, style: { strokeWidth: 1.75, stroke: "var(--edge-strong)" },
      labelStyle: { fontSize: 12, fill: "var(--text-2)", fontWeight: 500 },
      labelBgStyle: { fill: "var(--canvas)", fillOpacity: 0.95 },
      labelBgPadding: [6, 3], labelBgBorderRadius: 6,
    }));
  }

  // ── стрелки ──
  // одна правда о времени: если у карточки своё время (часы или от эфира), стрелка к ней говорит
  // то же, что фишка на карточке, а не свою задержку – та всё равно не действует
  const edgeLabel = (e: Edge): string => {
    const to = e.to_step ? byId.get(e.to_step) : undefined;
    const timed = to && (to.schedule.absolute_time_msk || to.schedule.webinar_offset_minutes !== null);
    if (timed && (e.kind === "done" || e.kind === "also_fire" || e.kind === "survey_next")) {
      return chipFor(to, g, a.branch, incoming, entries, byId, !!a.simple) ?? edgeWords(e, byId.get(e.from_step), !!a.simple);
    }
    return edgeWords(e, byId.get(e.from_step), !!a.simple);
  };
  for (const e of g.edges) {
    let target = e.to_step;
    if (e.kind === "chain_to_funnel") {
      target = `${FUNNEL_PREFIX}${e.from_step}:${e.to_funnel}`;
      const p = positions[e.from_step] ?? { x: 0, y: 0 };
      nodes.push({
        id: target, type: "label", position: { x: p.x + size(e.from_step).width + 160, y: p.y + 40 },
        data: { label: `↪ воронка «${e.to_funnel}»` }, draggable: false, selectable: false,
      });
    }
    if (!target) continue;
    const selected = a.selection.edge === e.id;
    const color = selected ? "var(--accent)" : EDGE_COLOR[e.kind] ?? "var(--edge)";
    // сторона выхода и входа – по положению карточек
    let sourceHandle = e.kind === "callback" ? "callback" : handleOf(e.kind);
    let targetHandle = "in";
    const from = byId.get(e.from_step);
    const ps = positions[e.from_step];
    const pt = e.to_step ? positions[e.to_step] : undefined;
    if (from && ps && pt && e.to_step) {
      const main = exitsOf(from);
      // сторону выбирают все выходы шага (и «дал / не дал / уже есть»), кроме кнопки-действия
      const flexible = e.kind !== "callback" && main.some((x) => x.kind === sourceHandle);
      const ss = size(e.from_step);
      const ts = size(e.to_step);
      const side = edgeSides({ x: ps.x, y: ps.y, w: ss.width, h: ss.height }, { x: pt.x, y: pt.y, w: ts.width, h: ts.height },
        e.kind === "callback" ? "right" : flexible ? null : "bottom");
      if (flexible && side.src === "right") sourceHandle += RIGHT;
      targetHandle = side.dst === "top" ? IN_TOP : "in";
    }
    edges.push({
      id: e.id,
      type: "studio",
      source: e.from_step,
      sourceHandle,
      target,
      targetHandle,
      label: edgeLabel(e),
      data: { tip: e.kind === "also_fire" && from && e.to_step
        ? `вместе с «${stepRef(from, !!a.simple)}» – отдельно от основной цепочки` : null },
      selected,
      reconnectable: e.kind !== "chain_to_funnel" ? "target" : false,
      style: { stroke: color, strokeWidth: selected ? 2.5 : 1.75, strokeDasharray: e.kind === "also_fire" ? "6 5" : undefined },
      labelStyle: { fontSize: 12, fill: selected ? "var(--accent-ink)" : "var(--text-2)", fontWeight: selected ? 600 : 500 },
      labelBgStyle: { fill: selected ? "var(--accent-soft)" : "var(--canvas)", fillOpacity: 0.96 },
      labelBgPadding: [6, 3],
      labelBgBorderRadius: 6,
      markerEnd: { type: MarkerType.ArrowClosed, color },
      interactionWidth: 18,
      className: `e-${e.kind}`,
    });
  }

  // ── заметки ──
  if (a.showNotes) {
    const stack = new Map<string, number>();
    for (const n of g.notes) {
      let pos: Position | null = null;
      let pinnedTo: string | null = null;
      if (n.step_id && positions[n.step_id]) {
        const p = positions[n.step_id];
        const i = stack.get(n.step_id) ?? 0;
        stack.set(n.step_id, i + 1);
        pos = { x: p.x + size(n.step_id).width + 18, y: p.y + i * 120 };
        pinnedTo = byId.get(n.step_id)?.key ?? null;
      } else if (n.position) {
        pos = n.position;
      }
      if (!pos) continue;
      nodes.push({
        id: NOTE_PREFIX + n.id, type: "note", position: pos, zIndex: 5,
        selected: a.selection.note === n.id,
        draggable: !n.step_id,
        data: { note: n, pinnedTo } satisfies NoteNodeData,
      });
    }
  }
  // размеры, которые React Flow уже измерил, – иначе мини-карта не рисует узлы
  for (const n of nodes) {
    if (n.type === "lane") {
      const d = n.data as LaneNodeData;
      n.measured = { width: d.width, height: d.height };
    } else if (a.sizes[n.id]) n.measured = a.sizes[n.id];
  }
  return { nodes, edges };
}
