import { describe, expect, it } from "vitest";
import {
  actionLabel, botBadge, botName, botOption, consequences, headline, mergeRedirects, onlyTestBots, people,
  peopleLines, publishGate, removedLines, sendLabel, sentOk,
  type EngineCheck, type EngineLine, type EngineReport, type EngineSend, type FunnelEngine, type GateState,
  type Tenant,
} from "./engine";

const line = (p: Partial<EngineLine>): EngineLine => ({
  step_id: p.key ?? "x", key: "x", pending: 0, awaiting: 0, awaiting_kinds: {}, status: "same", redirect: null, ...p,
});

const report = (p: Partial<EngineReport>): EngineReport => ({
  tenant: "studio-test", funnel_id: "f", from_version: 2, to_version: 3, mode: "new-only",
  positions_active: 0, pending_total: 0, lines: [], errors: [], warnings: [], migrated: 0, retimed: 0,
  skipped_past: 0, dry_run: true, ...p,
});

const send = (p: Partial<EngineSend>): EngineSend => ({
  id: 1, number: 1, tenant: "t1", funnel_id: "f", mode: "new-only", status: "ok", ok: true, engine_version: 1,
  author: "я", error: "", redirects: {}, report: {}, created_at: "2026-09-24T10:00:00+00:00", ...p,
});

describe("отчёт по живым людям словами", () => {
  it("склоняет «человек»", () => {
    expect([1, 2, 5, 11, 21, 22, 112].map(people)).toEqual([
      "1 человек", "2 человека", "5 человек", "11 человек", "21 человек", "22 человека", "112 человек",
    ]);
  });

  it("запланированные, ждущие контакт и почту, судьба шага по режиму", () => {
    const rep = report({
      positions_active: 7, pending_total: 5,
      lines: [
        line({ key: "b", step_id: "b", pending: 3, status: "changed" }),
        line({ key: "ask", step_id: "ask", awaiting: 2, awaiting_kinds: { contact: 1, email: 1 } }),
        line({ key: "t", step_id: "t", pending: 2, status: "time" }),
        line({ key: "idle", step_id: "idle" }),
      ],
    });
    const ls = peopleLines(rep);
    expect(ls.map((l) => l.key)).toEqual(["b", "ask", "t"]);                // пустые шаги не показываем
    expect(ls[0].text).toBe("у 3 человека запланирован этот шаг");
    expect(ls[0].fate).toContain("останется старый вариант");
    expect(ls[1].text).toBe("1 человек ждёт, когда пришлют контакт, 1 человек ждёт, когда пришлют почту");
    expect(ls[2].tone).toBe("warn");
    expect(ls[2].fate).toContain("у идущих останется старое");
    const mig = peopleLines({ ...rep, mode: "migrate" });
    expect(mig[0].fate).toBe("получат новый вариант шага");
    expect(mig[2].fate).toContain("пересчитаем");
    expect(headline(rep, "@x_bot")).toBe(
      "В боте @x_bot работает прошлая версия этой воронки – её сменит новая. Внутри воронки 7 человек, запланированных сообщений – 5.");
    expect(headline(report({ from_version: null, to_version: 1 }), "@x_bot")).toBe(
      "В боте @x_bot этой воронки ещё нет – она появится там впервые. Людей внутри нет.");
  });

  it("исчезнувший шаг: без карты в migrate – красным, с картой – куда переведём", () => {
    const gone = line({ key: "old", step_id: "id-old", pending: 2, status: "removed" });
    const keyOf = (id: string) => ({ "id-b": "b" })[id];
    expect(peopleLines(report({ mode: "migrate", lines: [gone] }), keyOf)[0]).toMatchObject({
      tone: "bad", fate: "шаг исчез – укажите, куда их перевести",
    });
    expect(peopleLines(report({ mode: "migrate", lines: [{ ...gone, redirect: "b" }] }), keyOf)[0].fate)
      .toBe("шаг исчез – переведём на «b»");
    expect(peopleLines(report({ lines: [gone] }))[0]).toMatchObject({ tone: "warn", fate: "шаг исчез – доедут по старой версии" });
  });
});

describe("карта перенаправления", () => {
  const rep = report({
    lines: [
      line({ key: "old", step_id: "o", pending: 2, status: "removed" }),
      line({ key: "old2", step_id: "o2", awaiting: 1, status: "removed" }),
      line({ key: "gone-empty", step_id: "g", status: "removed" }),
      line({ key: "b", step_id: "b", pending: 1 }),
    ],
  });
  it("только исчезнувшие шаги с людьми", () => {
    expect(removedLines(rep).map((l) => l.key)).toEqual(["old", "old2"]);
  });
  it("подсказка из диффа, поверх – выбор человека; «не выбрано» убирает подсказку", () => {
    const rm = removedLines(rep);
    expect(mergeRedirects({ o: "b", o2: "c" }, {}, rm)).toEqual({ o: "b", o2: "c" });
    expect(mergeRedirects({ o: "b", o2: "c" }, { o: "d" }, rm)).toEqual({ o: "d", o2: "c" });
    expect(mergeRedirects({ o: "b", o2: "c" }, { o2: "" }, rm)).toEqual({ o: "b" });
    expect(mergeRedirects({ zzz: "b" }, {}, rm)).toEqual({});
  });
});

describe("кнопка «Запустить в боте»", () => {
  const ok: EngineCheck = { ok: true, error: null, tenant: "t1", funnel_id: "f", mode: "new-only", report: null, text: "", suggestions: {} };
  const base: GateState = { studioBlockers: 0, studioReady: true, engineOn: true, tenant: "t1", check: ok, checking: false, busy: false };
  it("блокеры Студии блокируют по-прежнему", () => {
    expect(publishGate({ ...base, studioBlockers: 1 }).ok).toBe(false);
    expect(publishGate({ ...base, studioReady: false }).ok).toBe(false);
  });
  it("боты не подключены или бот не выбран – просто сохранить версию", () => {
    expect(publishGate({ ...base, engineOn: false, check: null, tenant: "" })).toEqual({ ok: true, why: null });
    expect(publishGate({ ...base, tenant: "", check: null })).toEqual({ ok: true, why: null });
  });
  it("нужен ответ бота; отказ бота не даёт запустить", () => {
    expect(publishGate({ ...base, studioBlockers: 2 }).why).toBe("Сначала исправьте проблемы, отмеченные красным");
    expect(publishGate({ ...base, checking: true }).ok).toBe(false);
    expect(publishGate({ ...base, check: { ...ok, ok: false, error: "шаг «old» удалён" } })).toEqual({
      ok: false, why: "Бот не примет эту версию – причина выше",
    });
    expect(publishGate(base)).toEqual({ ok: true, why: null });
    expect(publishGate({ ...base, busy: true }).ok).toBe(false);
  });
});

describe("журнал отправок", () => {
  it("строка истории: куда, каким режимом, или почему не ушла", () => {
    expect(sendLabel(send({ tenant: "studio-test", engine_version: 4, mode: "migrate" }), "@x_bot"))
      .toBe("→ запущена в боте @x_bot · перевести всех");
    expect(sendLabel(send({ ok: false, status: "failed", engine_version: null, error: "гонка" })))
      .toBe("✕ в бот «t1» не ушла: гонка");
  });
  it("ушла ли опубликованная версия в этот запуск", () => {
    const h = [send({ number: 2, ok: false, status: "failed" }), send({ number: 2, tenant: "t2" }), send({ number: 1 })];
    expect(sentOk(h, 2, "t1")).toBeUndefined();
    expect(sentOk(h, 2, "t2")?.tenant).toBe("t2");
    expect(sentOk(h, 1, "t1")?.number).toBe(1);
    expect(sentOk(h, null, "t1")).toBeUndefined();
  });
});

// ── бот: имя, последствия, бейдж шапки (живой тест 24.09) ──────────────────

const tenant = (p: Partial<Tenant> = {}): Tenant => ({
  slug: "studio-test", title: "Тест Студии", status: "active", entry_funnel: "e2e_funnel",
  bots: [{ platform: "tg", username: "funnel_studio_TEST_bot", enabled: true, test: true }], ...p,
});

const engine = (p: Partial<FunnelEngine> = {}): FunnelEngine => ({
  configured: true, where: "db", error: null, tenants: [tenant()], tenant: "studio-test", funnel_id: "e2e_funnel",
  state: null, history: [], in_bot: { engine_version: 2, studio_number: 2, published_at: "2026-09-24T10:00:00+00:00", published_by: "Alex" },
  is_entry: true, test_link: "https://t.me/funnel_studio_TEST_bot?start=studio_test_e2e_funnel", ...p,
});

describe("бот словами", () => {
  it("имя бота и пункт выбора", () => {
    expect(botName(tenant())).toBe("@funnel_studio_TEST_bot");
    expect(botOption(tenant())).toBe("@funnel_studio_TEST_bot (Тест Студии)");
    expect(botOption(tenant({ bots: [], title: "E2E" }))).toBe("E2E – бот не подключён");
    expect(botName(tenant({ bots: [{ platform: "max", username: "id1_bot", enabled: true, test: true }] }))).toBe("@id1_bot (MAX)");
    expect(botName(tenant({ bots: [] }))).toBe("«Тест Студии»");
  });
  it("«боевые не затрагиваются» – только если все боты запуска тестовые", () => {
    expect(onlyTestBots(tenant())).toBe(true);
    expect(onlyTestBots(tenant({ bots: [{ platform: "tg", username: "northwind_extra_bot", enabled: true, test: false }] }))).toBe(false);
    expect(onlyTestBots(tenant({ bots: [] }))).toBe(false);
  });
  it("последствия запуска одной-двумя фразами", () => {
    expect(consequences(report({ positions_active: 1 }), "new-only", true))
      .toBe("Новые люди получат эту версию. 1 человек уже внутри – он дойдёт по старой. Боевые боты не затрагиваются.");
    expect(consequences(report({ positions_active: 3 }), "new-only", false))
      .toBe("Новые люди получат эту версию. 3 человека уже внутри – они дойдут по старой.");
    expect(consequences(report({ from_version: null }), "new-only", false)).toBe("Новые люди пойдут по этой воронке.");
    expect(consequences(report({ positions_active: 3 }), "migrate", false))
      .toBe("Все – и новые люди, и те, кто уже внутри, – перейдут на эту версию.");
    expect(consequences(null, "new-only", true)).toBe("");
  });
  it("кнопка – по действию", () => {
    expect(actionLabel({ bot: "@funnel_studio_TEST_bot", busy: false })).toBe("Запустить в боте @funnel_studio_TEST_bot");
    expect(actionLabel({ bot: null, busy: false })).toBe("Сохранить версию");
    expect(actionLabel({ bot: "@x", busy: true })).toBe("Запускаю…");
  });
});

describe("бейдж шапки", () => {
  it("работает в боте и совпадает с редактором", () => {
    expect(botBadge({ eng: engine(), published: 2, changes: 0 })).toMatchObject({
      kind: "live", text: "Работает в боте @funnel_studio_TEST_bot · версия 2", behind: false,
    });
  });
  it("есть правки, которых нет в боте: изменения в редакторе (и несохранённые) или версия новее", () => {
    expect(botBadge({ eng: engine(), published: 2, changes: 1 }).behind).toBe(true);
    expect(botBadge({ eng: engine(), published: 3, changes: 0 })).toMatchObject({ behind: true, text: "Работает в боте @funnel_studio_TEST_bot · версия 2" });
    // в бот отправили не из Студии (командная строка) – не знаем, совпадает ли
    expect(botBadge({ eng: engine({ in_bot: { engine_version: 5, studio_number: null, published_at: "", published_by: "" } }), published: 2, changes: 0 }))
      .toMatchObject({ behind: true, text: "Работает в боте @funnel_studio_TEST_bot · версия 5" });
  });
  it("ещё не запущена / нет связи / боты не подключены", () => {
    expect(botBadge({ eng: engine({ in_bot: null }), published: 1, changes: 0 }).text).toBe("Ещё не запущена ни в одном боте");
    expect(botBadge({ eng: engine({ tenant: null, in_bot: null }), published: null, changes: 0 }).text).toBe("Ещё не запущена ни в одном боте");
    expect(botBadge({ eng: engine({ error: "база не отвечает" }), published: 1, changes: 0 }).text).toBe("Нет связи с ботами");
    expect(botBadge({ eng: engine({ configured: false }), published: 1, changes: 0 }).kind).toBe("off");
  });
});
