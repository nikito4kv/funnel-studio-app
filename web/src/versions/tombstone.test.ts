import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { OpError } from "../model/ops";
import type { Funnel } from "../model/types";
import { validate } from "../model/validate";
import { restoreAsTombstone } from "./tombstone";

const here = dirname(fileURLToPath(import.meta.url));
const load = (f: string) => JSON.parse(readFileSync(join(here, "..", "model", "__fixtures__", f), "utf-8")) as Funnel;

describe("вернуть исчезнувший шаг надгробием", () => {
  it("тот же id и key, переход на выбранный шаг, место на холсте – прежнее", () => {
    const g = load("webinar__funnel.json");
    const gone = g.steps.find((s) => s.key === "s_survey_nudge")!;
    const target = g.steps.find((s) => s.key === "s_thanks")!;
    const pos = g.layout.positions[gone.id] ?? { x: 10, y: 20 };
    const without: Funnel = { ...g, steps: g.steps.filter((s) => s.id !== gone.id) };
    const out = restoreAsTombstone(without, {
      step: { id: gone.id, key: gone.key, type: gone.type, title: "", position: pos }, redirectTo: target.id, edgeId: "e-tomb",
    });
    const t = out.steps.find((s) => s.id === gone.id)!;
    expect(t.type).toBe("jump");
    expect(t.key).toBe("s_survey_nudge");
    expect(t.title).toBe("удалён: был 💬 Текст");
    expect(out.edges.filter((e) => e.from_step === gone.id)).toEqual([
      { id: "e-tomb", from_step: gone.id, kind: "jump", to_step: target.id, to_funnel: null, delay_seconds: null, typing_before: false },
    ]);
    expect(out.layout.positions[gone.id]).toEqual(pos);
    expect(validate(out).filter((i) => i.level === "error")).toEqual([]);
  });
  it("без цели и поверх существующего – ошибка для человека", () => {
    const g = load("webinar__funnel.json");
    const s = g.steps[3];
    expect(() => restoreAsTombstone(g, { step: { ...s, title: "", position: null }, redirectTo: g.steps[0].id, edgeId: "x" })).toThrow(OpError);
    const without: Funnel = { ...g, steps: g.steps.filter((x) => x.id !== s.id) };
    expect(() => restoreAsTombstone(without, { step: { ...s, title: "", position: null }, redirectTo: "нет", edgeId: "x" })).toThrow(/куда/);
  });
});
