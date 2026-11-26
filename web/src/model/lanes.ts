// Полосы для импортированной воронки: раскладываем шаги по смыслу, как на досках –
// «Регистрация», «Канал», «ВИП», «Опрос», «День N», «Вне цепочки».
//
// Правила (первое подошедшее):
//   1. вопрос опроса и шаг, чья кнопка-действие ведёт в вопрос, – «Опрос»;
//   2. шаг со своим временем (по часам или от эфира) – «День N» по day_offset;
//   3. остальные идут по стрелкам от входа и наследуют полосу предыдущего шага; в фазе
//      регистрации дожимы канала (условие «не в канале») уходят в «Канал», ВИП-шаги – в «ВИП»;
//   4. куда воронка не доходит (шаг шлёт внешний модуль) – «Вне цепочки».
import { findEntries } from "./graphinfo";
import type { Funnel, Lane, Step, Uuid } from "./types";

const REG = "Регистрация";
const CHANNEL = "Канал";
const VIP = "ВИП";
const SURVEY = "Опрос";
const OUTSIDE = "Вне цепочки";

function scheduledDay(s: Step): number | null {
  const sc = s.schedule;
  if (sc.webinar_offset_minutes === null && !sc.absolute_time_msk) return null;
  if (sc.absolute_time_msk && sc.day_offset_by_branch) {
    return Math.min(...Object.values(sc.day_offset_by_branch), sc.day_offset);
  }
  return sc.day_offset;
}

export function dayLaneTitle(d: number, multiDay: boolean): string {
  if (d < 0) return `За ${-d} дн. до эфира`;
  return multiDay ? `День ${d + 1}` : "День эфира";
}

export function autoLanes(g: Funnel, newId: () => Uuid): Lane[] {
  const byId = new Map(g.steps.map((s) => [s.id, s]));
  const out = new Map<Uuid, { to: Uuid; kind: string }[]>();
  for (const e of g.edges) {
    if (!e.to_step || !byId.has(e.to_step)) continue;
    const arr = out.get(e.from_step) ?? [];
    arr.push({ to: e.to_step, kind: e.kind });
    out.set(e.from_step, arr);
  }
  const days = g.steps.map(scheduledDay).filter((d): d is number => d !== null);
  const multiDay = days.some((d) => d > 0);
  const dayTitle = (d: number) => dayLaneTitle(d, multiDay);

  const own = (s: Step): string | null => {
    if (s.survey.qid) return SURVEY;
    const toQuestion = (out.get(s.id) ?? []).some((e) => e.kind === "callback" && byId.get(e.to)?.survey.qid);
    if (toQuestion) return SURVEY;
    const d = scheduledDay(s);
    if (d !== null) return dayTitle(d);
    return null;
  };
  const inherit = (parent: string, s: Step): string => {
    if (parent !== REG) return parent;
    if (s.conditions.channel === "not_joined") return CHANNEL;
    if (s.conditions.not_paid || s.conditions.paid) return VIP;
    return REG;
  };

  const laneOf = new Map<Uuid, string>();
  const queue: Uuid[] = [];
  for (const id of findEntries(g)) {
    const s = byId.get(id);
    if (!s || laneOf.has(id)) continue;
    laneOf.set(id, own(s) ?? inherit(REG, s));
    queue.push(id);
  }
  while (queue.length) {
    const cur = queue.shift()!;
    for (const e of out.get(cur) ?? []) {
      if (laneOf.has(e.to)) continue;
      const s = byId.get(e.to)!;
      laneOf.set(e.to, own(s) ?? inherit(laneOf.get(cur)!, s));
      queue.push(e.to);
    }
  }
  for (const s of g.steps) if (!laneOf.has(s.id)) laneOf.set(s.id, own(s) ?? OUTSIDE);

  const dayTitles = [...new Set(days)].sort((a, b) => a - b).map(dayTitle);
  const order = [REG, CHANNEL, VIP, ...dayTitles, SURVEY, OUTSIDE];
  const lanes: Lane[] = [];
  for (const title of order) {
    const steps = g.steps.filter((s) => laneOf.get(s.id) === title).map((s) => s.id);
    if (!steps.length || lanes.some((l) => l.title === title)) continue;
    const day = days.find((d) => dayTitle(d) === title);
    lanes.push({
      id: newId(), title, order: lanes.length, step_ids: steps,
      kind: title === REG ? "entry" : title === CHANNEL ? "channel" : title === VIP ? "vip"
        : title === SURVEY ? "survey" : title === OUTSIDE ? "outside" : "day",
      day: day ?? null,
    });
  }
  return lanes;
}
