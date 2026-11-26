import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { autoLanes } from "./lanes";
import type { Funnel } from "./types";

const load = (f: string): Funnel => JSON.parse(readFileSync(join(__dirname, "__fixtures__", f), "utf-8"));
let n = 0;
const id = () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}`;

function byTitle(g: Funnel) {
  const key = new Map(g.steps.map((s) => [s.id, s.key]));
  return Object.fromEntries(autoLanes(g, id).map((l) => [l.title, l.step_ids.map((x) => key.get(x))]));
}

describe("полосы для импортированной воронки", () => {
  it("webinar: регистрация, канал, ВИП, опрос, вне цепочки", () => {
    expect(byTitle(load("webinar__funnel.json"))).toEqual({
      "Регистрация": ["s_confirm", "s_greet", "s_welcome"],
      "Канал": ["s_remind1", "s_remind2"],
      "ВИП": ["s_vipintro", "s_offer", "s_offer_nudge"],
      "Опрос": ["s_survey", "s_q1", "s_q2", "s_q3", "s_thanks"],
      "Вне цепочки": ["s_survey_nudge"],
    });
  });

  it("автовебинар на 3 дня: дни по day_offset, каждый шаг – ровно в одной полосе", () => {
    const g = load("autowebinar__funnel.json");
    const lanes = autoLanes(g, id);
    const titles = lanes.map((l) => l.title);
    expect(titles).toEqual(expect.arrayContaining(["Регистрация", "День 1", "День 3"]));
    const all = lanes.flatMap((l) => l.step_ids);
    expect(all.length).toBe(g.steps.length);
    expect(new Set(all).size).toBe(g.steps.length);
    expect(lanes.map((l) => l.order)).toEqual(lanes.map((_, i) => i));
  });
});
