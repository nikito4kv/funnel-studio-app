// Публикация в движок: клиент API и чистая логика диалога (тестируется vitest без браузера).
// Сервер – studio/server/versions.py, логика связи с движком – studio/versions/engine.py.
import { ApiError } from "../api";

export type EngineMode = "new-only" | "migrate";

export const MODE_LABEL: Record<EngineMode, string> = {
  "new-only": "только новым",
  migrate: "перевести всех",
};

export const MODE_HINT: Record<EngineMode, string> = {
  "new-only": "Кто уже внутри воронки, дойдёт по прежней версии; новые люди пойдут по новой. Безопасно по умолчанию.",
  migrate: "Все, кто внутри, переходят на новую версию: время сообщений «по часам» пересчитывается, с удалённых шагов – куда укажете ниже.",
};

export interface TenantBot {
  platform: "tg" | "max" | "vk";
  username: string;
  enabled: boolean;
  /** тестовый бот – не из боевых запусков bot-engine */
  test: boolean;
}

/** Запуск движка = бот (или несколько) + его воронки и настройки. */
export interface Tenant {
  slug: string;
  title: string;
  status: string;
  bots?: TenantBot[];
  /** куда ведёт /start бота (null – воронок ещё нет) */
  entry_funnel?: string | null;
  entry_explicit?: boolean;
  restart_policy?: string;
  funnels?: string[];
}

export interface EngineInfo {
  configured: boolean;
  where: string;
  error: string | null;
  tenants: Tenant[];
}

export interface EngineVersionRow {
  version: number;
  source: string;
  mode: EngineMode;
  published_at: string;
  published_by: string;
  comment: string;
}

export interface InBot {
  engine_version: number;
  /** номер версии Студии, если в бот её отправила Студия (иначе null – например, из командной строки) */
  studio_number: number | null;
  published_at: string;
  published_by: string;
}

export interface FunnelEngine extends EngineInfo {
  tenant: string | null;
  funnel_id: string;
  state: { tenant_exists: boolean; active: number; pending: number; versions: EngineVersionRow[] } | null;
  history: EngineSend[];
  /** что сейчас работает в боте выбранного запуска (null – ещё не запускали) */
  in_bot?: InBot | null;
  /** /start бота ведёт в эту воронку */
  is_entry?: boolean;
  /** «Проверить в боте»: t.me/<бот>?start=studio_test_<воронка> */
  test_link?: string | null;
}

export interface EngineLine {
  step_id: string;
  key: string;
  pending: number;
  awaiting: number;
  awaiting_kinds: Record<string, number>;
  status: "same" | "changed" | "time" | "removed";
  redirect: string | null;
}

export interface EngineReport {
  tenant: string;
  funnel_id: string;
  from_version: number | null;
  to_version: number;
  mode: EngineMode;
  positions_active: number;
  pending_total: number;
  lines: EngineLine[];
  errors: string[];
  warnings: string[];
  migrated: number;
  retimed: number;
  skipped_past: number;
  dry_run: boolean;
}

export interface EngineCheck {
  ok: boolean;
  error: string | null;
  tenant: string;
  funnel_id: string;
  mode: EngineMode;
  report: EngineReport | null;
  text: string;
  /** {id исчезнувшего шага: id шага новой версии} – подсказка карты */
  suggestions: Record<string, string>;
}

/** Строка журнала отправок версии в движок. */
export interface EngineSend {
  id: number;
  number: number;
  tenant: string;
  funnel_id: string;
  mode: EngineMode;
  status: "ok" | "failed";
  ok: boolean;
  engine_version: number | null;
  author: string;
  error: string;
  redirects: Record<string, string>;
  report: Partial<Pick<EngineReport, "positions_active" | "pending_total" | "migrated" | "retimed" | "skipped_past" | "from_version">>;
  created_at: string;
  text?: string;
  /** сделали ли воронку стартовой (make_entry) */
  entry?: { ok: boolean; funnel_id?: string; error?: string } | null;
}

export interface EngineReq {
  tenant: string;
  mode: EngineMode;
  redirects: Record<string, string>;
  /** сделать воронку стартовой: /start бота ведёт сюда */
  make_entry?: boolean;
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(url, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Сервер Студии не отвечает");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new ApiError(data.message ?? `HTTP ${r.status}`, data.errors ?? []) as ApiError & { engine?: EngineCheck };
    e.engine = data.engine;
    throw e;
  }
  return data as T;
}

const f = (name: string) => `api/funnels/${encodeURIComponent(name)}`;

export const engineApi = {
  info: () => call<EngineInfo>("GET", "api/engine"),
  funnel: (name: string) => call<FunnelEngine>("GET", `${f(name)}/engine`),
  setTarget: (name: string, tenant: string) => call<{ ok: boolean }>("PUT", `${f(name)}/engine`, { tenant }),
  check: (name: string, req: EngineReq & { version?: number | null }) =>
    call<EngineCheck>("POST", `${f(name)}/engine/check`, req),
  send: (name: string, number: number, req: EngineReq & { author: string; comment?: string }) =>
    call<EngineSend>("POST", `${f(name)}/versions/${number}/engine`, req),
};

// ── чистая логика ───────────────────────────────────────────────────────────

/** «1 человек», «3 человека», «5 человек». */
export function people(n: number): string {
  const d10 = n % 10;
  const d100 = n % 100;
  const w = d10 === 1 && d100 !== 11 ? "человек" : d10 >= 2 && d10 <= 4 && (d100 < 12 || d100 > 14) ? "человека" : "человек";
  return `${n} ${w}`;
}

const AWAIT_WHAT: Record<string, string> = { contact: "контакт", email: "почту" };

export interface PeopleLine {
  step_id: string;
  key: string;
  status: EngineLine["status"];
  /** что с людьми на шаге – словами */
  text: string;
  /** что станет с шагом */
  fate: string;
  tone: "ok" | "warn" | "bad";
}

/** Отчёт движка → строки «что будет с людьми» (только шаги, где есть люди). */
export function peopleLines(rep: EngineReport | null, stepKey: (id: string) => string | undefined = () => undefined): PeopleLine[] {
  if (!rep) return [];
  return rep.lines
    .filter((l) => l.pending || l.awaiting)
    .map((l) => {
      const what: string[] = [];
      if (l.pending) what.push(`у ${people(l.pending)} запланирован этот шаг`);
      const kinds = Object.entries(l.awaiting_kinds ?? {});
      if (kinds.length) {
        for (const [k, n] of kinds) what.push(`${people(n)} ${n === 1 ? "ждёт" : "ждут"}, когда пришлют ${AWAIT_WHAT[k] ?? "ответ"}`);
      } else if (l.awaiting) {
        what.push(`${people(l.awaiting)} ${l.awaiting === 1 ? "ждёт" : "ждут"} ответа на нём`);
      }
      let fate: string;
      let tone: PeopleLine["tone"] = "ok";
      const migrate = rep.mode === "migrate";
      if (l.status === "same") fate = "шаг не меняется";
      else if (l.status === "changed") fate = migrate ? "получат новый вариант шага" : "у них останется старый вариант, новые получат новый";
      else if (l.status === "time") {
        fate = migrate ? "время изменилось – пересчитаем по новому" : "время изменилось – у идущих останется старое";
        tone = "warn";
      } else {
        const to = l.redirect ?? undefined;
        if (to) fate = migrate ? `шаг исчез – переведём на «${stepKey(to) ?? to}»` : `шаг исчез – доедут по старой версии`;
        else if (migrate) {
          fate = "шаг исчез – укажите, куда их перевести";
          tone = "bad";
        } else fate = "шаг исчез – доедут по старой версии";
        if (tone === "ok") tone = "warn";
      }
      return { step_id: l.step_id, key: l.key, status: l.status, text: what.join(", "), fate, tone };
    });
}

/** Шапка отчёта: сколько людей внутри и что станет с версией в боте. */
export function headline(rep: EngineReport | null, bot: string): string {
  if (!rep) return "";
  const head = rep.from_version
    ? `В боте ${bot} работает прошлая версия этой воронки – её сменит новая.`
    : `В боте ${bot} этой воронки ещё нет – она появится там впервые.`;
  if (!rep.positions_active && !rep.pending_total) return `${head} Людей внутри нет.`;
  return `${head} Внутри воронки ${people(rep.positions_active)}, запланированных сообщений – ${rep.pending_total}.`;
}

// ── бот: как назвать, тестовый ли, что будет после запуска ──────────────────

/** Главный бот запуска: Telegram, иначе MAX. */
export function mainBot(t: Tenant | null | undefined): TenantBot | null {
  const bots = (t?.bots ?? []).filter((b) => b.username);
  return bots.find((b) => b.platform === "tg") ?? bots[0] ?? null;
}

/** «@funnel_studio_TEST_bot» или «Тест Студии», если у запуска нет бота с именем. */
export function botName(t: Tenant | null | undefined): string {
  const b = mainBot(t);
  if (b) return `@${b.username}${b.platform === "max" ? " (MAX)" : ""}`;
  return t ? `«${t.title || t.slug}»` : "";
}

/** Пункт выбора бота: «@funnel_studio_TEST_bot (Тест Студии)». */
export function botOption(t: Tenant): string {
  const b = mainBot(t);
  const title = t.title && t.title !== t.slug ? t.title : "";
  const pause = t.status !== "active" ? " – на паузе" : "";
  if (b) return `@${b.username}${b.platform === "max" ? " (MAX)" : ""}${title ? ` (${title})` : ""}${pause}`;
  return `${title || t.slug} – бот не подключён${pause}`;
}

/** Все боты запуска – тестовые (не из боевых запусков bot-engine). */
export function onlyTestBots(t: Tenant | null | undefined): boolean {
  const bots = t?.bots ?? [];
  return bots.length > 0 && bots.every((b) => b.test);
}

/** Одна-две фразы: что будет после «Запустить в боте». */
export function consequences(rep: EngineReport | null, mode: EngineMode, testOnly: boolean): string {
  if (!rep) return "";
  const out: string[] = [];
  const n = rep.positions_active;
  if (mode === "migrate") {
    out.push("Все – и новые люди, и те, кто уже внутри, – перейдут на эту версию.");
  } else {
    out.push(rep.from_version ? "Новые люди получат эту версию." : "Новые люди пойдут по этой воронке.");
    if (n) out.push(`${people(n)} уже внутри – ${n === 1 ? "он дойдёт" : "они дойдут"} по старой.`);
  }
  if (testOnly) out.push("Боевые боты не затрагиваются.");
  return out.join(" ");
}

export interface BadgeInput {
  eng: FunnelEngine | null;
  /** последняя сохранённая (опубликованная) версия Студии */
  published: number | null;
  /** сколько изменений в редакторе против неё (включая несохранённые) */
  changes: number;
}

export interface Badge {
  kind: "off" | "none" | "live";
  text: string;
  /** есть правки, которых нет в боте */
  behind: boolean;
  title: string;
}

/** Бейдж шапки: работает ли воронка в боте и совпадает ли бот с тем, что в редакторе. */
export function botBadge({ eng, published, changes }: BadgeInput): Badge {
  if (!eng) return { kind: "none", text: "…", behind: false, title: "" };
  if (!eng.configured) return { kind: "off", text: "", behind: false, title: "" };
  if (eng.error) {
    return { kind: "none", text: "Нет связи с ботами", behind: false, title: "Версии сохраняются в Студии, но что сейчас в боте – не видно" };
  }
  const t = eng.tenants.find((x) => x.slug === eng.tenant);
  const inBot = eng.in_bot;
  if (!t || !inBot) {
    return { kind: "none", text: "Ещё не запущена ни в одном боте", behind: false, title: "Нажмите «Запустить в боте», чтобы бот начал вести людей по этой воронке" };
  }
  const n = inBot.studio_number ?? inBot.engine_version;
  const behind = inBot.studio_number === null || inBot.studio_number !== published || changes > 0;
  return {
    kind: "live",
    text: `Работает в боте ${botName(t)} · версия ${n}`,
    behind,
    title: behind
      ? "Бот работает по запущенной версии. Правки из редактора в бот не попадают, пока не нажать «Запустить в боте»"
      : "Бот работает ровно по тому, что в редакторе",
  };
}

/** Надпись главной кнопки диалога. */
export function actionLabel(a: { bot: string | null; busy: boolean }): string {
  if (a.busy) return a.bot ? "Запускаю…" : "Сохраняю…";
  return a.bot ? `Запустить в боте ${a.bot}` : "Сохранить версию";
}

/** Исчезнувшие шаги, на которых есть люди (им нужна карта в режиме «перевести всех»). */
export function removedLines(rep: EngineReport | null): EngineLine[] {
  return (rep?.lines ?? []).filter((l) => l.status === "removed" && (l.pending || l.awaiting));
}

/** Карта для отправки: подсказки сервера, поверх – выбор человека (пустое = «не выбрано»). */
export function mergeRedirects(suggestions: Record<string, string>, chosen: Record<string, string>,
  removed: EngineLine[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const l of removed) {
    const v = l.step_id in chosen ? chosen[l.step_id] : suggestions[l.step_id];
    if (v) out[l.step_id] = v;
  }
  return out;
}

export interface GateState {
  studioBlockers: number;
  studioReady: boolean;
  engineOn: boolean;
  tenant: string;
  check: EngineCheck | null;
  checking: boolean;
  busy: boolean;
}

/** Можно ли жать главную кнопку и почему нет – одной строкой. engineOn и tenant – выбран бот. */
export function publishGate(s: GateState): { ok: boolean; why: string | null } {
  if (s.busy) return { ok: false, why: null };
  if (!s.studioReady) return { ok: false, why: "Проверяю воронку…" };
  if (s.studioBlockers) return { ok: false, why: "Сначала исправьте проблемы, отмеченные красным" };
  if (!s.engineOn || !s.tenant) return { ok: true, why: null };
  if (s.checking || !s.check) return { ok: false, why: "Смотрю, что будет с людьми в боте…" };
  if (!s.check.ok) return { ok: false, why: "Бот не примет эту версию – причина выше" };
  return { ok: true, why: null };
}

/** Строка журнала для «Истории». bot – как назвать бота запуска (иначе – имя запуска). */
export function sendLabel(r: EngineSend, bot?: string): string {
  const where = bot || `«${r.tenant}»`;
  if (r.ok) return `→ запущена в боте ${where} · ${MODE_LABEL[r.mode] ?? r.mode}`;
  return `✕ в бот ${where} не ушла: ${r.error}`;
}

/** Опубликованная версия уже лежит в этом запуске движка? */
export function sentOk(history: EngineSend[], number: number | null, tenant: string): EngineSend | undefined {
  if (number === null) return undefined;
  return history.find((r) => r.number === number && r.tenant === tenant && r.ok);
}

// ── автор публикации (пока без входа – имя в браузере) ──
const AUTHOR_KEY = "fs.publish.author";

export function loadAuthor(): string {
  try {
    return globalThis.localStorage?.getItem(AUTHOR_KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveAuthor(v: string): void {
  try {
    globalThis.localStorage?.setItem(AUTHOR_KEY, v);
  } catch {
    /* приватное окно – не страшно */
  }
}
