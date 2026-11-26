// Каноническая форма графа «по смыслу»: без id (ссылки – через key), без раскладки и source.
// Два графа, собранные разными путями (импорт и операции редактора), равны, если равны их
// канонические формы.
import type { Funnel } from "./types";

function sortKeys(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(sortKeys);
  if (v && typeof v === "object") {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(v as object).sort()) out[k] = sortKeys((v as Record<string, unknown>)[k]);
    return out;
  }
  return v;
}

export function canonical(g: Funnel): unknown {
  const key = new Map<string, string>();
  g.steps.forEach((s) => key.set(s.id, s.key));
  g.unresolved.forEach((u) => key.set(u.id, `?${u.key}`));
  const k = (id: string | null) => (id === null ? null : key.get(id) ?? `!${id}`);
  const laneTitle = new Map(g.lanes.map((l) => [l.id, l.title]));
  return sortKeys({
    id: g.id,
    title: g.title,
    air_email_profile: g.air_email_profile,
    max_video_note_caption: g.max_video_note_caption,
    resend_entry_on_repeat: g.resend_entry_on_repeat,
    webinar_room: g.webinar_room,
    branch_router: g.branch_router && {
      ...g.branch_router,
      branches: g.branch_router.branches.map((b) => ({ ...b, entry_step: k(b.entry_step) })),
    },
    entry_variants: g.entry_variants.map((v) => ({ ...v, entry_step: k(v.entry_step) })),
    steps: g.steps.map(({ id: _id, ...rest }) => rest),
    edges: g.edges.map((e) => ({
      from: k(e.from_step), kind: e.kind, to: k(e.to_step), to_funnel: e.to_funnel,
      delay: e.delay_seconds, typing: e.typing_before,
    })),
    unresolved: g.unresolved.map((u) => u.key),
    lanes: [...g.lanes].sort((a, b) => a.order - b.order).map((l) => ({
      title: l.title, kind: l.kind, day: l.day, steps: l.step_ids.map(k),
    })),
    notes: g.notes.map(({ id: _id, step_id, lane_id, ...rest }) => ({
      ...rest, step: k(step_id), lane: lane_id ? laneTitle.get(lane_id) ?? null : null,
    })),
  });
}

export function sameMeaning(a: Funnel, b: Funnel): boolean {
  return JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
}

/** Первые расхождения канонических форм – для понятного сообщения теста. */
export function meaningDiff(a: Funnel, b: Funnel, limit = 10): string[] {
  const out: string[] = [];
  const walk = (x: unknown, y: unknown, path: string) => {
    if (out.length >= limit) return;
    if (JSON.stringify(x) === JSON.stringify(y)) return;
    if (x && y && typeof x === "object" && typeof y === "object") {
      const keys = new Set([...Object.keys(x as object), ...Object.keys(y as object)]);
      for (const kk of keys) walk((x as Record<string, unknown>)[kk], (y as Record<string, unknown>)[kk], `${path}.${kk}`);
      return;
    }
    out.push(`${path}: ${JSON.stringify(x)?.slice(0, 120)} ≠ ${JSON.stringify(y)?.slice(0, 120)}`);
  };
  walk(canonical(a), canonical(b), "");
  return out;
}
