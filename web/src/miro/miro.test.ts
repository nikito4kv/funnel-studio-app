// Импорт из Miro на стороне холста: замечания в «Проверке», «Проверено», ссылка на элемент доски.
import { describe, expect, it } from "vitest";
import { emptyFunnel, newStep } from "../model/defaults";
import * as ops from "../model/ops";
import type { Funnel } from "../model/types";
import { validate } from "../model/validate";
import { applyFix, simpleIssueText } from "../simple/issues";
import { boardLine } from "./api";

function miroFunnel(): Funnel {
  const g = emptyFunnel("miro", "webinar");
  const s = newStep("00000000-0000-4000-8000-000000000001", "m01", "text");
  s.content.tg.text = "Привет";
  s.miro = {
    item_id: "111", item_ids: ["111"], dismissed: [], board: {},
    doubts: [
      { code: "time_unclear", text: "не поняла время: «когда-нибудь»", item_id: "222", level: "check" },
      { code: "rule_vip", text: "сделано по правилу", item_id: null, level: "info" },
    ],
  };
  g.steps = [s];
  g.miro = {
    board_id: "uXjVGWYprnw=", frame_id: "999", frame_title: "ЗАПУСК", link: "", imported_at: "", entry_item: "111",
    edges_base: [], bot_description: "", dismissed: [],
    doubts: [{ code: "loose_sticker", text: "стикер «через час» не отнесён", item_id: "333", level: "check" }],
  };
  return g;
}

describe("импорт из Miro", () => {
  it("сомнения «проверить» – в панели «Проверка» со ссылкой на элемент доски", () => {
    const g = miroFunnel();
    const miro = validate(g).filter((i) => i.rule === 90);
    expect(miro).toHaveLength(2);           // info-сомнения не показываются
    const step = miro.find((i) => i.stepId)!;
    expect(step.level).toBe("warn");
    expect(step.link).toBe("https://miro.com/app/board/uXjVGWYprnw%3D/?moveToWidget=222");
    expect(simpleIssueText(step, g)).toContain("не поняла время");
    const top = miro.find((i) => !i.stepId)!;
    expect(simpleIssueText(top, g)).toMatch(/^Доска Miro:/);
  });

  it("«Проверено» убирает замечание и запоминает его", () => {
    const g = miroFunnel();
    const [a, b] = validate(g).filter((i) => i.rule === 90);
    let next = applyFix(g, a);
    next = applyFix(next, b);
    expect(validate(next).filter((i) => i.rule === 90)).toHaveLength(0);
    expect(next.steps[0].miro!.dismissed).toEqual(["time_unclear:не поняла время: «когда-нибудь»"]);
    expect(next.miro!.dismissed).toHaveLength(1);
    expect(next.steps[0].miro!.doubts.map((d) => d.level)).toEqual(["info"]);
  });

  it("ссылка на элемент и копия карточки без привязки к доске", () => {
    const g = miroFunnel();
    expect(ops.miroItemUrl(g, "111")).toContain("moveToWidget=111");
    expect(ops.miroItemUrl(g, null)).toContain("moveToWidget=999");
    const dup = ops.duplicateSteps(g, { stepIds: [g.steps[0].id], newId: (() => { let n = 0; return () => `00000000-0000-4000-8000-00000000010${n++}`; })() });
    expect(dup.steps[1].miro).toBeNull();
  });

  it("счёт элементов доски: сколько в шагах, в настройках, заметками, пропущено и почему", () => {
    expect(boardLine(undefined)).toBe("");
    const line = boardLine({
      items: 553, in_steps: 523, settings: 13, notes: 16, skipped: 1, unaccounted: 0,
      skipped_reasons: { "пустая фигура без текста": 1 },
    });
    expect(line).toBe("Доска: 553 элем. – в шагах 523, в настройках 13, заметками 16, пропущено 1 (пустая фигура без текста)");
    expect(boardLine({ items: 3, in_steps: 1, settings: 0, notes: 0, skipped: 0, unaccounted: 2, skipped_reasons: {} }))
      .toContain("не учтено 2");
  });
});
