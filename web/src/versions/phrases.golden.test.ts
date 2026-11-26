// Фразы холста (phrases.ts) и дифф версий на сервере (studio/versions/phrases.py) обязаны
// говорить одними словами. Обе стороны сверяются с одним эталоном – phrases_golden.json
// (пересобрать: uv run python -m studio.versions.golden; pytest сверяет Python с ним же).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { EDGE_KIND_LABEL, STEP_TYPE_LABEL } from "../model/ops";
import type { Funnel } from "../model/types";
import { conditionBadges, duration, edgePhrase, minutes, schedulePhrase } from "../phrases";

const here = dirname(fileURLToPath(import.meta.url));
const golden = JSON.parse(readFileSync(join(here, "__fixtures__", "phrases_golden.json"), "utf-8"));

describe("фразы: TS = эталон = Python", () => {
  it("длительности и минуты", () => {
    for (const [s, v] of Object.entries(golden.duration)) expect(duration(Number(s))).toBe(v);
    for (const [m, v] of Object.entries(golden.minutes)) expect(minutes(Number(m))).toBe(v);
  });
  it("названия типов шагов и стрелок", () => {
    expect(STEP_TYPE_LABEL).toEqual(golden.step_type_label);
    expect(EDGE_KIND_LABEL).toEqual(golden.edge_kind_label);
  });
  for (const file of Object.keys(golden.funnels)) {
    it(`время, условия и стрелки: ${file}`, () => {
      const g = JSON.parse(readFileSync(join(here, "..", "model", "__fixtures__", file), "utf-8")) as Funnel;
      const want = golden.funnels[file];
      const byId = new Map(g.steps.map((s) => [s.id, s]));
      for (const s of g.steps) {
        const w = want.steps[s.id];
        expect(schedulePhrase(s.schedule), s.key).toBe(w.schedule);
        for (const b of g.branch_router?.branches ?? []) expect(schedulePhrase(s.schedule, b), `${s.key}/${b.id}`).toBe(w.by_branch[b.id]);
        expect(conditionBadges(s.conditions).map((x) => `${x.icon} ${x.text}`), s.key).toEqual(w.badges);
      }
      for (const e of g.edges) expect(edgePhrase(e, byId.get(e.from_step)), e.id).toBe(want.edges[e.id]);
    });
  }
});
