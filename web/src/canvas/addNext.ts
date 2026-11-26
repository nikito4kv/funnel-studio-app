// «＋ Следующее сообщение» и стрелка, отпущенная в пустое место: новая карточка сразу
// связана с предыдущей, выделена, курсор – в её тексте. Одна правка – одна отмена (Ctrl+Z).
import * as ops from "../model/ops";
import type { EdgeKind, Position } from "../model/types";
import { newId, useStudio } from "../store";
import { DEFAULT_SIZE, laneBands, laneAt, type Size } from "./layout";

/** Задержка новой стрелки «дальше по времени» – минута, как чаще всего на досках. */
export const NEXT_DELAY = 60;

/** Свободный выход шага, от которого продолжают цепочку, или null. */
export function freeExit(stepId: string): EdgeKind | null {
  const g = useStudio.getState().funnel;
  const s = g?.steps.find((x) => x.id === stepId);
  if (!g || !s || s.type === "jump") return null;
  const used = new Set(g.edges.filter((e) => e.from_step === stepId).map((e) => e.kind));
  const order: EdgeKind[] = s.type === "phone_request" ? ["contact", "skip_if_phone_known", "skip"]
    : s.type === "email_request" ? ["email", "skip_if_email_known", "skip"]
      : s.survey.qid ? ["survey_next"] : ["done"];
  return order.find((k) => !used.has(k)) ?? null;
}

function overlaps(p: Position, sizes: Record<string, Size>, skip: string): boolean {
  const g = useStudio.getState().funnel!;
  return g.steps.some((s) => {
    if (s.id === skip) return false;
    const q = g.layout.positions[s.id];
    if (!q) return false;
    const z = sizes[s.id] ?? DEFAULT_SIZE;
    return p.x < q.x + z.width + 40 && p.x + DEFAULT_SIZE.width + 40 > q.x && p.y < q.y + z.height && p.y + 300 > q.y;
  });
}

/** Новая карточка после шага `fromId`: справа от него (или в точке `at`), связанная стрелкой. */
export function addNext(fromId: string, opts: { at?: Position; kind?: EdgeKind; size?: Size | null; sizes?: Record<string, Size> } = {}): string | null {
  const st = useStudio.getState();
  const g = st.funnel;
  if (!g) return null;
  const kind = opts.kind ?? freeExit(fromId);
  if (!kind) return null;
  const from = g.layout.positions[fromId] ?? { x: 0, y: 0 };
  const size = opts.size ?? opts.sizes?.[fromId] ?? DEFAULT_SIZE;
  let pos = opts.at;
  if (!pos) {
    pos = { x: from.x + size.width + 140, y: from.y };
    for (let i = 0; i < 12 && overlaps(pos, opts.sizes ?? {}, fromId); i++) pos = { x: pos.x + DEFAULT_SIZE.width + 140, y: pos.y };
  }
  const sizes = opts.sizes ?? {};
  const lane = g.lanes.find((l) => l.step_ids.includes(fromId)) ?? laneAt(laneBands(g, g.layout.positions, sizes), pos.y + 40);
  const id = newId();
  const edgeId = newId();
  const ok = st.apply((f) => {
    let out = ops.addStep(f, { id, type: "text", position: pos, laneId: lane?.id ?? null });
    const delay = kind === "done" || kind === "also_fire" ? NEXT_DELAY : undefined;
    out = ops.addEdge(out, { id: edgeId, from: fromId, kind, to: id, delay });
    return out;
  });
  if (!ok) return null;
  st.select({ steps: [id], edge: null, note: null });
  st.requestTextFocus();
  return id;
}
