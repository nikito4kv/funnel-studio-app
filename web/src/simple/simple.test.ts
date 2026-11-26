import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { newStep } from "../model/defaults";
import { KNOWN_TOKENS, PLACEHOLDERS } from "../model/placeholders";
import type { Funnel } from "../model/types";
import { sampleValues } from "../preview/sample";
import { branchKind, branchLong, branchShort, buttonTarget, edgeKindLabel, exitLabel, humanizeTokens, PLACEHOLDER_HUMAN, placeholderLabel } from "./labels";
import { initialMode } from "./mode";
import { stepName, stepRef } from "./names";

const RENDER = join(__dirname, "..", "..", "..", "runtime", "engine_lib", "render.py");
const FIX = join(__dirname, "..", "model", "__fixtures__");
const fixture = (f: string) => JSON.parse(readFileSync(join(FIX, f), "utf-8")) as Funnel;

/** Токены, которые движок подставляет в render.py (out.replace("{…}", …) и цикл по utm_*). */
function renderTokens(src: string): string[] {
  const out = new Set<string>();
  for (const m of src.matchAll(/out\.replace\("(\{[^"{}]+\})"/g)) out.add(m[1]);
  const utm = /for _k in \(([^)]*)\):\s*\n\s*out = out\.replace\("\{" \+ _k \+ "\}"/.exec(src);
  if (utm) for (const m of utm[1].matchAll(/"([a-z_]+)"/g)) out.add(`{${m[1]}}`);
  return [...out];
}

describe("словарь плейсхолдеров", () => {
  it.skipIf(!existsSync(RENDER))("покрывает все токены render.py движка", () => {
    const tokens = renderTokens(readFileSync(RENDER, "utf-8"));
    expect(tokens.length).toBeGreaterThan(40);
    expect(tokens).toContain("{utm_source}");
    const missing = tokens.filter((t) => !PLACEHOLDER_HUMAN[t]);
    expect(missing).toEqual([]);
    expect(tokens.filter((t) => !KNOWN_TOKENS.has(t))).toEqual([]);
  });

  it("у каждого токена палитры есть человеческое название и пример для превью", () => {
    const sample = sampleValues(newStep("s", "k"), fixture("webinar__funnel.json"), { platform: "tg", branch: null });
    for (const p of PLACEHOLDERS) {
      expect(PLACEHOLDER_HUMAN[p.token], p.token).toBeTruthy();
      expect(p.token in sample, p.token).toBe(true);
    }
  });

  it("человеческие слова из задания", () => {
    expect(placeholderLabel("{Имя}", true)).toBe("имя человека");
    expect(placeholderLabel("{channel.link}", true)).toBe("ссылка на канал");
    expect(placeholderLabel("{stream_url}", true)).toBe("ссылка на эфир");
    expect(placeholderLabel("{vip_link}", true)).toBe("оплата ВИП");
    expect(placeholderLabel("{gift_url}", true)).toBe("подарок");
    expect(placeholderLabel("{webinar.time}", true)).toBe("время эфира");
    expect(placeholderLabel("{webinar.main_time}", true)).toBe("время основного эфира");
    expect(placeholderLabel("{webinar.date}", true)).toBe("дата эфира");
    expect(placeholderLabel("{webinar.tgtime}", true)).toBe("дата и время эфира");
    expect(placeholderLabel("{ticket}", true)).toBe("номер билета");
    expect(placeholderLabel("{ref_link}", true)).toBe("реф-ссылка");
    // режим разработчика – как в yaml
    expect(placeholderLabel("{channel.link}", false)).toBe("channel.link");
    expect(placeholderLabel("{нет_такого}", true)).toContain("неизвестная");
    expect(humanizeTokens("Привет, {Имя}! {x}")).toBe("Привет, «имя человека»! {x}");
  });

  it("кнопка: «→ ссылка на канал» вместо {channel.link}", () => {
    expect(buttonTarget("{channel.link}", null, false, true)).toBe("→ ссылка на канал");
    expect(buttonTarget("{channel.link}", null, false, false)).toBe("{channel.link}");
    expect(buttonTarget("https://example.com/x?id={tg_id}", null, false, true)).toBe("→ example.com/x?id=«номер человека в Telegram»");
    expect(buttonTarget(null, "gm:svq", false, true)).toBe("→ действие в боте");
    expect(buttonTarget(null, "gm:svq", false, false)).toBe("действие: gm:svq");
  });
});

describe("слова простого режима", () => {
  it("стрелки и точки выхода", () => {
    expect(edgeKindLabel("also_fire", true)).toBe("одновременно");
    expect(edgeKindLabel("also_fire", false)).toBe("параллельно");
    expect(exitLabel("done", "photo", true, "по времени")).toBe("дальше по времени");
    expect(exitLabel("contact", "phone_request", true, "дал")).toBe("дал номер");
    expect(exitLabel("skip", "phone_request", true, "не дал")).toBe("не дал");
    expect(exitLabel("skip_if_phone_known", "phone_request", true, "уже известен")).toBe("номер уже есть");
    expect(exitLabel("skip", "email_request", true, "не прислал")).toBe("не прислал");
    expect(exitLabel("done", "photo", false, "по времени")).toBe("по времени");
  });

  it("ветки называются по времени эфира", () => {
    const g = fixture("autowebinar__funnel.json");
    const bs = g.branch_router!.branches;
    expect(bs.map((b) => branchLong(b, bs))).toEqual(["дневная ветка · эфир 13:00", "вечерняя ветка · эфир 18:00"]);
    expect(branchShort(bs[1], bs)).toBe("вечерняя · 18:00");
    expect(branchKind({ webinar_time: "11:00" }, 1)).toBe("основная");
    expect(branchKind({ webinar_time: "09:00" }, 2)).toBe("утренняя");
  });

  it("режим по умолчанию – постановщик", () => {
    expect(initialMode(null)).toBe("simple");
    expect(initialMode("dev")).toBe("dev");
    expect(initialMode("мусор")).toBe("simple");
  });

  it("имя шага: название, иначе первая строка без обращения по имени", () => {
    const s = newStep("s", "s_welcome", "photo");
    expect(stepName(s)).toBe("Сообщение с картинкой");
    s.content.tg.text = "<b>{Имя}, привет!</b> Как дела?\nвторая строка";
    expect(stepName(s)).toBe("Привет! Как дела?");
    s.content.tg.text = "🧑‍🍳 {Имя}, через 5 минут эфир";
    expect(stepName(s)).toBe("🧑‍🍳 Через 5 минут эфир");
    s.content.tg.text = "Эфир в {webinar.time}";
    expect(stepName(s)).toBe("Эфир в «время эфира»");
    s.title = "Приветствие";
    expect(stepName(s)).toBe("Приветствие");
    expect(stepRef(s, false)).toBe("s_welcome");
    expect(stepRef(s, true)).toBe("Приветствие");
  });
});
