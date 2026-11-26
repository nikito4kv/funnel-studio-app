import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { emptyFunnel, newStep } from "./defaults";
import * as ops from "./ops";
import { OpError } from "./ops";
import type { Funnel } from "./types";

const FIX = join(__dirname, "__fixtures__");
const fixture = (f: string) => JSON.parse(readFileSync(join(FIX, f), "utf-8"));

let n = 0;
const id = () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}`;

function two(): { g: Funnel; a: string; b: string } {
  const a = id();
  const b = id();
  let g = emptyFunnel("t", "demo");
  g = ops.addStep(g, { id: a, type: "photo" });
  g = ops.addStep(g, { id: b, type: "text" });
  return { g, a, b };
}

describe("умолчания совпадают с pydantic", () => {
  it("шаг", () => {
    expect(newStep("00000000-0000-0000-0000-000000000000", "k")).toEqual(fixture("default_step.json"));
  });
  it("воронка", () => {
    expect(emptyFunnel("x")).toEqual(fixture("default_funnel.json"));
  });
});

describe("шаги", () => {
  it("addStep даёт уникальный key по типу и позицию", () => {
    const { g, a } = two();
    expect(g.steps.map((s) => s.key)).toEqual(["msg_01", "msg_02"]);
    const g2 = ops.addStep(g, { id: id(), type: "video_note", position: { x: 5, y: 6 } });
    expect(g2.steps[2].key).toBe("circle_01");
    expect(g2.layout.positions[g2.steps[2].id]).toEqual({ x: 5, y: 6 });
    expect(g2.steps[0]).toBe(g.steps[0]); // структурное разделение
    expect(() => ops.addStep(g, { id: a, type: "text" })).toThrow(OpError);
    expect(() => ops.addStep(g, { id: id(), type: "text", key: "msg_01" })).toThrow(/занято/);
    expect(() => ops.addStep(g, { id: id(), type: "text", key: "плохо" })).toThrow(/латиница/);
  });

  it("вопрос опроса получает qid", () => {
    let g = emptyFunnel("t");
    g = ops.addStep(g, { id: id(), type: "text", survey: true });
    g = ops.addStep(g, { id: id(), type: "text", survey: true });
    expect(g.steps.map((s) => [s.key, s.survey.qid])).toEqual([["q1", "q1"], ["q2", "q2"]]);
  });

  it("removeStep: новый шаг без входящих удаляется целиком", () => {
    const { g, a, b } = two();
    const g1 = ops.addEdge(g, { id: id(), from: b, kind: "done", to: a });
    // из b идёт стрелка в a, но в b – нет: b удаляется вместе со своей стрелкой
    const g2 = ops.removeStep(g1, { stepId: b });
    expect(g2.steps.map((s) => s.id)).toEqual([a]);
    expect(g2.edges).toEqual([]);
  });

  it("removeStep: со входящей стрелкой – только надгробие", () => {
    const { g, a, b } = two();
    const c = id();
    let g1 = ops.addStep(g, { id: c, type: "text" });
    g1 = ops.addEdge(g1, { id: id(), from: a, kind: "done", to: b, delay: 5 });
    g1 = ops.addEdge(g1, { id: id(), from: b, kind: "done", to: c });
    g1 = ops.setText(g1, { stepId: b, platform: "tg", value: "привет" });
    expect(() => ops.removeStep(g1, { stepId: b })).toThrow(/перенаправить/);
    const eid = id();
    const g2 = ops.removeStep(g1, { stepId: b, redirectTo: c, edgeId: eid });
    const tomb = g2.steps.find((s) => s.id === b)!;
    expect(tomb.type).toBe("jump");
    expect(tomb.key).toBe("msg_02");
    expect(tomb.content.tg.text).toBe("");
    expect(g2.edges.filter((e) => e.from_step === b)).toEqual([
      { id: eid, from_step: b, kind: "jump", to_step: c, to_funnel: null, delay_seconds: null, typing_before: false },
    ]);
    // входящая стрелка осталась – люди с таймером на b перейдут на c
    expect(g2.edges.some((e) => e.to_step === b)).toBe(true);
  });

  it("removeStep: импортированный шаг (uuid5) – только надгробие", () => {
    const g = fixture("webinar__funnel.json") as Funnel;
    const nudge = g.steps.find((s) => s.key === "s_survey_nudge")!;
    expect(ops.removeBlockers(g, nudge.id)).toEqual([expect.stringMatching(/yaml/)]);
    expect(() => ops.removeStep(g, { stepId: nudge.id })).toThrow(OpError);
  });

  it("setType предупреждает и убирает несовместимое", () => {
    let { g, a, b } = two();
    g = ops.setType(g, { stepId: a, type: "phone_request" });
    g = ops.addEdge(g, { id: id(), from: a, kind: "contact", to: b });
    g = ops.setMedia(g, { stepId: a, patch: { image: "./static/x.jpg" } });
    expect(ops.typeChangeLosses(g, a, "text")).toEqual(["стрелки: дал телефон"]);
    expect(ops.typeChangeLosses(g, a, "sms")).toContain("картинка");
    const g2 = ops.setType(g, { stepId: a, type: "text" });
    expect(g2.edges).toEqual([]);
    expect(g2.steps[0].media.image).toBe("./static/x.jpg");
  });

  it("setKey меняет только имя, id и стрелки на месте", () => {
    let { g, a, b } = two();
    g = ops.addEdge(g, { id: id(), from: a, kind: "done", to: b });
    const g2 = ops.setKey(g, { stepId: b, key: "z_new" });
    expect(g2.steps[1].id).toBe(b);
    expect(g2.steps[1].key).toBe("z_new");
    expect(g2.edges).toBe(g.edges);
    expect(() => ops.setKey(g, { stepId: b, key: "msg_01" })).toThrow(/занято/);
  });

  it("setStartStep переносит шаг в начало", () => {
    const { g, b } = two();
    expect(ops.setStartStep(g, { stepId: b }).steps[0].id).toBe(b);
  });
});

describe("контент по платформам", () => {
  it("setText: TG, MAX как в TG / свой / скрыть", () => {
    let { g, a } = two();
    g = ops.setText(g, { stepId: a, platform: "tg", value: "tg" });
    expect(g.steps[0].content.tg.text).toBe("tg");
    g = ops.setText(g, { stepId: a, platform: "max", mode: "set", value: "max" });
    expect(g.steps[0].content.max.text).toBe("max");
    g = ops.setText(g, { stepId: a, platform: "max", mode: "hide" });
    expect(g.steps[0].content.max.text).toBe("");
    g = ops.setText(g, { stepId: a, platform: "max", mode: "inherit" });
    expect(g.steps[0].content.max.text).toBe(null);
    expect(() => ops.setText(g, { stepId: a, platform: "tg", mode: "inherit" })).toThrow(OpError);
  });

  it("setButtons: MAX пусто = скрыть, null = как в TG", () => {
    let { g, a } = two();
    g = ops.setButtons(g, { stepId: a, platform: "tg", buttons: [{ text: "Канал", url: "{channel.link}" } as never] });
    expect(g.steps[0].content.tg.buttons[0]).toEqual({ text: "Канал", url: "{channel.link}", callback_data: null, style: null, web_app: false });
    g = ops.setButtons(g, { stepId: a, platform: "max", mode: "hide" });
    expect(g.steps[0].content.max.buttons).toEqual([]);
    g = ops.setButtons(g, { stepId: a, platform: "max", mode: "inherit" });
    expect(g.steps[0].content.max.buttons).toBe(null);
  });

  it("setClaim: убрать кнопку-действие – уходит и стрелка «нажал»", () => {
    let { g, a, b } = two();
    expect(() => ops.addEdge(g, { id: id(), from: a, kind: "callback", to: b })).toThrow(/кнопки-действия/);
    g = ops.setClaim(g, { stepId: a, platform: "tg", value: "ЗАБРАТЬ" });
    g = ops.addEdge(g, { id: id(), from: a, kind: "callback", to: b });
    expect(() => ops.addEdge(g, { id: id(), from: a, kind: "callback", to: b })).toThrow(/нажал/);
    g = ops.setClaim(g, { stepId: a, platform: "max", mode: "hide" });
    expect(g.steps[0].content.max.claim_button_text).toBe("");
    g = ops.setClaim(g, { stepId: a, platform: "tg", mode: "hide" });
    expect(g.steps[0].content.tg.claim_button_text).toBe(null);
    expect(g.edges).toEqual([]);
  });

  it("setMedia / setClaimSettings / setVariants / setMisc", () => {
    let { g, a } = two();
    g = ops.setMedia(g, { stepId: a, patch: { image: "./static/a.jpg" } });
    g = ops.setClaimSettings(g, { stepId: a, patch: { keeps_chain: true } });
    g = ops.setMisc(g, { stepId: a, patch: { title: "Приветствие" } });
    expect(g.steps[0].media.image).toBe("./static/a.jpg");
    expect(g.steps[0].claim.keeps_chain).toBe(true);
    expect(g.steps[0].title).toBe("Приветствие");
    expect(() => ops.setMedia(g, { stepId: a, patch: { nope: 1 } as never })).toThrow(/неизвестное поле/);
  });
});

describe("время и условия", () => {
  it("setSchedule проверяет правила движка", () => {
    const { g, a } = two();
    const g1 = ops.setSchedule(g, { stepId: a, patch: { absolute_time_msk: "09:00", day_offset: 1 } });
    expect(g1.steps[0].schedule.absolute_time_msk).toBe("09:00");
    expect(() => ops.setSchedule(g, { stepId: a, patch: { day_offset_by_branch: { A: 1 } } })).toThrow(/точное время/);
    expect(() => ops.setSchedule(g, { stepId: a, patch: { webinar_offset_by_branch: { A: 1 } } })).toThrow(/смещения/);
    expect(() => ops.setSchedule(g, { stepId: a, patch: { absolute_time_msk: "9:00" } })).toThrow(/ЧЧ:ММ/);
    const g2 = ops.setSchedule(g, { stepId: a, patch: { webinar_offset_minutes: -30, anchor: "repeat", webinar_offset_by_branch: { EVE: -60 } } });
    expect(g2.steps[0].schedule.webinar_offset_by_branch).toEqual({ EVE: -60 });
  });

  it("setConditions: противоречие запрещено", () => {
    const { g, a } = two();
    const g1 = ops.setConditions(g, { stepId: a, patch: { attendance: "was_not", bizon_room: "room1" } });
    expect(g1.steps[0].conditions.attendance).toBe("was_not");
    expect(() => ops.setConditions(g, { stepId: a, patch: { paid: true, not_paid: true } })).toThrow(/не уйдёт/);
  });

  it("часы регистрации и «не раньше чем через» – правила движка (reg_window, min_delay_seconds)", () => {
    const { g, a } = two();
    const g1 = ops.setConditions(g, { stepId: a, patch: { registered_windows: ["11:00-12:00", "16:00-24:00"] } });
    expect(g1.steps[0].conditions.registered_windows).toEqual(["11:00-12:00", "16:00-24:00"]);
    expect(() => ops.setConditions(g, { stepId: a, patch: { registered_windows: ["12:00-11:00"] } })).toThrow(/раньше конца/);
    expect(() => ops.setConditions(g, { stepId: a, patch: { registered_windows: ["9:00-10:00"] } })).toThrow(/ЧЧ:ММ/);
    expect(() => ops.setConditions(g, { stepId: a, patch: { registered_windows: ["23:00-24:30"] } })).toThrow(/такого времени нет/);
    expect(ops.windowProblem("00:00-24:00")).toBeNull();
    const g2 = ops.setSchedule(g, { stepId: a, patch: { absolute_time_msk: "09:00", min_delay_seconds: 360 } });
    expect(g2.steps[0].schedule.min_delay_seconds).toBe(360);
    expect(() => ops.setSchedule(g, { stepId: a, patch: { min_delay_seconds: -5 } })).toThrow(/меньше нуля/);
    const g3 = ops.setFunnelMeta(g, { resend_entry_on_repeat: true });
    expect(g3.resend_entry_on_repeat).toBe(true);
  });

  it("setContact / setEmail / setSurvey / setCall / setSms / setPin", () => {
    let { g, a, b } = two();
    g = ops.setContact(g, { stepId: a, patch: { skip_if_known: true } });
    g = ops.setEmail(g, { stepId: a, patch: { retry_text: "ещё раз" } });
    g = ops.setCall(g, { stepId: a, patch: { audio_id: 42 } });
    g = ops.setSms(g, { stepId: a, patch: { text: "sms" } });
    g = ops.setPin(g, { stepId: a, patch: { pin: true } });
    g = ops.setSurvey(g, { stepId: b, patch: { qid: "q1" } });
    g = ops.addEdge(g, { id: id(), from: b, kind: "survey_next", to: a });
    expect(() => ops.setSurvey(g, { stepId: b, patch: { qid: null } })).toThrow(/следующий вопрос/);
    expect(() => ops.setCall(g, { stepId: a, patch: { audio_id: 1.5 } })).toThrow();
    const s = g.steps[0];
    expect([s.contact.skip_if_known, s.email.retry_text, s.call.audio_id, s.sms.text, s.pin.pin]).toEqual([true, "ещё раз", 42, "sms", true]);
  });
});

describe("стрелки", () => {
  it("done – одна, also_fire – сколько угодно, но не дублем", () => {
    let { g, a, b } = two();
    const c = id();
    g = ops.addStep(g, { id: c, type: "text" });
    g = ops.addEdge(g, { id: id(), from: a, kind: "done", to: b, delay: 10 });
    expect(() => ops.addEdge(g, { id: id(), from: a, kind: "done", to: c })).toThrow(/по времени/);
    g = ops.addEdge(g, { id: id(), from: a, kind: "also_fire", to: c, delay: 180 });
    g = ops.addEdge(g, { id: id(), from: a, kind: "also_fire", to: b, delay: 60 });
    expect(() => ops.addEdge(g, { id: id(), from: a, kind: "also_fire", to: c })).toThrow(/уже есть/);
    expect(() => ops.addEdge(g, { id: id(), from: a, kind: "done", to: a })).toThrow();
    expect(g.edges.map((e) => [e.kind, e.delay_seconds])).toEqual([["done", 10], ["also_fire", 180], ["also_fire", 60]]);
  });

  it("стрелки контакта только у запроса телефона; skip – без таймаута по умолчанию", () => {
    let { g, a, b } = two();
    expect(() => ops.addEdge(g, { id: id(), from: a, kind: "contact", to: b })).toThrow(/запроса телефона/);
    g = ops.setType(g, { stepId: a, type: "phone_request" });
    g = ops.addEdge(g, { id: id(), from: a, kind: "skip", to: b });
    expect(g.edges[0].delay_seconds).toBe(null);
    const e = g.edges[0].id;
    g = ops.setEdgeDelay(g, { edgeId: e, delay: 10 });
    expect(g.edges[0].delay_seconds).toBe(10);
    expect(() => ops.setEdgeDelay(g, { edgeId: e, delay: -1 })).toThrow();
  });

  it("задержка done и перехода в воронку – общая", () => {
    let { g, a, b } = two();
    const d = id();
    g = ops.addEdge(g, { id: d, from: a, kind: "done", to: b, delay: 30 });
    const ch = id();
    g = ops.addEdge(g, { id: ch, from: a, kind: "chain_to_funnel", toFunnel: "alt", typing: true });
    expect(g.edges.find((e) => e.id === ch)!.delay_seconds).toBe(30);
    g = ops.setEdgeDelay(g, { edgeId: ch, delay: 90 });
    expect(g.edges.map((e) => e.delay_seconds)).toEqual([90, 90]);
    g = ops.setEdgeFunnel(g, { edgeId: ch, toFunnel: "lmnew" });
    expect(g.edges[1].to_funnel).toBe("lmnew");
  });

  it("setEdgeKind, retargetEdge, removeEdge", () => {
    let { g, a, b } = two();
    const c = id();
    g = ops.addStep(g, { id: c, type: "text" });
    const e = id();
    g = ops.addEdge(g, { id: e, from: a, kind: "done", to: b, delay: 5 });
    g = ops.setEdgeKind(g, { edgeId: e, kind: "also_fire" });
    expect(g.edges[0]).toMatchObject({ kind: "also_fire", delay_seconds: 5 });
    g = ops.retargetEdge(g, { edgeId: e, to: c });
    expect(g.edges[0].to_step).toBe(c);
    expect(() => ops.setEdgeKind(g, { edgeId: e, kind: "contact" })).toThrow();
    g = ops.removeEdge(g, { edgeId: e });
    expect(g.edges).toEqual([]);
  });

  it("надгробие: из jump – только переход", () => {
    let { g, a, b } = two();
    g = ops.setType(g, { stepId: a, type: "jump" });
    expect(() => ops.addEdge(g, { id: id(), from: a, kind: "done", to: b })).toThrow(/надгробия/);
    g = ops.addEdge(g, { id: id(), from: a, kind: "jump", to: b });
    expect(g.edges[0].delay_seconds).toBe(null);
  });
});

describe("воронка, полосы, заметки", () => {
  it("branch_router и entry_variants ссылаются на существующие шаги", () => {
    const { g, a } = two();
    const router = { type: "time_of_day_msk" as const, branches: [{ id: "M", entry_step: a, webinar_time: "11:00", repeat_offset_minutes: 0, windows: [{ from: "00:00", to: "23:59", day: "today" as const, date: null }] }] };
    expect(ops.setBranchRouter(g, router).branch_router).toEqual(router);
    expect(() => ops.setBranchRouter(g, { ...router, branches: [{ ...router.branches[0], entry_step: id() }] })).toThrow();
    expect(() => ops.setBranchRouter(g, { ...router, branches: [{ ...router.branches[0], windows: [{ from: "00:00", to: "23:59", day: "fixed", date: null }] }] })).toThrow(/дата/);
    expect(() => ops.setEntryVariants(g, [{ when: {} as never, entry_step: id() }])).toThrow();
    expect(ops.setFunnelMeta(g, { air_email_profile: "lm5" }).air_email_profile).toBe("lm5");
  });

  it("полосы: добавить, положить карточку, переименовать, удалить", () => {
    let { g, a, b } = two();
    const l1 = id();
    const l2 = id();
    g = ops.addLane(g, { id: l1, title: "Вход", y: 0 });
    g = ops.addLane(g, { id: l2, title: "День 1", kind: "day", day: 0, y: 600 });
    g = ops.assignLane(g, { stepIds: [a, b], laneId: l1 });
    g = ops.assignLane(g, { stepIds: [b], laneId: l2 });
    expect(g.lanes.map((l) => [l.title, l.order, l.step_ids])).toEqual([["Вход", 0, [a]], ["День 1", 1, [b]]]);
    expect(g.layout.positions[l2]).toEqual({ x: 0, y: 600 });
    g = ops.updateLane(g, { laneId: l2, patch: { title: "День 2" } });
    g = ops.removeLane(g, { laneId: l1 });
    expect(g.lanes.map((l) => l.title)).toEqual(["День 2"]);
    expect(g.layout.positions[l1]).toBeUndefined();
  });

  it("заметки и правки: номер сквозной, статусы по порядку", () => {
    let { g, a } = two();
    const n1 = id();
    const r1 = id();
    const r2 = id();
    g = ops.addNote(g, { id: n1, text: "не забыть", position: { x: 1, y: 2 } });
    g = ops.addRevision(g, { id: r1, text: "заменить картинку", action: "replace_media", stepId: a, author: "я" });
    g = ops.addRevision(g, { id: r2, text: "время", action: "replace_time", stepId: a });
    expect(g.notes.map((x) => [x.kind, x.number, x.status])).toEqual([["note", null, null], ["revision", 1, "open"], ["revision", 2, "open"]]);
    expect(() => ops.setRevisionStatus(g, { noteId: r1, status: "accepted" })).toThrow();
    g = ops.setRevisionStatus(g, { noteId: r1, status: "done" });
    g = ops.setRevisionStatus(g, { noteId: r1, status: "accepted" });
    g = ops.updateNote(g, { noteId: n1, patch: { text: "готово" } });
    g = ops.removeNote(g, { noteId: r2 });
    expect(g.notes.map((x) => [x.text, x.status])).toEqual([["готово", null], ["заменить картинку", "accepted"]]);
    expect(() => ops.addNote(g, { id: id(), text: "висит в воздухе" })).toThrow(/холст/);
    // удалённый шаг: заметка становится свободной
    g = ops.removeStep(g, { stepId: a });
    expect(g.notes[1].step_id).toBe(null);
    expect(g.notes[1].position).not.toBe(null);
  });
});

describe("копирование и исправления", () => {
  it("duplicateSteps: новые id, key с суффиксом, внутренние стрелки", () => {
    let { g, a, b } = two();
    g = ops.addEdge(g, { id: id(), from: a, kind: "done", to: b, delay: 7 });
    g = ops.setPositions(g, { positions: { [a]: { x: 0, y: 0 } } });
    const map = new Map<string, string>();
    const g2 = ops.duplicateSteps(g, { stepIds: [a, b], newId: (old) => { const v = id(); map.set(old, v); return v; } });
    expect(g2.steps.map((s) => s.key)).toEqual(["msg_01", "msg_02", "msg_01_copy", "msg_02_copy"]);
    expect(g2.edges[1]).toMatchObject({ from_step: map.get(a), to_step: map.get(b), delay_seconds: 7 });
    expect(g2.layout.positions[map.get(a)!]).toEqual({ x: 40, y: 40 });
    const g3 = ops.duplicateSteps(g2, { stepIds: [a], newId: id });
    expect(g3.steps[4].key).toBe("msg_01_copy2");
  });

  it("fixDashes меняет только длинное тире", () => {
    let { g, a } = two();
    g = ops.setText(g, { stepId: a, platform: "tg", value: "раз — два – три" });
    g = ops.setButtons(g, { stepId: a, platform: "tg", buttons: [{ text: "А — Б", url: "x" } as never] });
    const g2 = ops.fixDashes(g);
    expect(g2.steps[0].content.tg.text).toBe("раз – два – три");
    expect(g2.steps[0].content.tg.buttons[0].text).toBe("А – Б");
    expect(g2.steps[1]).toBe(g.steps[1]);
  });
});
