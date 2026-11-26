// Ветки по времени регистрации (branch_router): проверки окон и разбор суток по веткам.
//
// Как выбирает движок (loader.BranchRouter.pick): берёт время регистрации ЧЧ:ММ по Москве и
// идёт по веткам и окнам по порядку; первое окно, где с ≤ время ≤ по (включительно), побеждает.
// Не подошло ни одно – человек начинает с первого шага воронки БЕЗ даты эфира, и всё, что
// считается от эфира, у него не сработает. Поэтому окна должны покрывать сутки без дыр и
// не пересекаться (при пересечении вторая ветка молча недостижима).
import type { Branch, BranchRouter, Funnel } from "./types";

export const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
const DAY_MIN = 24 * 60;

export function toMin(t: string): number {
  const [h, m] = t.split(":").map(Number);
  return h * 60 + m;
}

export function fmtMin(m: number): string {
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

export interface BranchProblem {
  level: "error" | "warn";
  text: string;
  branchId?: string;
}

export interface Segment {
  from: number;
  to: number;          // включительно
  owners: string[];
}

/** Сутки кусками: [с, по] и чьи это минуты (ветки в порядке проверки) – для шкалы и проверок.
 *  Считается по границам окон, а не поминутно: проверка идёт на каждую правку воронки. */
export function segments(router: BranchRouter): Segment[] {
  const wins: { from: number; to: number; id: string }[] = [];
  for (const b of router.branches) {
    for (const w of b.windows) {
      if (!HHMM.test(w.from) || !HHMM.test(w.to)) continue;
      const from = toMin(w.from);
      const to = toMin(w.to);
      if (from <= to) wins.push({ from, to, id: b.id });
    }
  }
  const cuts = new Set<number>([0, DAY_MIN]);
  for (const w of wins) { cuts.add(w.from); cuts.add(w.to + 1); }
  const points = [...cuts].filter((x) => x >= 0 && x <= DAY_MIN).sort((a, b) => a - b);
  const out: Segment[] = [];
  for (let i = 0; i + 1 < points.length; i++) {
    const from = points[i];
    const to = points[i + 1] - 1;
    const owners: string[] = [];
    for (const w of wins) if (w.from <= from && w.to >= from && !owners.includes(w.id)) owners.push(w.id);
    const last = out[out.length - 1];
    if (last && last.owners.join("|") === owners.join("|")) last.to = to;
    else out.push({ from, to, owners });
  }
  return out;
}

export function branchProblems(g: Funnel, name: (id: string) => string = (id) => `«${id}»`): BranchProblem[] {
  const r = g.branch_router;
  if (!r) return [];
  const out: BranchProblem[] = [];
  const ids = new Set(g.steps.map((s) => s.id));
  const seen = new Set<string>();
  for (const b of r.branches) {
    if (seen.has(b.id)) out.push({ level: "error", branchId: b.id, text: `ветка ${name(b.id)} повторяется – у веток должны быть разные названия` });
    seen.add(b.id);
    if (!ids.has(b.entry_step)) out.push({ level: "error", branchId: b.id, text: `у ветки ${name(b.id)} первый шаг удалён – выберите, с чего она начинается` });
    if (!HHMM.test(b.webinar_time)) out.push({ level: "error", branchId: b.id, text: `ветка ${name(b.id)}: время эфира – ЧЧ:ММ` });
    if (!b.windows.length) out.push({ level: "warn", branchId: b.id, text: `у ветки ${name(b.id)} нет окон регистрации – в неё никто не попадёт` });
    for (const w of b.windows) {
      if (!HHMM.test(w.from) || !HHMM.test(w.to)) {
        out.push({ level: "error", branchId: b.id, text: `ветка ${name(b.id)}: окно «${w.from}–${w.to}» – время пишется ЧЧ:ММ` });
      } else if (toMin(w.from) > toMin(w.to)) {
        out.push({ level: "error", branchId: b.id, text: `ветка ${name(b.id)}: окно с ${w.from} по ${w.to} переходит через полночь – бот так не умеет, разбейте на два: ${w.from}–23:59 и 00:00–${w.to}` });
      }
      if (w.day === "fixed" && !w.date) out.push({ level: "error", branchId: b.id, text: `ветка ${name(b.id)}: у окна «в дату» не указана дата эфира` });
    }
  }
  for (const s of segments(r)) {
    const range = s.from === s.to ? `в ${fmtMin(s.from)}` : `с ${fmtMin(s.from)} по ${fmtMin(s.to)}`;
    if (!s.owners.length) {
      out.push({ level: "error", text: `регистрация ${range} не попадает ни в одну ветку – человек пойдёт без даты эфира, и сообщения «от эфира» ему не придут` });
    } else if (s.owners.length > 1) {
      out.push({ level: "error", text: `регистрация ${range} попадает в несколько веток (${s.owners.map(name).join(", ")}) – сработает только первая, ${name(s.owners[0])}` });
    }
  }
  return out;
}

/** Новая ветка: следующее свободное имя, эфир на 5 часов позже последней, без окон. */
export function newBranch(r: BranchRouter | null, entryStep: string): Branch {
  const taken = new Set(r?.branches.map((b) => b.id) ?? []);
  const base = r?.branches.length ? "B" : "main";
  let id = base;
  for (let i = 2; taken.has(id); i++) id = `${base}${i}`;
  const lastTime = r?.branches[r.branches.length - 1]?.webinar_time;
  const time = lastTime && HHMM.test(lastTime) ? fmtMin(Math.min(toMin(lastTime) + 300, DAY_MIN - 60)) : "11:00";
  return {
    id, entry_step: entryStep, webinar_time: time, repeat_offset_minutes: 0,
    windows: r?.branches.length ? [] : [{ from: "00:00", to: "23:59", day: "today", date: null }],
  };
}

/** Фраза «повтор в тот же день в 18:00» / «повтор назавтра в 13:00». */
export function repeatPhrase(b: Pick<Branch, "webinar_time" | "repeat_offset_minutes">): string {
  if (!b.repeat_offset_minutes || !HHMM.test(b.webinar_time)) return "без повтора";
  const total = toMin(b.webinar_time) + b.repeat_offset_minutes;
  const day = Math.floor(total / DAY_MIN);
  const t = fmtMin(((total % DAY_MIN) + DAY_MIN) % DAY_MIN);
  const when = day === 0 ? "в тот же день" : day === 1 ? "назавтра" : `через ${day} дн.`;
  return `повтор ${when} в ${t}`;
}
