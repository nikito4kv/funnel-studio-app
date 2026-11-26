import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { emptyFunnel } from "./defaults";
import * as ops from "./ops";
import { autoReplaceAll, autoReplaceAt, unknownTokens } from "./placeholders";
import type { Funnel } from "./types";
import { conditionBadges } from "../phrases";
import { htmlProblems, validate } from "./validate";

let n = 0;
const id = () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}`;
// правило 84 (пустой шаг) проверяется отдельно – в остальных тестах шаги без текста нарочно
const rules = (g: Funnel) => validate(g).map((i) => i.rule).filter((r) => r !== 84).sort((a, b) => a - b);

function base() {
  const a = id();
  const b = id();
  let g = emptyFunnel("t");
  g = ops.addStep(g, { id: a, type: "photo" });
  g = ops.addStep(g, { id: b, type: "text" });
  g = ops.addEdge(g, { id: id(), from: a, kind: "done", to: b });
  g = ops.setMedia(g, { stepId: a, patch: { image: "./static/a.jpg" } });
  g = ops.setText(g, { stepId: a, platform: "tg", value: "Привет" });
  g = ops.setText(g, { stepId: b, platform: "tg", value: "Дальше" });
  return { g, a, b };
}

describe("проверка", () => {
  it("чистая воронка – без замечаний", () => {
    expect(validate(base().g)).toEqual([]);
  });

  it("84 пустой шаг – ошибка, картинка без картинки – предупреждение", () => {
    const { g, a, b } = base();
    let e = ops.setText(g, { stepId: b, platform: "tg", value: " <b> </b> " });
    e = ops.setMisc(e, { stepId: b, patch: { title: "Текст" } });
    const iss = validate(e).filter((i) => i.rule === 84);
    expect(iss.map((i) => [i.level, i.message, i.stepId])).toEqual([["error", "Шаг «Текст» пустой – бот ничего не отправит", b]]);
    const noImg = ops.setMedia(g, { stepId: a, patch: { image: null } });
    expect(validate(noImg).filter((i) => i.rule === 84).map((i) => i.level)).toEqual(["warn"]);
    // MAX с текстом, Telegram пустой – предупреждение, не ошибка
    let mx = ops.setText(g, { stepId: b, platform: "tg", value: "" });
    mx = ops.setText(mx, { stepId: b, platform: "max", mode: "set", value: "только для MAX" });
    expect(validate(mx).filter((i) => i.rule === 84).map((i) => [i.level, i.message])).toEqual([["warn", expect.stringContaining("в Telegram пустой")]]);
  });

  it("webinar: только ожидаемое", () => {
    const g = JSON.parse(readFileSync(join(__dirname, "__fixtures__", "webinar__funnel.json"), "utf-8"));
    const issues = validate(g);
    // s_welcome: подпись длиннее 1024 знаков (нарочно – так проверяется разбиение на два сообщения);
    // s_survey_nudge шлёт game-модуль – стрелок в него нет
    expect(issues.map((i) => i.rule).sort((a, b) => a - b)).toEqual([3, 42]);
  });

  it("83 «кликнул ссылку» – только движок Студии: предупреждение и значок", () => {
    let { g, b } = base();
    g = ops.setConditions(g, { stepId: b, patch: { link_click: "funnel_offer" } });
    const iss = validate(g).filter((i) => i.rule === 83);
    expect(iss).toHaveLength(1);
    expect(iss[0].level).toBe("warn");
    expect(iss[0].message).toContain("только в движке Студии");
    const s = g.steps.find((x) => x.id === b)!;
    expect(conditionBadges(s.conditions)).toEqual([{ icon: "👆", text: "кликнул funnel_offer*" }]);
  });

  it("42 шаг без входящих, 41 параллельная у шага с условием", () => {
    let { g, a, b } = base();
    const c = id();
    g = ops.addStep(g, { id: c, type: "text" });
    expect(rules(g)).toEqual([42]);
    g = ops.addEdge(g, { id: id(), from: b, kind: "also_fire", to: c, delay: 60 });
    g = ops.setConditions(g, { stepId: b, patch: { channel: "not_joined" } });
    expect(rules(g)).toEqual([41]);
    void a;
  });

  it("55 два шага цепочки на одно время", () => {
    let { g, a, b } = base();
    g = ops.setSchedule(g, { stepId: a, patch: { absolute_time_msk: "10:00" } });
    g = ops.setSchedule(g, { stepId: b, patch: { absolute_time_msk: "10:00" } });
    expect(rules(g)).toEqual([55]);
    g = ops.setSchedule(g, { stepId: b, patch: { absolute_time_msk: "10:01" } });
    expect(rules(g)).toEqual([]);
  });

  it("1 плейсхолдер, 2 HTML, 4 тире, 3 длина", () => {
    let { g, a } = base();
    g = ops.setText(g, { stepId: a, platform: "tg", value: "{Имя}, {имя_друга} <b>жирный <i>x</b></i> — <br>" });
    const r = rules(g);
    expect(r).toContain(1);
    expect(r).toContain(2);
    expect(r).toContain(4);
    const dash = validate(g).find((i) => i.rule === 4)!;
    expect(dash.fix).toBe("dashes");
    expect(rules(ops.fixDashes(g))).not.toContain(4);
    g = ops.setText(g, { stepId: a, platform: "tg", value: "x".repeat(1100) });
    expect(validate(g).find((i) => i.rule === 3)?.level).toBe("warn");
    g = ops.setText(g, { stepId: a, platform: "tg", value: "x".repeat(5000) });
    expect(validate(g).find((i) => i.rule === 3)?.level).toBe("error");
  });

  it("45 кнопка без цели, 30 ВИП на подарок, 13 текст при известном телефоне", () => {
    let { g, a, b } = base();
    g = ops.setButtons(g, { stepId: a, platform: "tg", buttons: [{ text: "Куда?" } as never, { text: "ЗАБРАТЬ VIP", url: "{gift_url}" } as never] });
    expect(rules(g)).toEqual([30, 45]);
    g = ops.setType(g, { stepId: b, type: "phone_request" });
    g = ops.setContact(g, { stepId: b, patch: { text_if_known: "мы вас знаем" } });
    expect(rules(g)).toContain(13);
    // у вопроса опроса кнопки-ответы без ссылок – это нормально
    let s = base().g;
    const q = s.steps[1].id;
    s = ops.setSurvey(s, { stepId: q, patch: { qid: "q1" } });
    s = ops.setButtons(s, { stepId: q, platform: "tg", buttons: [{ text: "Да" } as never] });
    expect(rules(s)).toEqual([]);
  });

  it("40 дубль key (из импорта)", () => {
    const { g } = base();
    const dup = { ...g, steps: [...g.steps, { ...g.steps[1], id: id() }] };
    expect(rules(dup)).toContain(40);
  });
});

describe("html и плейсхолдеры", () => {
  it("разбор HTML", () => {
    expect(htmlProblems('<b>a</b> <a href="x">y</a> <tg-time unix="1">z</tg-time>')).toEqual([]);
    expect(htmlProblems("<b>a")).toEqual(["тег <b> не закрыт"]);
    expect(htmlProblems("a</i>")).toEqual(["лишний закрывающий </i>"]);
    expect(htmlProblems("<div>x</div>")[0]).toMatch(/не понимает/);
  });

  it("неизвестные токены и автозамена", () => {
    expect(unknownTokens("{Имя} {webinar.tgtime} {utm_source} {что-то}")).toEqual(["{что-то}"]);
    expect(autoReplaceAt("Привет, [ИМЯ]")).toEqual({ remove: 5, insert: "{Имя}" });
    expect(autoReplaceAt("ИМЯ,")).toEqual({ remove: 4, insert: "{Имя}," });
    expect(autoReplaceAt("раз —")).toEqual({ remove: 1, insert: "–" });
    expect(autoReplaceAt("просто текст")).toBe(null);
    expect(autoReplaceAll("ИМЯ, эфир в ОСНОВНОЕ ВРЕМЯ — ждём")).toBe("{Имя}, эфир в {webinar.main_time} – ждём");
  });
});
