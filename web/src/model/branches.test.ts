import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { branchProblems, newBranch, repeatPhrase, segments } from "./branches";
import { clearWhen, isEmptyWhen, newEntryVariant, newStepVariant, setWhen, whenFields, whenPhrase } from "./cohort";
import { defaultCohort, emptyFunnel } from "./defaults";
import * as ops from "./ops";
import { OpError } from "./ops";
import type { Funnel } from "./types";
import { validate } from "./validate";

const FIX = join(__dirname, "__fixtures__");
const fixture = (f: string) => JSON.parse(readFileSync(join(FIX, f), "utf-8")) as Funnel;
const five = () => fixture("autowebinar__funnel.json");

describe("ветки: проверки окон", () => {
  it("пятидневный автовебинар: окна покрывают сутки без пересечений", () => {
    const g = five();
    expect(branchProblems(g)).toEqual([]);
    const seg = segments(g.branch_router!);
    expect(seg.map((s) => [s.from, s.to, s.owners.join()])).toEqual([
      [0, 719, "DAY"], [720, 1019, "EVE"], [1020, 1439, "DAY"],
    ]);
    expect(validate(g).filter((i) => i.rule === 82)).toEqual([]);
  });

  it("дыра в сутках и пересечение – ошибки с понятным текстом", () => {
    let g = five();
    // вечерняя ветка теперь до 15:59 – с 16:00 по 16:59 никуда
    g = ops.updateBranch(g, { branchId: "EVE", patch: { windows: [{ from: "12:00", to: "15:59", day: "today", date: null }] } });
    let p = branchProblems(g);
    expect(p.map((x) => x.text)).toEqual([expect.stringContaining("с 16:00 по 16:59 не попадает ни в одну ветку")]);
    // и пересечение 11:00–11:59
    g = ops.updateBranch(g, { branchId: "EVE", patch: { windows: [{ from: "11:00", to: "16:59", day: "today", date: null }] } });
    p = branchProblems(g);
    expect(p).toHaveLength(1);
    expect(p[0].text).toContain("с 11:00 по 11:59 попадает в несколько веток («DAY», «EVE») – сработает только первая, «DAY»");
    expect(validate(g).some((i) => i.rule === 82 && i.level === "error")).toBe(true);
  });

  it("окно через полночь и дата без даты", () => {
    let g = five();
    g = ops.updateBranch(g, { branchId: "DAY", patch: { windows: [{ from: "17:00", to: "11:59", day: "today", date: null }] } });
    // «в дату» без даты операция не пропустит – такой граф бывает только из старого файла
    expect(() => ops.updateBranch(g, { branchId: "EVE", patch: { windows: [{ from: "12:00", to: "16:59", day: "fixed", date: null }] } })).toThrow(OpError);
    const r = g.branch_router!;
    g = { ...g, branch_router: { ...r, branches: r.branches.map((x) => (x.id === "EVE" ? { ...x, windows: [{ from: "12:00", to: "16:59", day: "fixed" as const, date: null }] } : x)) } };
    const texts = branchProblems(g).map((x) => x.text).join("\n");
    expect(texts).toContain("переходит через полночь");
    expect(texts).toContain("не указана дата");
  });

  it("повтор словами", () => {
    expect(repeatPhrase({ webinar_time: "13:00", repeat_offset_minutes: 300 })).toBe("повтор в тот же день в 18:00");
    expect(repeatPhrase({ webinar_time: "18:00", repeat_offset_minutes: 1140 })).toBe("повтор назавтра в 13:00");
    expect(repeatPhrase({ webinar_time: "18:00", repeat_offset_minutes: 0 })).toBe("без повтора");
  });
});

describe("ветки: операции", () => {
  it("переименование ветки правит «своё для ветки» у шагов, остальные шаги не трогает", () => {
    const g = five();
    const withMap = g.steps.filter((s) => s.schedule.webinar_offset_by_branch?.EVE !== undefined || s.schedule.day_offset_by_branch?.EVE !== undefined);
    expect(withMap.length).toBeGreaterThan(0);
    const g2 = ops.updateBranch(g, { branchId: "EVE", patch: { id: "VECHER" } });
    expect(g2.branch_router!.branches.map((b) => b.id)).toEqual(["DAY", "VECHER"]);
    for (const s of g2.steps) {
      expect(s.schedule.webinar_offset_by_branch?.EVE).toBeUndefined();
      expect(s.schedule.day_offset_by_branch?.EVE).toBeUndefined();
    }
    const before = withMap[0];
    const after = g2.steps.find((s) => s.id === before.id)!;
    const m = before.schedule.webinar_offset_by_branch ?? before.schedule.day_offset_by_branch!;
    const m2 = after.schedule.webinar_offset_by_branch ?? after.schedule.day_offset_by_branch!;
    expect(m2.VECHER).toBe(m.EVE);
    // шаг без «своего для ветки» – тот же объект (правки на нём не «сделаются» сами)
    const plain = g.steps.find((s) => !s.schedule.webinar_offset_by_branch && !s.schedule.day_offset_by_branch)!;
    expect(g2.steps.find((s) => s.id === plain.id)).toBe(plain);
  });

  it("проверки операций: время, первый шаг, повтор, дубль", () => {
    const g = five();
    expect(() => ops.updateBranch(g, { branchId: "DAY", patch: { webinar_time: "25:00" } })).toThrow(OpError);
    expect(() => ops.updateBranch(g, { branchId: "DAY", patch: { entry_step: "нет-такого" } })).toThrow(OpError);
    expect(() => ops.updateBranch(g, { branchId: "DAY", patch: { repeat_offset_minutes: -5 } })).toThrow(/повтора/);
    expect(() => ops.updateBranch(g, { branchId: "DAY", patch: { id: "EVE" } })).toThrow(/повторяется/);
    expect(() => ops.updateBranch(g, { branchId: "NOPE", patch: {} })).toThrow(/нет/);
    expect(ops.updateBranch(g, { branchId: "DAY", patch: { repeat_offset_minutes: 240 } }).branch_router!.branches[0].repeat_offset_minutes).toBe(240);
  });

  it("добавить, переставить, удалить; удаление последней выключает ветки", () => {
    let g = emptyFunnel("t", "demo");
    g = ops.addStep(g, { id: "00000000-0000-4000-8000-000000000001", type: "text" });
    const first = newBranch(null, g.steps[0].id);
    expect(first).toMatchObject({ id: "main", webinar_time: "11:00", windows: [{ from: "00:00", to: "23:59", day: "today" }] });
    g = ops.addBranch(g, { branch: first });
    const second = newBranch(g.branch_router, g.steps[0].id);
    expect(second).toMatchObject({ id: "B", webinar_time: "16:00", windows: [] });
    g = ops.addBranch(g, { branch: second });
    expect(branchProblems(g).some((p) => p.text.includes("нет окон"))).toBe(true);
    g = ops.moveBranch(g, { branchId: "B", delta: -1 });
    expect(g.branch_router!.branches.map((b) => b.id)).toEqual(["B", "main"]);
    g = ops.setSchedule(g, { stepId: g.steps[0].id, patch: { absolute_time_msk: "10:00", day_offset_by_branch: { B: 1, main: 0 } } });
    g = ops.removeBranch(g, { branchId: "B" });
    expect(g.steps[0].schedule.day_offset_by_branch).toEqual({ main: 0 });
    g = ops.removeBranch(g, { branchId: "main" });
    expect(g.branch_router).toBeNull();
    expect(g.steps[0].schedule.day_offset_by_branch).toBeNull();
    expect(() => ops.addBranch(g, { branch: { ...first, entry_step: "нет" } })).toThrow(OpError);
  });
});

describe("варианты для источников трафика", () => {
  it("условие: правка полей и фраза", () => {
    let w = defaultCohort();
    expect(isEmptyWhen(w)).toBe(true);
    w = setWhen(w, "utm_medium", " posev ");
    w = setWhen(w, "lm_post_id_in", "2880, 2831;2858");
    w = { ...w, platforms: ["max"] };
    expect(whenFields(w)).toEqual(["utm_medium", "lm_post_id_in"]);
    expect(w.lm_post_id_in).toEqual(["2880", "2831", "2858"]);
    expect(whenPhrase(w)).toBe("метка utm_medium = posev и один из постов: 2880, 2831, 2858 и платформа MAX");
    w = clearWhen(setWhen(w, "utm_medium", ""), "lm_post_id_in");
    expect(w.utm_medium).toBeNull();
    expect(whenFields(w)).toEqual([]);
  });

  it("вариант шага и свой первый шаг проходят через операции", () => {
    let g = fixture("webinar__funnel.json");
    const s = g.steps[1];
    const v = { ...newStepVariant(), text: "Текст для посевов {Имя}" };
    g = ops.setVariants(g, { stepId: s.id, variants: [v] });
    expect(g.steps[1].content.variants).toEqual([v]);
    g = ops.setEntryVariants(g, [newEntryVariant(s.id)]);
    expect(g.entry_variants[0]).toMatchObject({ entry_step: s.id, when: { utm_medium: "posev" } });
    expect(() => ops.setEntryVariants(g, [newEntryVariant("нет-такого")])).toThrow(OpError);
    // варианты тоже проверяются: неизвестная вставка в тексте варианта
    g = ops.setVariants(g, { stepId: s.id, variants: [{ ...v, text: "{подарок_дня}" }] });
    expect(validate(g).some((i) => i.rule === 1 && i.token === "{подарок_дня}")).toBe(true);
  });
});
