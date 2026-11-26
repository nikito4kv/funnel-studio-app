import { describe, expect, it } from "vitest";
import {
  botfatherHints, cleanCommands, commandsError, counter, initial, sameValue, showValue, TOKEN_STEPS, whereLine,
} from "./text";

describe("раздел «Боты»: слова", () => {
  it("счётчик символов", () => {
    expect(counter("Привет", 64)).toEqual({ text: "6 / 64", over: false });
    expect(counter("😀".repeat(3), 2)).toEqual({ text: "3 / 2", over: true });   // эмодзи – один символ
  });

  it("где работает", () => {
    expect(whereLine({ kind: "studio", slug: "studio-test", title: "studio-test", funnels: [] })).toBe("в Студии: запуск studio-test");
    expect(whereLine({ kind: "repo", slug: "webinar", title: "Spreadsheet Automation Workshop", funnels: [] }))
      .toBe("в bot-engine: запуск webinar – Spreadsheet Automation Workshop");
  });

  it("команды: чистка и проверка как на сервере", () => {
    expect(cleanCommands([{ command: " /start ", description: " Начать " }, { command: "", description: "" }]))
      .toEqual([{ command: "start", description: "Начать" }]);
    expect(commandsError([{ command: "start", description: "Начать" }])).toBeNull();
    expect(commandsError([{ command: "Старт", description: "x" }])).toMatch(/латинские/);
    expect(commandsError([{ command: "help", description: "" }])).toMatch(/описание/);
    expect(commandsError([{ command: "a", description: "x" }, { command: "/a", description: "y" }])).toMatch(/повторяется/);
    expect(commandsError(Array.from({ length: 101 }, (_, i) => ({ command: `c${i}`, description: "x" })))).toMatch(/100/);
  });

  it("было → стало словами", () => {
    expect(showValue("name", "")).toBe("(пусто)");
    expect(showValue("commands", [{ command: "start", description: "Начать" }])).toBe("/start – Начать");
    expect(showValue("commands", [])).toBe("(команд нет)");
    expect(showValue("photo", true)).toBe("новая картинка");
    expect(sameValue("commands", [{ command: "/start", description: "Начать" }], [{ command: "start", description: "Начать" }])).toBe(true);
    expect(sameValue("description", "а ", "а")).toBe(true);
  });

  it("буква вместо аватара", () => {
    expect(initial("тест", "x_bot")).toBe("Т");
    expect(initial("", "x_bot")).toBe("X");
  });

  it("подсказки @BotFather – с командами для копирования и без служебных слов", () => {
    const h = botfatherHints("funnel_studio_TEST_bot", { can_read_all_group_messages: false, supports_inline_queries: false });
    expect(h.map((x) => x.id)).toEqual(["descpic", "inline", "domain", "privacy"]);
    const copies = h.flatMap((x) => x.steps.map((s) => s.copy).filter(Boolean));
    for (const c of ["/setdescriptionpic", "/setinline", "/setdomain", "/newapp", "/setprivacy", "@funnel_studio_TEST_bot"]) {
      expect(copies).toContain(c);
    }
    expect(h.find((x) => x.id === "privacy")!.state).toMatch(/только команды/);
    expect(TOKEN_STEPS.map((s) => s.copy).filter(Boolean)).toEqual(["@BotFather", "/mybots"]);
    const all = JSON.stringify(h) + JSON.stringify(TOKEN_STEPS);
    expect(all).not.toMatch(/getUpdates|[Ww]ebhook|tenant|STUDIO_[A-Z]|движок/);
  });
});
