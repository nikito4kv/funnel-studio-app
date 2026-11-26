// Фразы времени и условий «на языке доски». Собираются из модели, а не пишутся руками.
// Переключатель ветки в шапке пересчитывает фишки в часы этой ветки («в 13:05» / «в 18:05»).
import type { Branch, Conditions, Edge, Schedule, Step } from "./model/types";

export function duration(sec: number): string {
  if (sec === 0) return "сразу";
  // «2 ч 3 мин», «1 дн 4 ч», «45 сек» – не больше двух единиц, как пишут на доске
  const units: [number, string][] = [[86400, "дн"], [3600, "ч"], [60, "мин"], [1, "сек"]];
  const parts: string[] = [];
  let rest = sec;
  for (const [n, name] of units) {
    if (rest >= n) {
      parts.push(`${Math.floor(rest / n)} ${name}`);
      rest %= n;
    }
    if (parts.length === 2) break;
  }
  return parts.join(" ");
}

export function minutes(m: number): string {
  const a = Math.abs(m);
  if (a === 0) return "0 мин";
  if (a % 60 === 0) return `${a / 60} ч`;
  if (a > 60) return `${Math.floor(a / 60)} ч ${a % 60} мин`;
  return `${a} мин`;
}

export function dayWord(d: number): string {
  if (d === 0) return "день эфира";
  if (d < 0) return `за ${-d} дн. до эфира`;
  return `день ${d + 1}`;
}

/** День для воронки без эфира: движок считает дни от дня входа человека (planner.anchor_msk). */
export function entryDayWord(d: number): string {
  // «после регистрации» – явно: движок считает дни от дня входа, не от предыдущего сообщения
  if (d <= 0) return "в день регистрации";
  if (d === 1) return "на следующий день после регистрации";
  return `через ${d} дн. после регистрации`;
}

function hm(total: number): { day: number; text: string } {
  const day = Math.floor(total / 1440);
  const m = ((total % 1440) + 1440) % 1440;
  return { day, text: `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}` };
}

function toMin(t: string): number {
  const [h, m] = t.split(":").map(Number);
  return h * 60 + m;
}

/** Фраза «когда отправить» по расписанию шага; null – шаг идёт по входящей стрелке. */
export function schedulePhrase(s: Schedule, branch?: Branch | null, names?: Record<string, string>, noWebinar = false): string | null {
  const bn = (id: string) => names?.[id] ?? id;
  if (s.webinar_offset_minutes !== null) {
    const repeat = s.anchor === "repeat";
    const what = repeat ? "повтора" : "эфира";
    const off = (branch && s.webinar_offset_by_branch?.[branch.id]) ?? s.webinar_offset_minutes;
    const rel = off < 0 ? `за ${minutes(off)} до ${what}` : off === 0 ? `в начале ${what}` : `через ${minutes(off)} после начала ${what}`;
    const day = s.day_offset ? `, ${dayWord(s.day_offset)}` : "";
    if (branch) {
      const base = toMin(branch.webinar_time) + (repeat ? branch.repeat_offset_minutes : 0);
      const t = hm(base + off);
      const shift = t.day ? ` (+${t.day} дн)` : "";
      return `в ${t.text}${shift} · ${rel}${day}`;
    }
    const per = s.webinar_offset_by_branch
      ? " (" + Object.entries(s.webinar_offset_by_branch).map(([b, v]) => `${bn(b)}: ${minutes(v)}`).join(" / ") + ")"
      : "";
    return rel + per + day;
  }
  if (s.absolute_time_msk) {
    // в воронке без эфира слова «эфир» нет: «на следующий день после регистрации в 11:00»
    if (noWebinar) return `${entryDayWord(s.day_offset)} в ${s.absolute_time_msk}`;
    if (branch || !s.day_offset_by_branch) {
      const d = (branch && s.day_offset_by_branch?.[branch.id]) ?? s.day_offset;
      return `в ${s.absolute_time_msk}, ${dayWord(d)}`;
    }
    return `в ${s.absolute_time_msk} (` +
      Object.entries(s.day_offset_by_branch).map(([b, d]) => `${bn(b)}: ${dayWord(d)}`).join(" / ") + ")";
  }
  if (s.after_start_seconds !== null) return `через ${duration(s.after_start_seconds)} после входа`;
  return null;
}

export function edgePhrase(e: Edge, from?: Step, simple = false): string {
  const d = e.delay_seconds;
  const after = d ? ` · через ${duration(d)}` : "";
  switch (e.kind) {
    case "done":
      return d ? `через ${duration(d)}` : "сразу";
    case "contact":
      return "дал телефон" + after;
    case "skip":
      return d !== null ? `не дал за ${duration(d)}` : "не дал";
    case "skip_if_phone_known":
      return "телефон известен" + after;
    case "email":
      return "прислал почту" + after;
    case "skip_if_email_known":
      return "почта известна" + after;
    case "callback":
      return from?.content.tg.claim_button_text ? `нажал «${from.content.tg.claim_button_text}»` : "нажал кнопку";
    case "survey_next":
      return "ответил";
    case "jump":
      return "переход";
    case "also_fire":
      return `${simple ? "одновременно" : "параллельно"} · через ${duration(d ?? 0)}`;
    case "chain_to_funnel":
      return `в воронку «${e.to_funnel}»` + after;
  }
}

export interface Badge {
  icon: string;
  text: string;
}

/** Значки условий: шаг ОТПРАВЛЯЕТСЯ, только если… (иначе пропускается). */
export function conditionBadges(c: Conditions): Badge[] {
  const out: Badge[] = [];
  const room = c.bizon_room ? ` · ${c.bizon_room}` : "";
  if (c.attendance === "was") out.push({ icon: "📺", text: `был на эфире${room}` });
  if (c.attendance === "was_not") out.push({ icon: "🙈", text: `не был на эфире${room}` });
  if (c.not_paid) out.push({ icon: "💳", text: "не оплатил ВИП" });
  if (c.paid) out.push({ icon: "💎", text: "оплатил ВИП" });
  if (c.not_course_paid) out.push({ icon: "🎓", text: "не купил курс" });
  if (c.course_paid) out.push({ icon: "🎓", text: "купил курс" });
  if (c.channel === "not_joined") out.push({ icon: "🚪", text: "не в канале" });
  if (c.channel === "joined") out.push({ icon: "🚪", text: "в канале" });
  if (c.survey_done) out.push({ icon: "📋", text: "не прошёл опрос" });
  if (c.has_lm) out.push({ icon: "🎁", text: "есть лидмагнит" });
  if (c.no_link_click) out.push({ icon: "🔗", text: `не кликнул ${c.no_link_click}*` });
  if (c.link_click) out.push({ icon: "👆", text: `кликнул ${c.link_click}*` });
  if (c.no_manager_dm) out.push({ icon: "💬", text: "не написал менеджеру" });
  if (c.max_subscription_gate) out.push({ icon: "Ⓜ", text: "подписан в MAX" });
  if (c.registered_windows?.length) {
    out.push({ icon: "🕘", text: "пришёл не в " + c.registered_windows.map((w) => w.replace("-", "–")).join(", ") });
  }
  return out;
}
