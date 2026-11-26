// Оформление ленты симулятора: значки, группы по дням, «важность» события.
import type { Dropped, EventKind, SimEvent, SimResult } from "./types";

export type Tone = "ok" | "late" | "drop" | "gate" | "human" | "info";

const ICON: Record<EventKind, string> = {
  start: "▶", sent: "✉", gated: "⛔", past_skip: "✖", parallel_lost: "✂", replaced: "↻", dup: "⧉",
  wait: "⌛", human: "👤", timeout: "⏱", end: "■", chain: "↪", chain_ignored: "⚠", stuck: "⚠",
  error: "⚠", jump: "⤳", skip_known: "↷", tg_skip: "↷", skip_max: "↷", recovered: "↺",
  cancelled: "✂", claim_cancel: "✂", edit_end: "✂", unknown_step: "⚠",
};

const DROP: ReadonlySet<EventKind> = new Set<EventKind>([
  "past_skip", "parallel_lost", "claim_cancel", "edit_end", "unknown_step", "stuck", "chain_ignored", "error",
]);
const GATE: ReadonlySet<EventKind> = new Set<EventKind>(["gated", "skip_known", "tg_skip", "skip_max"]);

export function toneOf(e: SimEvent): Tone {
  if (e.kind === "sent") return e.late_from || e.dnd_from || e.no_dnd ? "late" : "ok";
  if (DROP.has(e.kind)) return e.resolved ? "info" : "drop";
  if (GATE.has(e.kind)) return "gate";
  if (e.kind === "human") return "human";
  return "info";
}

export function iconOf(e: SimEvent): string {
  if (e.kind === "sent") {
    if (e.dnd_from) return "🌙";
    if (e.late_from) return "⏳";
    if (e.type === "video_note") return "◉";
    if (e.type === "call") return "☎";
    if (e.type === "sms") return "💬";
  }
  return ICON[e.kind] ?? "•";
}

export type Filter = "all" | "messages" | "problems";

export function visible(e: SimEvent, f: Filter): boolean {
  if (f === "all") return true;
  const t = toneOf(e);
  if (f === "messages") return e.kind === "sent" || e.kind === "start";
  return t === "drop" || t === "late" || e.kind === "replaced" || e.kind === "gated";
}

export interface DayGroup {
  day: number;
  title: string;
  date: string;
  airs: string[];
  events: SimEvent[];
}

/** Лента по дням: «День регистрации», «День 1»… – с эфирами ветки в этот день. */
export function groupByDay(r: SimResult, f: Filter = "all"): DayGroup[] {
  const meta = new Map(r.days.map((d) => [d.day, d]));
  const out: DayGroup[] = [];
  for (const e of r.events) {
    if (!visible(e, f)) continue;
    let g = out[out.length - 1];
    if (!g || g.day !== e.day) {
      const m = meta.get(e.day);
      g = { day: e.day, title: m?.title ?? `День ${e.day}`, date: m?.date ?? "", airs: m?.airs ?? [], events: [] };
      out.push(g);
    }
    g.events.push(e);
  }
  return out;
}

const REASON_RU: Record<string, string> = {
  past_skip: "время прошло",
  parallel_lost: "параллельная ветка потеряна",
  dup: "повтор в окне 90 с",
  claim_cancel: "снят нажатием кнопки",
  edit_end: "показан правкой – цепочка стоит",
  unknown_step: "шага нет",
};

export function reasonRu(r: string): string {
  return REASON_RU[r] ?? r;
}

/** Уникальные выпавшие шаги (шаг может выпасть несколько раз). */
export function uniqueDropped(list: Dropped[]): Dropped[] {
  const seen = new Set<string>();
  return list.filter((d) => {
    const k = `${d.key}:${d.reason}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

/** «2026-09-24T10:00» по МСК сейчас (сервер ждёт время МСК без пояса). */
export function nowMsk(d: Date = new Date()): string {
  const msk = new Date(d.getTime() + (d.getTimezoneOffset() + 180) * 60000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${msk.getFullYear()}-${p(msk.getMonth() + 1)}-${p(msk.getDate())}T${p(msk.getHours())}:${p(msk.getMinutes())}`;
}

/** Сколько дней эфира в воронке – для переключателей «был на эфире дня N»: по шагам с условием
 *  «был / не был на эфире» (день шага = day_offset + 1), иначе по шагам «от эфира». */
export function webinarDays(steps: { day_offset: number; attendance: boolean; webinar: boolean }[]): number {
  const pick = (f: (s: (typeof steps)[number]) => boolean) => steps.filter(f).map((s) => s.day_offset + 1);
  const days = pick((s) => s.attendance);
  const all = days.length ? days : pick((s) => s.webinar);
  return Math.min(7, Math.max(1, ...all));
}
