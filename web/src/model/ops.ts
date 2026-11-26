// Операции редактора – чистые функции (граф, аргументы) → новый граф.
//
// Все правки холста и панели идут только через них: так работает undo/redo (история – это
// просто прошлые графы), и так их можно проверить тестом, включая «воссоздать воронку с нуля».
// Нетронутые шаги и стрелки сохраняют ссылочную идентичность (структурное разделение) – по ней
// хранилище понимает, какую карточку изменила операция.
//
// Инварианты модели проверяются здесь же; нарушение – OpError с русским текстом для человека.
import {
  defaultButton, defaultCall, defaultContact, defaultEmail, defaultMedia, defaultSms, newNote,
  newStep,
} from "./defaults";
import type {
  Branch, BranchRouter, Button, CallSettings, ClaimSettings, Conditions, ContactSettings, Edge, EdgeKind,
  EmailSettings, EntryVariant, Funnel, Lane, Media, Note, PinSettings, Platform, Position,
  RevisionAction, RevisionStatus, Schedule, SmsSettings, Step, StepType, StepVariant,
  SurveySettings, Uuid, WebinarRoom,
} from "./types";

export class OpError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "OpError";
  }
}

type G = Funnel;

// ── справочники ──────────────────────────────────────────────────────────────

export const STEP_TYPE_LABEL: Record<StepType, string> = {
  text: "💬 Текст",
  photo: "🖼 Сообщение с картинкой",
  video_note: "◉ Кружок",
  video: "🎬 Видео",
  animation: "🎞 GIF",
  rich: "📰 Статья",
  phone_request: "📞 Запрос телефона",
  email_request: "✉ Запрос почты",
  jump: "⤳ Переход (удалённый шаг)",
  call: "☎ Автозвонок",
  sms: "📱 SMS",
  air_pin: "📌 Закреп эфира",
};

const KEY_PREFIX: Record<StepType, string> = {
  text: "msg", photo: "msg", video_note: "circle", video: "video", animation: "gif",
  rich: "rich", phone_request: "phone", email_request: "email", jump: "jump", call: "call",
  sms: "sms", air_pin: "airpin",
};

export const EDGE_KIND_LABEL: Record<EdgeKind, string> = {
  done: "по времени",
  contact: "дал телефон",
  skip: "не дал (таймаут)",
  skip_if_phone_known: "телефон уже известен",
  email: "прислал почту",
  skip_if_email_known: "почта уже известна",
  callback: "нажал кнопку-действие",
  survey_next: "ответил – следующий вопрос",
  jump: "переход",
  also_fire: "параллельно",
  chain_to_funnel: "в другую воронку",
};

/** Виды стрелок, у которых есть задержка. */
export const DELAY_KINDS: ReadonlySet<EdgeKind> = new Set<EdgeKind>([
  "done", "contact", "skip", "skip_if_phone_known", "email", "skip_if_email_known",
  "also_fire", "chain_to_funnel",
]);

/** Задержка новой стрелки – как умолчание движка (skip – «таймаут не задан»). */
export function defaultDelay(kind: EdgeKind): number | null {
  if (!DELAY_KINDS.has(kind)) return null;
  return kind === "skip" ? null : 0;
}

export const KEY_RE = /^[A-Za-z0-9_.-]+$/;

/** Шаг пришёл из yaml (id – uuid5 от key): у такого могут быть люди и таймеры. */
export function isImportedId(id: Uuid): boolean {
  return id.length === 36 && id[14] === "5";
}

// ── помощники ────────────────────────────────────────────────────────────────

export function findStep(g: G, id: Uuid): Step {
  const s = g.steps.find((x) => x.id === id);
  if (!s) throw new OpError(`шага ${id} нет в воронке`);
  return s;
}

function findEdge(g: G, id: Uuid): Edge {
  const e = g.edges.find((x) => x.id === id);
  if (!e) throw new OpError(`стрелки ${id} нет в воронке`);
  return e;
}

function updateStep(g: G, id: Uuid, fn: (s: Step) => Step): G {
  let found = false;
  const steps = g.steps.map((s) => {
    if (s.id !== id) return s;
    found = true;
    return fn(s);
  });
  if (!found) throw new OpError(`шага ${id} нет в воронке`);
  return { ...g, steps };
}

function stepLabel(s: Step): string {
  return `«${s.key}»`;
}

export function uniqueKey(g: G, base: string, taken?: Set<string>): string {
  const keys = taken ?? new Set(g.steps.map((s) => s.key));
  if (!keys.has(base)) return base;
  for (let i = 2; ; i++) {
    const k = `${base}${i}`;
    if (!keys.has(k)) return k;
  }
}

function nextKey(g: G, type: StepType): string {
  const prefix = KEY_PREFIX[type];
  const keys = new Set(g.steps.map((s) => s.key));
  for (let i = 1; ; i++) {
    const k = `${prefix}_${String(i).padStart(2, "0")}`;
    if (!keys.has(k)) return k;
  }
}

function checkNewKey(g: G, key: string, except?: Uuid): void {
  if (!key) throw new OpError("техническое имя шага не может быть пустым");
  if (!KEY_RE.test(key)) {
    throw new OpError(`техническое имя «${key}»: только латиница, цифры, «_», «.» и «-»`);
  }
  const clash = g.steps.find((s) => s.key === key && s.id !== except);
  if (clash) throw new OpError(`техническое имя «${key}» уже занято другим шагом`);
}

function patchOf<T extends object>(base: T, patch: Partial<T>, what: string): T {
  for (const k of Object.keys(patch)) {
    if (!(k in base)) throw new OpError(`${what}: неизвестное поле «${k}»`);
  }
  return { ...base, ...patch };
}

// ── воронка ──────────────────────────────────────────────────────────────────

export interface FunnelMetaPatch {
  id?: string;
  title?: string;
  air_email_profile?: string;
  max_video_note_caption?: boolean;
  resend_entry_on_repeat?: boolean;
  webinar_room?: WebinarRoom | null;
}

export function setFunnelMeta(g: G, patch: FunnelMetaPatch): G {
  if (patch.id !== undefined && !patch.id.trim()) throw new OpError("id воронки не может быть пустым");
  return patchOf(g, patch as Partial<G>, "воронка");
}

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

export function setBranchRouter(g: G, router: BranchRouter | null): G {
  if (router) {
    const ids = new Set<string>();
    for (const b of router.branches) {
      if (!b.id) throw new OpError("у ветки должно быть имя");
      if (ids.has(b.id)) throw new OpError(`ветка «${b.id}» повторяется`);
      ids.add(b.id);
      findStep(g, b.entry_step);
      if (!HHMM.test(b.webinar_time)) throw new OpError(`ветка «${b.id}»: время эфира ЧЧ:ММ`);
      for (const w of b.windows) {
        if (!HHMM.test(w.from) || !HHMM.test(w.to)) throw new OpError(`ветка «${b.id}»: окно ЧЧ:ММ–ЧЧ:ММ`);
        if (w.day === "fixed" && !w.date) throw new OpError(`ветка «${b.id}»: у фиксированной даты нужна дата`);
      }
    }
  }
  return { ...g, branch_router: router };
}

export function setEntryVariants(g: G, variants: EntryVariant[]): G {
  for (const v of variants) findStep(g, v.entry_step);
  return { ...g, entry_variants: variants };
}

/** Сделать шаг первым в воронке (без branch_router движок начинает с первого шага). */
export function setStartStep(g: G, a: { stepId: Uuid }): G {
  const s = findStep(g, a.stepId);
  return { ...g, steps: [s, ...g.steps.filter((x) => x !== s)] };
}

// ── шаги ─────────────────────────────────────────────────────────────────────

export interface AddStepArgs {
  id: Uuid;
  type: StepType;
  key?: string;
  position?: Position;
  laneId?: Uuid | null;
  /** «Вопрос опроса» из палитры: текстовый шаг с qid. */
  survey?: boolean;
}

export function addStep(g: G, a: AddStepArgs): G {
  if (g.steps.some((s) => s.id === a.id)) throw new OpError(`шаг с id ${a.id} уже есть`);
  let key = a.key;
  if (key === undefined) key = a.survey ? nextSurveyKey(g) : nextKey(g, a.type);
  checkNewKey(g, key);
  let step = newStep(a.id, key, a.type);
  if (a.survey) {
    if (a.type !== "text") throw new OpError("вопрос опроса – это текстовый шаг");
    step = { ...step, survey: { ...step.survey, qid: nextQid(g), edit: true } };
  }
  let out: G = { ...g, steps: [...g.steps, step] };
  if (a.position) out = setPositions(out, { positions: { [a.id]: a.position } });
  if (a.laneId) out = assignLane(out, { stepIds: [a.id], laneId: a.laneId });
  return out;
}

function nextQid(g: G): string {
  const used = new Set(g.steps.map((s) => s.survey.qid).filter(Boolean));
  for (let i = 1; ; i++) if (!used.has(`q${i}`)) return `q${i}`;
}

function nextSurveyKey(g: G): string {
  const keys = new Set(g.steps.map((s) => s.key));
  for (let i = 1; ; i++) if (!keys.has(`q${i}`)) return `q${i}`;
}

export interface RemoveStepArgs {
  stepId: Uuid;
  /** Куда перенаправить людей, если шаг нельзя удалить насовсем. */
  redirectTo?: Uuid;
  /** id стрелки-перехода надгробия (выдаёт вызывающий). */
  edgeId?: Uuid;
}

/** Почему шаг нельзя просто удалить (пусто – можно). */
export function removeBlockers(g: G, stepId: Uuid): string[] {
  const out: string[] = [];
  const incoming = g.edges.filter((e) => e.to_step === stepId && e.from_step !== stepId);
  if (incoming.length) out.push(`в него ведут стрелки (${incoming.length})`);
  if (g.branch_router?.branches.some((b) => b.entry_step === stepId)) out.push("это вход ветки");
  if (g.entry_variants.some((v) => v.entry_step === stepId)) out.push("это вход для когорты");
  if (isImportedId(stepId)) out.push("шаг пришёл из yaml – у него могут быть люди и таймеры");
  return out;
}

/**
 * Удалить шаг. Если в него ведут стрелки или он пришёл из yaml – не удаляем, а превращаем
 * в «надгробие»: тип jump, одна стрелка «переход» на redirectTo. Ключ остаётся, поэтому
 * люди с уже поставленными таймерами на этот шаг не теряются (правила 42 и 78).
 */
export function removeStep(g: G, a: RemoveStepArgs): G {
  const step = findStep(g, a.stepId);
  const blockers = removeBlockers(g, a.stepId);
  if (!blockers.length) {
    const positions = { ...g.layout.positions };
    delete positions[a.stepId];
    const pos = g.layout.positions[a.stepId] ?? null;
    return {
      ...g,
      steps: g.steps.filter((s) => s.id !== a.stepId),
      edges: g.edges.filter((e) => e.from_step !== a.stepId && e.to_step !== a.stepId),
      layout: { ...g.layout, positions },
      lanes: g.lanes.map((l) => (l.step_ids.includes(a.stepId)
        ? { ...l, step_ids: l.step_ids.filter((x) => x !== a.stepId) } : l)),
      // заметка на карточке не пропадает – становится свободной на её месте
      notes: g.notes.map((n) => (n.step_id === a.stepId
        ? { ...n, step_id: null, position: n.position ?? (pos ? { x: pos.x + 330, y: pos.y } : { x: 0, y: 0 }) }
        : n)),
    };
  }
  if (!a.redirectTo) {
    throw new OpError(`Шаг ${stepLabel(step)} нельзя удалить насовсем: ${blockers.join("; ")}. ` +
      "Выберите, куда перенаправить людей – шаг станет переходом («надгробием»).");
  }
  if (a.redirectTo === a.stepId) throw new OpError("нельзя перенаправить шаг сам на себя");
  findStep(g, a.redirectTo);
  if (!a.edgeId) throw new OpError("нужен id стрелки-перехода");
  const tomb: Step = {
    ...newStep(step.id, step.key, "jump"),
    title: step.title || `надгробие: был ${STEP_TYPE_LABEL[step.type]}`,
  };
  const edges = g.edges.filter((e) => e.from_step !== step.id);
  edges.push({
    id: a.edgeId, from_step: step.id, kind: "jump", to_step: a.redirectTo, to_funnel: null,
    delay_seconds: null, typing_before: false,
  });
  return { ...g, steps: g.steps.map((s) => (s.id === step.id ? tomb : s)), edges };
}

/** Что пропадёт при смене типа – для предупреждения в панели. */
export function typeChangeLosses(g: G, stepId: Uuid, type: StepType): string[] {
  const s = findStep(g, stepId);
  const out: string[] = [];
  const next = changedType(g, s, type);
  const lostEdges = g.edges.filter((e) => e.from_step === s.id && !next.edges.some((x) => x.id === e.id));
  if (lostEdges.length) {
    out.push("стрелки: " + [...new Set(lostEdges.map((e) => EDGE_KIND_LABEL[e.kind]))].join(", "));
  }
  const m = s.media;
  const nm = next.step.media;
  if (m.image && !nm.image) out.push("картинка");
  if (m.video_note && !nm.video_note) out.push("кружок");
  if (m.video && !nm.video) out.push("видео");
  if (m.animation && !nm.animation) out.push("GIF");
  if (m.rich && !nm.rich) out.push("статья");
  if (JSON.stringify(s.contact) !== JSON.stringify(next.step.contact)) out.push("настройки запроса телефона");
  if (JSON.stringify(s.email) !== JSON.stringify(next.step.email)) out.push("настройки запроса почты");
  if (JSON.stringify(s.call) !== JSON.stringify(next.step.call)) out.push("настройки звонка");
  if (JSON.stringify(s.sms) !== JSON.stringify(next.step.sms)) out.push("текст SMS");
  if (s.content.tg.text && (type === "jump" || type === "call" || type === "sms")) out.push("текст сообщения");
  return out;
}

function changedType(g: G, s: Step, type: StepType): { step: Step; edges: Edge[] } {
  const media: Media = { ...s.media };
  if (type !== "video_note") { media.video_note = null; media.video_fallback = null; }
  if (type !== "video") media.video = null;
  if (type !== "animation") media.animation = null;
  if (type !== "rich") media.rich = null;
  if (["call", "sms", "jump", "video_note"].includes(type)) {
    media.image = null; media.images = null; media.image_lm = false;
  }
  let step: Step = { ...s, type, media };
  if (type !== "phone_request") step = { ...step, contact: defaultContact() };
  if (type !== "email_request") step = { ...step, email: defaultEmail() };
  if (type !== "call") step = { ...step, call: defaultCall() };
  if (type !== "sms") step = { ...step, sms: defaultSms() };
  if (type === "jump") {
    step = { ...newStep(s.id, s.key, "jump"), title: s.title };
  }
  const edges = g.edges.filter((e) => e.from_step !== s.id || kindAllowed(step, e.kind));
  return { step, edges };
}

export function setType(g: G, a: { stepId: Uuid; type: StepType }): G {
  const s = findStep(g, a.stepId);
  if (s.type === a.type) return g;
  const { step, edges } = changedType(g, s, a.type);
  return { ...g, steps: g.steps.map((x) => (x.id === s.id ? step : x)), edges };
}

export function setKey(g: G, a: { stepId: Uuid; key: string }): G {
  const s = findStep(g, a.stepId);
  if (s.key === a.key) return g;
  checkNewKey(g, a.key, a.stepId);
  return updateStep(g, a.stepId, (x) => ({ ...x, key: a.key }));
}

export interface MiscPatch {
  title?: string;
  role?: string | null;
  role_day?: number | null;
  diploma_template?: string;
  advanced?: Record<string, unknown>;
}

export function setMisc(g: G, a: { stepId: Uuid; patch: MiscPatch }): G {
  return updateStep(g, a.stepId, (s) => patchOf(s, a.patch as Partial<Step>, "шаг"));
}

// ── контент по платформам ────────────────────────────────────────────────────

export type PlatformMode = "set" | "inherit" | "hide";

export function setText(g: G, a: { stepId: Uuid; platform: Platform; mode?: PlatformMode; value?: string }): G {
  const mode = a.mode ?? "set";
  return updateStep(g, a.stepId, (s) => {
    if (a.platform === "tg") {
      if (mode !== "set") throw new OpError("в Telegram текст задаётся всегда – «как в TG» бывает только у MAX");
      return { ...s, content: { ...s.content, tg: { ...s.content.tg, text: a.value ?? "" } } };
    }
    const text = mode === "inherit" ? null : mode === "hide" ? "" : (a.value ?? "");
    return { ...s, content: { ...s.content, max: { ...s.content.max, text } } };
  });
}

function checkButtons(bs: Button[]): Button[] {
  return bs.map((b) => ({ ...defaultButton(), ...b }));
}

export function setButtons(g: G, a: { stepId: Uuid; platform: Platform; mode?: PlatformMode; buttons?: Button[] }): G {
  const mode = a.mode ?? "set";
  return updateStep(g, a.stepId, (s) => {
    if (a.platform === "tg") {
      if (mode !== "set") throw new OpError("в Telegram кнопки задаются всегда");
      return { ...s, content: { ...s.content, tg: { ...s.content.tg, buttons: checkButtons(a.buttons ?? []) } } };
    }
    const buttons = mode === "inherit" ? null : mode === "hide" ? [] : checkButtons(a.buttons ?? []);
    return { ...s, content: { ...s.content, max: { ...s.content.max, buttons } } };
  });
}

/**
 * Кнопка-действие (claim): подпись по платформам. Цель – стрелка «нажал» (callback).
 * tg: mode "set" – задать, "hide" – убрать кнопку (вместе со стрелкой «нажал»).
 */
export function setClaim(g: G, a: { stepId: Uuid; platform: Platform; mode?: PlatformMode; value?: string }): G {
  const mode = a.mode ?? "set";
  let out = updateStep(g, a.stepId, (s) => {
    if (a.platform === "tg") {
      if (mode === "inherit") throw new OpError("«как в TG» бывает только у MAX");
      const v = mode === "hide" ? null : (a.value ?? "");
      return { ...s, content: { ...s.content, tg: { ...s.content.tg, claim_button_text: v } } };
    }
    const v = mode === "inherit" ? null : mode === "hide" ? "" : (a.value ?? "");
    return { ...s, content: { ...s.content, max: { ...s.content.max, claim_button_text: v } } };
  });
  if (a.platform === "tg" && mode === "hide") {
    out = { ...out, edges: out.edges.filter((e) => !(e.from_step === a.stepId && e.kind === "callback")) };
  }
  return out;
}

export function setClaimSettings(g: G, a: { stepId: Uuid; patch: Partial<ClaimSettings> }): G {
  return updateStep(g, a.stepId, (s) => ({ ...s, claim: patchOf(s.claim, a.patch, "кнопка-действие") }));
}

export function setVariants(g: G, a: { stepId: Uuid; variants: StepVariant[] }): G {
  return updateStep(g, a.stepId, (s) => ({ ...s, content: { ...s.content, variants: a.variants } }));
}

// ── медиа, время, условия, настройки типов ──────────────────────────────────

export function setMedia(g: G, a: { stepId: Uuid; patch: Partial<Media> }): G {
  return updateStep(g, a.stepId, (s) => ({ ...s, media: patchOf(s.media ?? defaultMedia(), a.patch, "медиа") }));
}

export function checkSchedule(s: Schedule): void {
  if (s.absolute_time_msk !== null && !HHMM.test(s.absolute_time_msk)) {
    throw new OpError(`время «${s.absolute_time_msk}»: нужно ЧЧ:ММ, например 09:30`);
  }
  if (s.day_offset_by_branch && !s.absolute_time_msk) {
    throw new OpError("свой день для каждой ветки бывает только у отправки «в точное время»");
  }
  if (s.webinar_offset_by_branch && s.webinar_offset_minutes === null) {
    throw new OpError("своё смещение для каждой ветки требует основного смещения от эфира");
  }
  for (const v of [s.day_offset, s.webinar_offset_minutes, s.after_start_seconds, s.min_delay_seconds]) {
    if (v !== null && !Number.isInteger(v)) throw new OpError("время: нужны целые числа");
  }
  if (s.min_delay_seconds < 0) throw new OpError("«не раньше чем через» не может быть меньше нуля");
}

const WINDOW = /^(\d{2}):(\d{2})-(\d{2}):(\d{2})$/;

/** Окна часов регистрации – те же правила, что у движка (reg_window.parse_windows).
 *  → текст ошибки или null. */
export function windowProblem(w: string): string | null {
  const m = WINDOW.exec(w.trim());
  if (!m) return `окно «${w}»: нужно ЧЧ:ММ-ЧЧ:ММ`;
  const [h1, m1, h2, m2] = m.slice(1).map(Number);
  if (h1 > 23 || m1 > 59 || m2 > 59 || h2 > 24 || (h2 === 24 && m2)) return `окно «${w}»: такого времени нет`;
  if (h1 * 60 + m1 >= h2 * 60 + m2) return `окно «${w}»: начало должно быть раньше конца (через полночь – двумя окнами)`;
  return null;
}

export function setSchedule(g: G, a: { stepId: Uuid; patch: Partial<Schedule> }): G {
  return updateStep(g, a.stepId, (s) => {
    const schedule = patchOf(s.schedule, a.patch, "время");
    checkSchedule(schedule);
    return { ...s, schedule };
  });
}

export function setConditions(g: G, a: { stepId: Uuid; patch: Partial<Conditions> }): G {
  return updateStep(g, a.stepId, (s) => {
    const c = patchOf(s.conditions, a.patch, "условия");
    if (c.paid && c.not_paid) throw new OpError("условия «оплатил ВИП» и «не оплатил ВИП» вместе – шаг не уйдёт никому");
    if (c.course_paid && c.not_course_paid) throw new OpError("условия «купил курс» и «не купил курс» вместе – шаг не уйдёт никому");
    if (!Number.isInteger(c.gate_window_hours) || c.gate_window_hours < 0) throw new OpError("окно проверки – целое число часов");
    for (const w of c.registered_windows ?? []) {
      const bad = windowProblem(w);
      if (bad) throw new OpError(`часы регистрации: ${bad}`);
    }
    return { ...s, conditions: c };
  });
}

export function setContact(g: G, a: { stepId: Uuid; patch: Partial<ContactSettings> }): G {
  return updateStep(g, a.stepId, (s) => ({ ...s, contact: patchOf(s.contact, a.patch, "запрос телефона") }));
}

export function setEmail(g: G, a: { stepId: Uuid; patch: Partial<EmailSettings> }): G {
  return updateStep(g, a.stepId, (s) => ({ ...s, email: patchOf(s.email, a.patch, "запрос почты") }));
}

export function setSurvey(g: G, a: { stepId: Uuid; patch: Partial<SurveySettings> }): G {
  const cur = findStep(g, a.stepId);
  const next = patchOf(cur.survey, a.patch, "опрос");
  if (!next.qid && g.edges.some((e) => e.from_step === a.stepId && e.kind === "survey_next")) {
    throw new OpError("шаг перестаёт быть вопросом, а из него идёт стрелка «следующий вопрос» – сначала удалите её");
  }
  return updateStep(g, a.stepId, (s) => ({ ...s, survey: next }));
}

export function setCall(g: G, a: { stepId: Uuid; patch: Partial<CallSettings> }): G {
  return updateStep(g, a.stepId, (s) => {
    const call = patchOf(s.call, a.patch, "звонок");
    if (call.audio_id !== null && !Number.isInteger(call.audio_id)) throw new OpError("id записи звонка – целое число");
    return { ...s, call };
  });
}

export function setSms(g: G, a: { stepId: Uuid; patch: Partial<SmsSettings> }): G {
  return updateStep(g, a.stepId, (s) => ({ ...s, sms: patchOf(s.sms, a.patch, "SMS") }));
}

export function setPin(g: G, a: { stepId: Uuid; patch: Partial<PinSettings> }): G {
  return updateStep(g, a.stepId, (s) => ({ ...s, pin: patchOf(s.pin, a.patch, "закреп") }));
}

// ── стрелки ──────────────────────────────────────────────────────────────────

/** Может ли из шага такого вида идти стрелка такого вида (без учёта уже имеющихся). */
export function kindAllowed(s: Step, kind: EdgeKind): boolean {
  if (s.type === "jump") return kind === "jump";
  switch (kind) {
    case "contact":
    case "skip_if_phone_known":
      return s.type === "phone_request";
    case "skip":
      return s.type === "phone_request" || s.type === "email_request";
    case "email":
    case "skip_if_email_known":
      return s.type === "email_request";
    case "callback":
      return s.content.tg.claim_button_text !== null;
    case "survey_next":
      return !!s.survey.qid;
    case "jump":
      return false;
    default:
      return true;
  }
}

function kindWhy(s: Step, kind: EdgeKind): string {
  if (s.type === "jump") return "из перехода-надгробия идёт только одна стрелка «переход»";
  switch (kind) {
    case "contact":
    case "skip_if_phone_known":
      return "эта стрелка бывает только у запроса телефона";
    case "skip":
      return "«не дал» бывает только у запроса телефона или почты";
    case "email":
    case "skip_if_email_known":
      return "эта стрелка бывает только у запроса почты";
    case "callback":
      return "у шага нет кнопки-действия – сначала добавьте её";
    case "survey_next":
      return "шаг не вопрос опроса";
    case "jump":
      return "«переход» бывает только у надгробия";
    default:
      return "недопустимо";
  }
}

export interface AddEdgeArgs {
  id: Uuid;
  from: Uuid;
  kind: EdgeKind;
  to?: Uuid | null;
  toFunnel?: string | null;
  delay?: number | null;
  typing?: boolean;
}

function checkDelay(kind: EdgeKind, d: number | null): void {
  if (!DELAY_KINDS.has(kind)) {
    if (d !== null) throw new OpError(`у стрелки «${EDGE_KIND_LABEL[kind]}» нет задержки`);
    return;
  }
  if (d === null) {
    if (kind !== "skip") throw new OpError("задержка обязательна (0 – сразу)");
    return;
  }
  if (!Number.isInteger(d) || d < 0) throw new OpError("задержка – целое число секунд, не меньше 0");
}

function checkEdge(g: G, e: Edge, ignore?: Uuid): void {
  const from = findStep(g, e.from_step);
  if (!kindAllowed(from, e.kind)) throw new OpError(`${stepLabel(from)}: ${kindWhy(from, e.kind)}`);
  const siblings = g.edges.filter((x) => x.from_step === e.from_step && x.id !== ignore);
  if (e.kind === "chain_to_funnel") {
    if (!e.to_funnel?.trim()) throw new OpError("укажите воронку, в которую переходить");
    if (e.to_step !== null) throw new OpError("у перехода в воронку нет шага-цели");
  } else {
    if (!e.to_step) throw new OpError("у стрелки нет цели");
    if (e.to_funnel !== null) throw new OpError("воронка-цель бывает только у перехода в воронку");
    if (e.typing_before) throw new OpError("«печатает…» бывает только у перехода в воронку");
    if (!g.steps.some((s) => s.id === e.to_step) && !g.unresolved.some((u) => u.id === e.to_step)) {
      throw new OpError("стрелка ведёт в шаг, которого нет");
    }
    if (e.to_step === e.from_step) throw new OpError("стрелка не может вести в тот же шаг");
  }
  if (e.kind === "also_fire") {
    if (siblings.some((x) => x.kind === "also_fire" && x.to_step === e.to_step)) {
      throw new OpError(`${stepLabel(from)}: параллельная стрелка в этот шаг уже есть`);
    }
  } else if (siblings.some((x) => x.kind === e.kind)) {
    const what = e.kind === "done" ? "у шага уже есть стрелка «по времени» – добавьте параллельную (Alt)"
      : e.kind === "callback" ? "у кнопки-действия уже есть стрелка «нажал»"
      : `у шага уже есть стрелка «${EDGE_KIND_LABEL[e.kind]}»`;
    throw new OpError(`${stepLabel(from)}: ${what}`);
  }
  checkDelay(e.kind, e.delay_seconds);
}

export function addEdge(g: G, a: AddEdgeArgs): G {
  if (g.edges.some((e) => e.id === a.id)) throw new OpError(`стрелка с id ${a.id} уже есть`);
  let delay = a.delay === undefined ? defaultDelay(a.kind) : a.delay;
  const siblings = g.edges.filter((x) => x.from_step === a.from);
  // у движка одна задержка на on_done и chain_to_funnel – держим их равными
  if (a.kind === "chain_to_funnel") {
    const done = siblings.find((x) => x.kind === "done");
    if (done) delay = done.delay_seconds;
  }
  const e: Edge = {
    id: a.id, from_step: a.from, kind: a.kind,
    to_step: a.kind === "chain_to_funnel" ? null : (a.to ?? null),
    to_funnel: a.kind === "chain_to_funnel" ? (a.toFunnel ?? null) : null,
    delay_seconds: delay, typing_before: a.typing ?? false,
  };
  checkEdge(g, e);
  let edges = [...g.edges, e];
  if (a.kind === "done") edges = syncChainDelay(edges, a.from, delay);
  return { ...g, edges };
}

function syncChainDelay(edges: Edge[], from: Uuid, delay: number | null): Edge[] {
  return edges.map((x) => (x.from_step === from && (x.kind === "chain_to_funnel" || x.kind === "done")
    && x.delay_seconds !== delay ? { ...x, delay_seconds: delay } : x));
}

export function setEdgeDelay(g: G, a: { edgeId: Uuid; delay: number | null }): G {
  const e = findEdge(g, a.edgeId);
  checkDelay(e.kind, a.delay);
  if (e.kind === "done" || e.kind === "chain_to_funnel") {
    return { ...g, edges: syncChainDelay(g.edges, e.from_step, a.delay) };
  }
  return { ...g, edges: g.edges.map((x) => (x.id === e.id ? { ...x, delay_seconds: a.delay } : x)) };
}

export function setEdgeKind(g: G, a: { edgeId: Uuid; kind: EdgeKind }): G {
  const e = findEdge(g, a.edgeId);
  if (e.kind === a.kind) return g;
  if (a.kind === "chain_to_funnel" || e.kind === "chain_to_funnel") {
    throw new OpError("переход в другую воронку задаётся в панели шага, а не сменой вида стрелки");
  }
  let delay = e.delay_seconds;
  if (!DELAY_KINDS.has(a.kind)) delay = null;
  else if (delay === null && a.kind !== "skip") delay = defaultDelay(a.kind);
  const next: Edge = { ...e, kind: a.kind, delay_seconds: delay };
  checkEdge(g, next, e.id);
  return { ...g, edges: g.edges.map((x) => (x.id === e.id ? next : x)) };
}

export function retargetEdge(g: G, a: { edgeId: Uuid; to: Uuid }): G {
  const e = findEdge(g, a.edgeId);
  if (e.kind === "chain_to_funnel") throw new OpError("у перехода в воронку нет шага-цели");
  const next = { ...e, to_step: a.to };
  checkEdge(g, next, e.id);
  return { ...g, edges: g.edges.map((x) => (x.id === e.id ? next : x)) };
}

export function setEdgeFunnel(g: G, a: { edgeId: Uuid; toFunnel: string; typing?: boolean }): G {
  const e = findEdge(g, a.edgeId);
  if (e.kind !== "chain_to_funnel") throw new OpError("воронка-цель бывает только у перехода в воронку");
  if (!a.toFunnel.trim()) throw new OpError("укажите воронку");
  return {
    ...g,
    edges: g.edges.map((x) => (x.id === e.id
      ? { ...x, to_funnel: a.toFunnel, typing_before: a.typing ?? x.typing_before } : x)),
  };
}

export function removeEdge(g: G, a: { edgeId: Uuid }): G {
  findEdge(g, a.edgeId);
  return { ...g, edges: g.edges.filter((e) => e.id !== a.edgeId) };
}

// ── раскладка и полосы ──────────────────────────────────────────────────────

export function setPositions(g: G, a: { positions: Record<Uuid, Position> }): G {
  return { ...g, layout: { ...g.layout, positions: { ...g.layout.positions, ...a.positions } } };
}

export interface AddLaneArgs {
  id: Uuid;
  title: string;
  kind?: string;
  day?: number | null;
  /** Верхняя граница полосы на холсте (хранится в layout.positions[id]). */
  y?: number;
}

export function addLane(g: G, a: AddLaneArgs): G {
  if (!a.title.trim()) throw new OpError("у полосы должно быть название");
  if (g.lanes.some((l) => l.id === a.id)) throw new OpError("полоса с таким id уже есть");
  const lane: Lane = {
    id: a.id, title: a.title, kind: a.kind ?? "", day: a.day ?? null,
    order: g.lanes.length ? Math.max(...g.lanes.map((l) => l.order)) + 1 : 0, step_ids: [],
  };
  let out: G = { ...g, lanes: [...g.lanes, lane] };
  if (a.y !== undefined) out = setPositions(out, { positions: { [a.id]: { x: 0, y: a.y } } });
  return out;
}

export function updateLane(g: G, a: { laneId: Uuid; patch: { title?: string; kind?: string; day?: number | null } }): G {
  if (!g.lanes.some((l) => l.id === a.laneId)) throw new OpError("полосы нет");
  if (a.patch.title !== undefined && !a.patch.title.trim()) throw new OpError("у полосы должно быть название");
  return { ...g, lanes: g.lanes.map((l) => (l.id === a.laneId ? { ...l, ...a.patch } : l)) };
}

export function removeLane(g: G, a: { laneId: Uuid }): G {
  if (!g.lanes.some((l) => l.id === a.laneId)) throw new OpError("полосы нет");
  const positions = { ...g.layout.positions };
  delete positions[a.laneId];
  return {
    ...g,
    lanes: g.lanes.filter((l) => l.id !== a.laneId),
    layout: { ...g.layout, positions },
    notes: g.notes.map((n) => (n.lane_id === a.laneId ? { ...n, lane_id: null } : n)),
  };
}

/** Положить карточки в полосу (null – вынуть из всех полос). */
export function assignLane(g: G, a: { stepIds: Uuid[]; laneId: Uuid | null }): G {
  if (a.laneId && !g.lanes.some((l) => l.id === a.laneId)) throw new OpError("полосы нет");
  for (const id of a.stepIds) findStep(g, id);
  const ids = new Set(a.stepIds);
  let changed = false;
  const lanes = g.lanes.map((l) => {
    if (l.id === a.laneId) {
      const add = a.stepIds.filter((x) => !l.step_ids.includes(x));
      if (!add.length) return l;
      changed = true;
      return { ...l, step_ids: [...l.step_ids, ...add] };
    }
    if (l.step_ids.some((x) => ids.has(x))) {
      changed = true;
      return { ...l, step_ids: l.step_ids.filter((x) => !ids.has(x)) };
    }
    return l;
  });
  return changed ? { ...g, lanes } : g;
}

export function setLanes(g: G, lanes: Lane[]): G {
  return { ...g, lanes };
}

// ── заметки и правки (в yaml не уходят никогда) ─────────────────────────────

export interface AddNoteArgs {
  id: Uuid;
  text: string;
  color?: string;
  stepId?: Uuid | null;
  laneId?: Uuid | null;
  position?: Position | null;
  author?: string;
  createdAt?: string;
}

function checkAnchor(g: G, a: { stepId?: Uuid | null; laneId?: Uuid | null; position?: Position | null }): void {
  if (a.stepId) findStep(g, a.stepId);
  if (a.laneId && !g.lanes.some((l) => l.id === a.laneId)) throw new OpError("полосы нет");
  if (!a.stepId && !a.laneId && !a.position) throw new OpError("заметку нужно положить на холст или прикрепить к карточке");
}

export function addNote(g: G, a: AddNoteArgs): G {
  checkAnchor(g, a);
  const n: Note = {
    ...newNote(a.id, a.text), color: a.color ?? "#fff6b6", step_id: a.stepId ?? null,
    lane_id: a.laneId ?? null, position: a.position ?? null, author: a.author ?? "",
    created_at: a.createdAt ?? "",
  };
  return { ...g, notes: [...g.notes, n] };
}

export function updateNote(g: G, a: { noteId: Uuid; patch: Partial<Pick<Note, "text" | "color" | "position" | "step_id" | "lane_id" | "action" | "author">> }): G {
  const n = g.notes.find((x) => x.id === a.noteId);
  if (!n) throw new OpError("заметки нет");
  const next = { ...n, ...a.patch };
  checkAnchor(g, { stepId: next.step_id, laneId: next.lane_id, position: next.position });
  return { ...g, notes: g.notes.map((x) => (x.id === a.noteId ? next : x)) };
}

export function removeNote(g: G, a: { noteId: Uuid }): G {
  if (!g.notes.some((x) => x.id === a.noteId)) throw new OpError("заметки нет");
  return { ...g, notes: g.notes.filter((x) => x.id !== a.noteId) };
}

export const REVISION_ACTION_LABEL: Record<RevisionAction, string> = {
  replace_text: "заменить текст",
  replace_media: "заменить картинку",
  replace_time: "поменять время",
  add: "добавить",
  remove: "убрать",
  other: "другое",
};

export const REVISION_STATUS_LABEL: Record<RevisionStatus, string> = {
  open: "открыта",
  done: "сделана",
  accepted: "принята",
  rejected: "отклонена",
};

export function addRevision(g: G, a: AddNoteArgs & { action: RevisionAction }): G {
  checkAnchor(g, a);
  const number = Math.max(0, ...g.notes.map((n) => n.number ?? 0)) + 1;
  const n: Note = {
    ...newNote(a.id, a.text), kind: "revision", color: a.color ?? "#ffd9d6",
    step_id: a.stepId ?? null, lane_id: a.laneId ?? null, position: a.position ?? null,
    author: a.author ?? "", created_at: a.createdAt ?? "", number, action: a.action, status: "open",
  };
  return { ...g, notes: [...g.notes, n] };
}

const STATUS_NEXT: Record<RevisionStatus, RevisionStatus[]> = {
  open: ["done", "rejected"],
  done: ["accepted", "rejected", "open"],
  accepted: ["open"],
  rejected: ["open"],
};

export function setRevisionStatus(g: G, a: { noteId: Uuid; status: RevisionStatus }): G {
  const n = g.notes.find((x) => x.id === a.noteId);
  if (!n || n.kind !== "revision") throw new OpError("правки нет");
  const cur = n.status ?? "open";
  if (cur === a.status) return g;
  if (!STATUS_NEXT[cur].includes(a.status)) {
    throw new OpError(`правку «${REVISION_STATUS_LABEL[cur]}» нельзя сразу сделать «${REVISION_STATUS_LABEL[a.status]}»`);
  }
  return { ...g, notes: g.notes.map((x) => (x.id === a.noteId ? { ...x, status: a.status } : x)) };
}

// ── копирование ──────────────────────────────────────────────────────────────

export interface DuplicateArgs {
  stepIds: Uuid[];
  /** Новый id для каждого копируемого объекта (шаг или стрелка) по старому id. */
  newId: (oldId: Uuid) => Uuid;
  offset?: Position;
}

/** Копия карточек: новые id, новые key с суффиксом _copy, стрелки между копиями – тоже. */
export function duplicateSteps(g: G, a: DuplicateArgs): G {
  const ids = new Set(a.stepIds);
  const src = g.steps.filter((s) => ids.has(s.id));
  if (!src.length) return g;
  const map = new Map(src.map((s) => [s.id, a.newId(s.id)]));
  const taken = new Set(g.steps.map((s) => s.key));
  const copies = src.map((s) => {
    const key = uniqueKey(g, `${s.key}_copy`, taken);
    taken.add(key);
    return { ...structuredClone(s), id: map.get(s.id)!, key, miro: null };
  });
  const edges = g.edges
    .filter((e) => ids.has(e.from_step) && (e.to_step === null || ids.has(e.to_step)))
    .map((e) => ({ ...e, id: a.newId(e.id), from_step: map.get(e.from_step)!, to_step: e.to_step ? map.get(e.to_step)! : null }));
  const off = a.offset ?? { x: 40, y: 40 };
  const positions: Record<Uuid, Position> = {};
  for (const s of src) {
    const p = g.layout.positions[s.id];
    if (p) positions[map.get(s.id)!] = { x: p.x + off.x, y: p.y + off.y };
  }
  let out: G = { ...g, steps: [...g.steps, ...copies], edges: [...g.edges, ...edges] };
  out = setPositions(out, { positions });
  for (const l of g.lanes) {
    const inLane = src.filter((s) => l.step_ids.includes(s.id)).map((s) => map.get(s.id)!);
    if (inLane.length) out = assignLane(out, { stepIds: inLane, laneId: l.id });
  }
  return out;
}

// ── массовые исправления ────────────────────────────────────────────────────

const LONG_DASH = /—/g;

function fixStr<T extends string | null>(v: T): T {
  return (typeof v === "string" ? v.replace(LONG_DASH, "–") : v) as T;
}

function fixButtons(bs: Button[] | null): Button[] | null {
  return bs === null ? null : bs.map((b) => (b.text.includes("—") ? { ...b, text: fixStr(b.text) } : b));
}

/** Все длинные тире в текстах шагов → короткое (правило 4). */
export function fixDashes(g: G, a: { stepIds?: Uuid[] } = {}): G {
  const only = a.stepIds ? new Set(a.stepIds) : null;
  return {
    ...g,
    steps: g.steps.map((s) => {
      if (only && !only.has(s.id)) return s;
      if (!JSON.stringify(s).includes("—")) return s;
      return {
        ...s,
        content: {
          tg: { text: fixStr(s.content.tg.text), buttons: fixButtons(s.content.tg.buttons)!, claim_button_text: fixStr(s.content.tg.claim_button_text) },
          max: { text: fixStr(s.content.max.text), buttons: fixButtons(s.content.max.buttons), claim_button_text: fixStr(s.content.max.claim_button_text) },
          variants: s.content.variants.map((v) => ({
            ...v, text: fixStr(v.text), text_max: fixStr(v.text_max), buttons: fixButtons(v.buttons),
            claim_button_text: fixStr(v.claim_button_text),
          })),
        },
        contact: {
          ...s.contact, contact_button: fixStr(s.contact.contact_button), text_if_known: fixStr(s.contact.text_if_known),
          manual_phone_max_text: fixStr(s.contact.manual_phone_max_text), manual_phone_retry_text: fixStr(s.contact.manual_phone_retry_text),
        },
        email: { ...s.email, text_if_known: fixStr(s.email.text_if_known), retry_text: fixStr(s.email.retry_text) },
        survey: { ...s.survey, done_button: fixStr(s.survey.done_button) },
        call: { ...s.call, text: fixStr(s.call.text) },
        sms: { text: fixStr(s.sms.text) },
        claim: { ...s.claim, ack_text: fixStr(s.claim.ack_text) },
      };
    }),
  };
}

// ── ветки по времени регистрации (branch_router) ────────────────────────────
//
// Имя ветки (id) – ключ в day_offset_by_branch / webinar_offset_by_branch шагов, поэтому
// переименование и удаление ветки правят и эти словари. Окна проверяются мягко: пересечения
// и дыры в сутках – не ошибка операции (их видно в проверке, пока человек правит окна по
// одному), а неверный формат времени и несуществующий первый шаг – ошибка.

function mapBranchKeys(g: G, fn: (m: Record<string, number>) => Record<string, number> | null): G {
  let changed = false;
  const steps = g.steps.map((s) => {
    const sc = s.schedule;
    if (!sc.day_offset_by_branch && !sc.webinar_offset_by_branch) return s;
    const day = sc.day_offset_by_branch ? fn(sc.day_offset_by_branch) : null;
    const web = sc.webinar_offset_by_branch ? fn(sc.webinar_offset_by_branch) : null;
    // нетронутый шаг остаётся тем же объектом (иначе правки на нём «сделались» бы сами)
    if (day === sc.day_offset_by_branch && web === sc.webinar_offset_by_branch) return s;
    changed = true;
    return { ...s, schedule: { ...sc, day_offset_by_branch: day, webinar_offset_by_branch: web } };
  });
  return changed ? { ...g, steps } : g;
}

export function addBranch(g: G, a: { branch: Branch }): G {
  const router: BranchRouter = g.branch_router ?? { type: "time_of_day_msk", branches: [] };
  return setBranchRouter(g, { ...router, branches: [...router.branches, a.branch] });
}

export function updateBranch(g: G, a: { branchId: string; patch: Partial<Branch> }): G {
  const r = g.branch_router;
  const cur = r?.branches.find((b) => b.id === a.branchId);
  if (!r || !cur) throw new OpError(`ветки «${a.branchId}» нет`);
  const next = patchOf(cur, a.patch, "ветка");
  if (!Number.isInteger(next.repeat_offset_minutes) || next.repeat_offset_minutes < 0) {
    throw new OpError("сдвиг повтора – целое число минут, не меньше нуля");
  }
  let out = setBranchRouter(g, { ...r, branches: r.branches.map((b) => (b.id === a.branchId ? next : b)) });
  if (next.id !== cur.id) {
    out = mapBranchKeys(out, (m) => {
      if (!(cur.id in m)) return m;
      const copy: Record<string, number> = {};
      for (const [k, v] of Object.entries(m)) copy[k === cur.id ? next.id : k] = v;
      return copy;
    });
  }
  return out;
}

/** Удалить ветку. Последняя ветка – ветки выключаются совсем (у шагов пропадает «своё для ветки»). */
export function removeBranch(g: G, a: { branchId: string }): G {
  const r = g.branch_router;
  if (!r?.branches.some((b) => b.id === a.branchId)) throw new OpError(`ветки «${a.branchId}» нет`);
  const rest = r.branches.filter((b) => b.id !== a.branchId);
  const out = setBranchRouter(g, rest.length ? { ...r, branches: rest } : null);
  return mapBranchKeys(out, (m) => {
    if (!rest.length) return null;
    if (!(a.branchId in m)) return m;
    const copy = { ...m };
    delete copy[a.branchId];
    return Object.keys(copy).length ? copy : null;
  });
}

/** Поменять ветки местами: при пересечении окон побеждает та, что выше. */
export function moveBranch(g: G, a: { branchId: string; delta: number }): G {
  const r = g.branch_router;
  const i = r?.branches.findIndex((b) => b.id === a.branchId) ?? -1;
  if (!r || i < 0) throw new OpError(`ветки «${a.branchId}» нет`);
  const j = i + a.delta;
  if (j < 0 || j >= r.branches.length) return g;
  const branches = [...r.branches];
  [branches[i], branches[j]] = [branches[j], branches[i]];
  return { ...g, branch_router: { ...r, branches } };
}

// ── исправления из панели проверки ──────────────────────────────────────────

function mapStr<T extends string | null>(v: T, fn: (s: string) => string): T {
  return (typeof v === "string" ? fn(v) : v) as T;
}

function mapBtns(bs: Button[] | null, fn: (s: string) => string): Button[] | null {
  return bs === null ? null : bs.map((b) => {
    const text = fn(b.text);
    const url = mapStr(b.url, fn);
    return text === b.text && url === b.url ? b : { ...b, text, url };
  });
}

/** Все тексты шага (сообщение, кнопки, их ссылки, варианты, тексты запросов, SMS, звонок) через fn. */
export function mapStepTexts(s: Step, fn: (t: string) => string): Step {
  const next: Step = {
    ...s,
    content: {
      tg: { text: fn(s.content.tg.text), buttons: mapBtns(s.content.tg.buttons, fn)!, claim_button_text: mapStr(s.content.tg.claim_button_text, fn) },
      max: { text: mapStr(s.content.max.text, fn), buttons: mapBtns(s.content.max.buttons, fn), claim_button_text: mapStr(s.content.max.claim_button_text, fn) },
      variants: s.content.variants.map((v) => ({
        ...v, text: mapStr(v.text, fn), text_max: mapStr(v.text_max, fn), buttons: mapBtns(v.buttons, fn),
        claim_button_text: mapStr(v.claim_button_text, fn),
      })),
    },
    contact: {
      ...s.contact, text_if_known: fn(s.contact.text_if_known), manual_phone_max_text: fn(s.contact.manual_phone_max_text),
      manual_phone_retry_text: fn(s.contact.manual_phone_retry_text),
    },
    email: { ...s.email, text_if_known: fn(s.email.text_if_known), retry_text: fn(s.email.retry_text) },
    call: { ...s.call, text: mapStr(s.call.text, fn) },
    sms: { text: mapStr(s.sms.text, fn) },
  };
  return JSON.stringify(next) === JSON.stringify(s) ? s : next;
}

/** Заменить плейсхолдер во всех текстах шага (правило 1: «неизвестный → выбрать из списка»). */
export function replaceToken(g: G, a: { stepId: Uuid; from: string; to: string }): G {
  if (!a.from) throw new OpError("что заменить – не задано");
  return updateStep(g, a.stepId, (s) => mapStepTexts(s, (t) => t.split(a.from).join(a.to)));
}

/** Цель одной кнопки (правила 45 и 30: «кнопка без цели», «ВИП-кнопка ведёт на подарок»). */
export function setButtonUrl(g: G, a: { stepId: Uuid; platform: Platform; index: number; url: string | null; callback?: string | null }): G {
  return updateStep(g, a.stepId, (s) => {
    const list = a.platform === "tg" ? s.content.tg.buttons : s.content.max.buttons;
    if (!list || !list[a.index]) throw new OpError(`кнопки №${a.index + 1} нет`);
    const buttons = list.map((b, i) => (i === a.index ? { ...b, url: a.url, callback_data: a.callback ?? null } : b));
    return a.platform === "tg"
      ? { ...s, content: { ...s.content, tg: { ...s.content.tg, buttons } } }
      : { ...s, content: { ...s.content, max: { ...s.content.max, buttons } } };
  });
}

// ── импорт из Miro ───────────────────────────────────────────────────────────

/** Ключ сомнения импорта – как в studio/miro/merge.py doubt_key. */
export function miroDoubtKey(d: { code: string; text: string }): string {
  return `${d.code}:${d.text}`;
}

/** «Проверено»: сомнение импорта из Miro убирается с шага (stepId) или с воронки (stepId = null)
 *  и запоминается – повторный импорт той же доски его не вернёт. */
export function dismissMiroDoubt(g: G, a: { stepId: Uuid | null; key: string }): G {
  if (a.stepId === null) {
    if (!g.miro) throw new OpError("воронка не из Miro");
    const m = g.miro;
    return { ...g, miro: { ...m, doubts: m.doubts.filter((d) => miroDoubtKey(d) !== a.key), dismissed: [...new Set([...m.dismissed, a.key])] } };
  }
  return updateStep(g, a.stepId, (s) => {
    if (!s.miro) throw new OpError("шаг не из Miro");
    const m = s.miro;
    return { ...s, miro: { ...m, doubts: m.doubts.filter((d) => miroDoubtKey(d) !== a.key), dismissed: [...new Set([...m.dismissed, a.key])] } };
  });
}

/** Ссылка на элемент доски Miro (или на фрейм, если элемента нет). */
export function miroItemUrl(g: Funnel, itemId: string | null | undefined): string | null {
  if (!g.miro) return null;
  const board = encodeURIComponent(g.miro.board_id);
  return `https://miro.com/app/board/${board}/?moveToWidget=${itemId || g.miro.frame_id}`;
}
