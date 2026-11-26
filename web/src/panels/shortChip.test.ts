import { describe, expect, it } from "vitest";
import { shortChip } from "./OtherPanels";

describe("короткая фраза времени в оглавлении", () => {
  it("целая и короткая", () => {
    expect(shortChip("через сразу после входа")).toBe("сразу");
    expect(shortChip("сразу при входе")).toBe("сразу");
    expect(shortChip("через 5 сек")).toBe("через 5 с");
    expect(shortChip("одновременно · через 3 мин")).toBe("через 3 мин");
    expect(shortChip("через 3 мин")).toBe("через 3 мин");
    expect(shortChip("дал телефон / не дал за 1 мин 30 сек")).toBe("после ответа");
    expect(shortChip("нажал «📝 ПРОЙТИ ОПРОС»")).toBe("по кнопке");
    expect(shortChip("в 09:50 · за 3 ч 10 мин до эфира")).toBe("в 09:50");
    expect(shortChip("в 09:00, день эфира")).toBe("в 09:00");
    expect(shortChip("в 11:00, день 2")).toBe("в 11:00, день 2");
  });
});
