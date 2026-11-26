import { describe, expect, it } from "vitest";
import { groupByDay, iconOf, nowMsk, toneOf, uniqueDropped, visible, webinarDays } from "./format";
import type { SimEvent, SimResult } from "./types";

function ev(p: Partial<SimEvent>): SimEvent {
  return {
    t: "2026-09-24T10:00:00+03:00", at: "24.09 10:00:00", time: "10:00", day: 0, kind: "sent", key: "a",
    step_id: "id-a", type: "text", type_label: "текст", title: "", by: null, text: "", ...p,
  };
}

const result = (events: SimEvent[]): SimResult => ({
  ok: true, registered_at: "", platform: "tg", branch: null,
  launch: { slug: "", found: false, notes: [], dnd_enabled: false },
  events,
  days: [{ day: 0, date: "24.09", title: "День регистрации", airs: ["эфир 1 в 13:00"] },
    { day: 2, date: "26.09", title: "День 2", airs: [] }],
  summary: { sent: 0, sent_steps: 0, late: 0, dnd: 0, replaced: 0, dropped: [], dropped_by_reason: {}, gated: [], stuck: [], truncated: null },
  marks: {},
});

describe("лента симулятора", () => {
  it("тон и значок события", () => {
    expect(toneOf(ev({}))).toBe("ok");
    expect(toneOf(ev({ late_from: "x" }))).toBe("late");
    expect(iconOf(ev({ late_from: "x" }))).toBe("⏳");
    expect(iconOf(ev({ dnd_from: "x" }))).toBe("🌙");
    expect(toneOf(ev({ kind: "past_skip" }))).toBe("drop");
    expect(toneOf(ev({ kind: "parallel_lost" }))).toBe("drop");
    expect(toneOf(ev({ kind: "parallel_lost", resolved: true }))).toBe("info");
    expect(toneOf(ev({ kind: "gated" }))).toBe("gate");
    expect(toneOf(ev({ kind: "human" }))).toBe("human");
    expect(iconOf(ev({ type: "video_note" }))).toBe("◉");
  });

  it("группы по дням – с подписями и эфирами", () => {
    const g = groupByDay(result([ev({}), ev({ kind: "wait" }), ev({ day: 2, kind: "past_skip" })]));
    expect(g.map((x) => [x.title, x.events.length])).toEqual([["День регистрации", 2], ["День 2", 1]]);
    expect(g[0].airs).toEqual(["эфир 1 в 13:00"]);
    const onlyProblems = groupByDay(result([ev({}), ev({ day: 2, kind: "past_skip" })]), "problems");
    expect(onlyProblems.map((x) => x.day)).toEqual([2]);
  });

  it("фильтры", () => {
    expect(visible(ev({ kind: "human" }), "messages")).toBe(false);
    expect(visible(ev({ kind: "replaced" }), "problems")).toBe(true);
    expect(visible(ev({}), "problems")).toBe(false);
  });

  it("выпавшие без повторов", () => {
    const d = { key: "x", step_id: null, reason: "past_skip", text: "", at: "" };
    expect(uniqueDropped([d, d, { ...d, reason: "dup" }])).toHaveLength(2);
  });

  it("время МСК и число дней эфира", () => {
    expect(nowMsk(new Date("2026-09-24T07:05:00Z"))).toBe("2026-09-24T10:05");
    const st = (day_offset: number, attendance = false, webinar = true) => ({ day_offset, attendance, webinar });
    expect(webinarDays([st(0, true), st(4, true), st(5)])).toBe(5);
    expect(webinarDays([st(0), st(2)])).toBe(3);
    expect(webinarDays([st(3, false, false)])).toBe(1);
  });
});
