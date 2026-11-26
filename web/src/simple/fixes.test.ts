import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import * as ops from "../model/ops";
import type { Funnel } from "../model/types";
import { validate, type Issue } from "../model/validate";
import { searchSteps } from "../nav/search";
import { buildPreview } from "../preview/model";
import { applyFix, simpleIssueText } from "./issues";

const FIX = join(__dirname, "..", "model", "__fixtures__");
const fixture = (f: string) => JSON.parse(readFileSync(join(FIX, f), "utf-8")) as Funnel;
const byKey = (g: Funnel, k: string) => g.steps.find((s) => s.key === k)!;

function only(g: Funnel, rule: number, stepId?: string): Issue {
  const list = validate(g).filter((i) => i.rule === rule && (!stepId || i.stepId === stepId));
  expect(list.length, `правило ${rule}`).toBeGreaterThan(0);
  return list[0];
}

describe("проверка простыми словами и «Исправить»", () => {
  it("неизвестная вставка → выбрать из списка", () => {
    let g = fixture("webinar__funnel.json");
    const s = byKey(g, "s_remind2");
    g = ops.setText(g, { stepId: s.id, platform: "tg", value: s.content.tg.text + " {подарок_дня}" });
    const i = only(g, 1, s.id);
    expect(i.fix).toBe("token");
    const text = simpleIssueText(i, g);
    expect(text).not.toContain("s_remind2");
    expect(text).toContain("{подарок_дня}");
    expect(text).toContain("Выберите, что подставить");
    g = applyFix(g, i, "{gift_url}");
    expect(byKey(g, "s_remind2").content.tg.text.endsWith(" {gift_url}")).toBe(true);
    expect(validate(g).some((x) => x.rule === 1 && x.stepId === s.id)).toBe(false);
  });

  it("кнопка без цели → выбрать цель; ВИП на подарок → на кассу", () => {
    let g = fixture("webinar__funnel.json");
    const s = byKey(g, "s_offer");
    g = ops.setButtons(g, { stepId: s.id, platform: "tg", buttons: [
      ...s.content.tg.buttons,
      { text: "Куда-то", url: null, callback_data: null, style: null, web_app: false },
      { text: "🎁 ЗАБРАТЬ VIP", url: "{gift_url}", callback_data: null, style: null, web_app: false },
    ] });
    const n = s.content.tg.buttons.length;
    const target = validate(g).find((i) => i.rule === 45 && i.fix === "target" && i.stepId === s.id)!;
    expect(simpleIssueText(target, g)).toContain("«Куда-то» никуда не ведёт");
    g = applyFix(g, target, "{channel.link}");
    expect(byKey(g, "s_offer").content.tg.buttons[n].url).toBe("{channel.link}");
    const vip = only(g, 30, s.id);
    g = applyFix(g, vip);
    expect(byKey(g, "s_offer").content.tg.buttons[n + 1].url).toBe("{vip_link}");
    expect(validate(g).some((x) => (x.rule === 30 || x.rule === 45) && x.stepId === s.id)).toBe(false);
  });

  it("длинное тире, «всегда слать текст», повтор имени, одно время – чинятся одной кнопкой", () => {
    let g = fixture("webinar__funnel.json");
    const s = byKey(g, "s_remind1");
    g = ops.setText(g, { stepId: s.id, platform: "tg", value: "Ждём — тебя" });
    g = applyFix(g, only(g, 4, s.id));
    expect(byKey(g, "s_remind1").content.tg.text).toBe("Ждём – тебя");

    const phone = g.steps.find((x) => x.type === "phone_request")!;
    g = ops.setContact(g, { stepId: phone.id, patch: { text_if_known: "Номер уже есть", always_send_text: false } });
    const i13 = only(g, 13, phone.id);
    expect(simpleIssueText(i13, g)).toContain("всегда слать текст");
    g = applyFix(g, i13);
    expect(g.steps.find((x) => x.id === phone.id)!.contact.always_send_text).toBe(true);

    // дубль технического имени (как бывает после ручной правки json)
    const dup = g.steps[1].key;
    g = { ...g, steps: g.steps.map((x, i) => (i === 2 ? { ...x, key: dup } : x)) };
    const i40 = only(g, 40);
    expect(simpleIssueText(i40, g)).toContain("служебное имя");
    g = applyFix(g, i40);
    expect(validate(g).some((x) => x.rule === 40)).toBe(false);
  });

  it("два шага на одно время → «Сдвинуть на минуту»", () => {
    let g = fixture("webinar__funnel.json");
    const [a, b] = g.steps;
    g = ops.setSchedule(g, { stepId: a.id, patch: { absolute_time_msk: "10:00" } });
    g = ops.setSchedule(g, { stepId: b.id, patch: { absolute_time_msk: "10:00" } });
    g = { ...g, edges: g.edges.filter((e) => !(e.from_step === a.id && e.kind === "done")) };
    g = ops.addEdge(g, { id: "00000000-0000-4000-8000-000000000055", from: a.id, kind: "done", to: b.id });
    const i = only(g, 55, b.id);
    expect(simpleIssueText(i, g)).toContain("Сдвиньте на минуту");
    g = applyFix(g, i);
    expect(g.steps.find((x) => x.id === b.id)!.schedule.absolute_time_msk).toBe("10:01");
    expect(validate(g).some((x) => x.rule === 55)).toBe(false);
  });
});

describe("превью «как в телефоне»", () => {
  it("подпись длиннее 1024 – два сообщения и предупреждение (только TG)", () => {
    let g = fixture("webinar__funnel.json");
    const s = byKey(g, "s_welcome");
    g = ops.setText(g, { stepId: s.id, platform: "tg", value: "а".repeat(1100) });
    const step = byKey(g, "s_welcome");
    const tg = buildPreview(step, g, "tg");
    expect(tg.messages.map((m) => m.kind)).toEqual(["media", "text"]);
    expect(tg.warnings[0]).toContain("двумя сообщениями");
    expect(tg.messages[1].buttons!.length).toBe(step.content.tg.buttons.length);
    const mx = buildPreview(step, g, "max");
    expect(mx.messages.map((m) => m.kind)).toEqual(["media"]);
    expect(mx.warnings).toEqual([]);
  });

  it("MAX: «скрыть» текст, свой текст, кружок видео с подписью", () => {
    let g = fixture("webinar__funnel.json");
    const s = byKey(g, "s_welcome");
    g = ops.setText(g, { stepId: s.id, platform: "max", mode: "hide" });
    let p = buildPreview(byKey(g, "s_welcome"), g, "max");
    expect(p.messages[0].text).toBeUndefined();
    expect(p.notes.join()).toContain("скрыт");
    g = ops.setText(g, { stepId: s.id, platform: "max", mode: "set", value: "Для MAX" });
    p = buildPreview(byKey(g, "s_welcome"), g, "max");
    expect(p.messages[0].text).toBe("Для MAX");

    const circle = g.steps.find((x) => x.type === "video_note")!;
    g = ops.setText(g, { stepId: circle.id, platform: "tg", value: "Сценарий кружка" });
    const c = g.steps.find((x) => x.id === circle.id)!;
    expect(buildPreview(c, g, "tg").messages[0].kind).toBe("circle");
    const withCap = buildPreview(c, { ...g, max_video_note_caption: true }, "max");
    expect(withCap.messages[0]).toMatchObject({ kind: "media", mediaKind: "video", text: "Сценарий кружка" });
    const noCap = buildPreview(c, { ...g, max_video_note_caption: false }, "max");
    expect(noCap.messages[0].text).toBeUndefined();
  });

  it("запрос телефона: кнопка контакта в TG, просьба сообщением в MAX; вариант для источника", () => {
    let g = fixture("webinar__funnel.json");
    const phone = g.steps.find((x) => x.type === "phone_request")!;
    expect(buildPreview(phone, g, "tg").reply).toBe(phone.contact.contact_button);
    g = ops.setContact(g, { stepId: phone.id, patch: { manual_phone_max: true, manual_phone_max_text: "Напишите номер" } });
    const p = buildPreview(g.steps.find((x) => x.id === phone.id)!, g, "max");
    expect(p.reply).toBeNull();
    expect(p.messages.some((m) => m.text === "Напишите номер")).toBe(true);
    const s = byKey(g, "s_remind1");
    g = ops.setVariants(g, { stepId: s.id, variants: [{ when: { utm_source: null, utm_medium: "posev", utm_campaign: null, trigger: null, lm_post_id: null, lm_post_id_in: [], platforms: [] }, text: "Для посевов", text_max: null, buttons: null, claim_button_text: null }] });
    expect(buildPreview(byKey(g, "s_remind1"), g, "tg", 0).messages.some((m) => m.text === "Для посевов")).toBe(true);
  });
});

describe("поиск шага", () => {
  it("находит по тексту и названию, сначала название; ё = е", () => {
    let g = fixture("autowebinar__funnel.json");
    const hits = searchSteps(g, "lesson");
    expect(hits.length).toBeGreaterThan(5);
    const s = g.steps[5];
    g = ops.setMisc(g, { stepId: s.id, patch: { title: "Ёлка и lesson" } });
    const h2 = searchSteps(g, "елка");
    expect(h2[0]).toMatchObject({ stepId: s.id, where: "название" });
    expect(h2[0].snippet[1].toLowerCase()).toBe("ёлка");
    expect(searchSteps(g, "  ")).toEqual([]);
    expect(searchSteps(g, "d1_remind")[0]?.key).toBe("d1_remind");
  });
});
