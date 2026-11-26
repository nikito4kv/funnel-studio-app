// Панель «⚠ Проверка»: правила из приложения А исследования (номера – оттуда же).
// Проверки только читают граф; исправления – через операции (например fixDashes).
import { branchProblems } from "./branches";
import { emptyIssue } from "./emptiness";
import { findEntries } from "./graphinfo";
import { unknownTokens } from "./placeholders";
import type { Button, Edge, Funnel, Step } from "./types";

export type Level = "error" | "warn";

/** Ссылка кнопки заполнена: адрес с доменом, плейсхолдер движка или ссылка Telegram. */
export function linkFilled(url: string): boolean {
  const u = url.trim();
  if (!u) return false;
  if (u.includes("{")) return true;
  if (/^(tg|mailto|tel):/i.test(u)) return true;
  return /^https?:\/\/[^\s/.]+\.[^\s]{2,}/i.test(u);
}

export interface Issue {
  id: string;
  rule: number;
  level: Level;
  message: string;
  stepId?: string;
  edgeId?: string;
  /** Чем исправить автоматически (кнопка «Исправить» в панели проверки). */
  fix?: "dashes" | "token" | "target" | "always_send" | "vip" | "rename" | "shift" | "miro_ok";
  /** Ссылка на элемент доски Miro («открыть в Miro»). */
  link?: string;
  // подробности – для формулировок простого режима и исправлений
  where?: string;
  token?: string;
  btn?: { platform: "tg" | "max"; index: number; text: string };
  otherStep?: string;
  branch?: string;
  len?: number;
  detail?: string;
}

export const RULE_TITLE: Record<number, string> = {
  1: "плейсхолдер, которого нет в движке",
  2: "HTML, который Telegram не примет",
  3: "длина текста",
  4: "длинное тире",
  13: "текст «телефон уже известен» без «всегда слать текст»",
  30: "ВИП-кнопка ведёт на подарок",
  39: "стрелка в никуда",
  40: "повтор технического имени",
  41: "параллельная стрелка у шага с условием",
  42: "шаг без входящих стрелок",
  45: "кнопка без цели",
  55: "два шага цепочки на одно время",
  82: "окна веток по времени регистрации",
  83: "условие есть только в движке Студии",
  84: "пустой шаг",
  90: "импорт из Miro: проверить",
};

/** Правило 84 проверяет и сервер при публикации (блокер «пустой шаг») – в число ошибок
 * для сервера его не включаем, иначе один и тот же шаг блокировал бы дважды. */
export const SERVER_CHECKED_RULES = new Set([84]);

// ── HTML Telegram ────────────────────────────────────────────────────────────

const ALLOWED_TAGS = new Set([
  "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a",
  "blockquote", "tg-spoiler", "tg-emoji", "tg-time", "span",
]);

/** Ошибки разметки: запрещённые теги, незакрытые и лишние закрывающие. */
export function htmlProblems(text: string | null | undefined): string[] {
  if (!text) return [];
  const out: string[] = [];
  const stack: string[] = [];
  for (const m of text.matchAll(/<(\/?)([a-zA-Z][\w-]*)([^<>]*)>/g)) {
    const [, close, rawName] = m;
    const name = rawName.toLowerCase();
    if (!ALLOWED_TAGS.has(name)) {
      out.push(`тег <${name}> Telegram не понимает${name === "br" ? " – перенос строки пишется просто Enter" : ""}`);
      continue;
    }
    if (!close) {
      stack.push(name);
    } else if (stack[stack.length - 1] === name) {
      stack.pop();
    } else if (stack.includes(name)) {
      out.push(`теги перепутаны: </${name}> закрывается раньше <${stack[stack.length - 1]}>`);
      stack.splice(stack.lastIndexOf(name), 1);
    } else {
      out.push(`лишний закрывающий </${name}>`);
    }
  }
  for (const t of stack) out.push(`тег <${t}> не закрыт`);
  return out;
}

export function plainText(html: string): string {
  return html.replace(/<[^<>]+>/g, "")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
}

export function textLimit(step: Step): { limit: number; caption: boolean } {
  const caption = ["photo", "video", "animation"].includes(step.type) || !!step.media.image;
  return { limit: caption ? 1024 : 4096, caption };
}

// ── обход текстов шага ──────────────────────────────────────────────────────

interface TextSlot {
  where: string;
  text: string;
  html: boolean;
}

function buttonSlots(bs: Button[] | null, where: string): TextSlot[] {
  if (!bs) return [];
  return bs.flatMap((b, i) => [
    { where: `${where}, кнопка ${i + 1}`, text: b.text, html: false },
    ...(b.url ? [{ where: `${where}, ссылка кнопки ${i + 1}`, text: b.url, html: false }] : []),
  ]);
}

export function textSlots(s: Step): TextSlot[] {
  const out: TextSlot[] = [
    { where: "текст TG", text: s.content.tg.text, html: true },
    ...buttonSlots(s.content.tg.buttons, "TG"),
  ];
  if (s.content.tg.claim_button_text) out.push({ where: "кнопка-действие TG", text: s.content.tg.claim_button_text, html: false });
  if (s.content.max.text) out.push({ where: "текст MAX", text: s.content.max.text, html: true });
  out.push(...buttonSlots(s.content.max.buttons, "MAX"));
  if (s.content.max.claim_button_text) out.push({ where: "кнопка-действие MAX", text: s.content.max.claim_button_text, html: false });
  s.content.variants.forEach((v, i) => {
    if (v.text) out.push({ where: `вариант ${i + 1}`, text: v.text, html: true });
    if (v.text_max) out.push({ where: `вариант ${i + 1} (MAX)`, text: v.text_max, html: true });
    out.push(...buttonSlots(v.buttons, `вариант ${i + 1}`));
  });
  for (const [where, t] of [
    ["текст «телефон известен»", s.contact.text_if_known],
    ["текст запроса телефона в MAX", s.contact.manual_phone_max_text],
    ["повтор запроса телефона", s.contact.manual_phone_retry_text],
    ["текст «почта известна»", s.email.text_if_known],
    ["повтор запроса почты", s.email.retry_text],
  ] as const) {
    if (t) out.push({ where, text: t, html: true });
  }
  if (s.sms.text) out.push({ where: "SMS", text: s.sms.text, html: false });
  if (s.call.text) out.push({ where: "звонок", text: s.call.text, html: false });
  return out;
}

// ── время ────────────────────────────────────────────────────────────────────

/** Ключ «когда уходит» для сравнения соседей по цепочке (null – по стрелке). */
function timeKey(s: Step, branch: string | null): string | null {
  const sc = s.schedule;
  if (sc.webinar_offset_minutes !== null) {
    const off = (branch && sc.webinar_offset_by_branch?.[branch]) ?? sc.webinar_offset_minutes;
    return `w:${sc.anchor}:${off}:${sc.day_offset}`;
  }
  if (sc.absolute_time_msk) {
    const day = (branch && sc.day_offset_by_branch?.[branch]) ?? sc.day_offset;
    return `c:${sc.absolute_time_msk}:${day}`;
  }
  return null;
}

// ── проверка ────────────────────────────────────────────────────────────────

// одна и та же воронка проверяется из шапки, холста и панели – считаем один раз на версию графа
const cache = new WeakMap<Funnel, Issue[]>();

export function validate(g: Funnel): Issue[] {
  const hit = cache.get(g);
  if (hit) return hit;
  const out = validateUncached(g);
  cache.set(g, out);
  return out;
}

function validateUncached(g: Funnel): Issue[] {
  const out: Issue[] = [];
  const push = (i: Omit<Issue, "id">) => out.push({ ...i, id: `${i.rule}:${i.stepId ?? ""}:${i.edgeId ?? ""}:${out.length}` });
  const byId = new Map(g.steps.map((s) => [s.id, s]));
  const unresolved = new Map(g.unresolved.map((u) => [u.id, u.key]));
  const incoming = new Map<string, Edge[]>();
  for (const e of g.edges) {
    if (e.to_step) incoming.set(e.to_step, [...(incoming.get(e.to_step) ?? []), e]);
  }
  const entries = findEntries(g);
  const branches = g.branch_router?.branches.map((b) => b.id) ?? [null];

  // 40 – дубль key
  const seen = new Map<string, Step>();
  for (const s of g.steps) {
    if (seen.has(s.key)) {
      push({ rule: 40, level: "error", stepId: s.id, fix: "rename", message: `«${s.key}»: такое техническое имя уже есть – движок возьмёт только первый шаг` });
    } else seen.set(s.key, s);
  }

  for (const e of g.edges) {
    const from = byId.get(e.from_step);
    // 39 – стрелка в никуда
    if (e.to_step && !byId.has(e.to_step)) {
      push({ rule: 39, level: "error", stepId: e.from_step, edgeId: e.id, detail: unresolved.get(e.to_step) ?? e.to_step, message: `«${from?.key}»: стрелка ведёт в шаг «${unresolved.get(e.to_step) ?? e.to_step}», которого нет` });
    }
    // 41 – also_fire у гейт-шага теряется
    if (e.kind === "also_fire" && from && activeConditions(from)) {
      push({ rule: 41, level: "error", stepId: from.id, edgeId: e.id, otherStep: e.to_step ?? undefined, message: `«${from.key}»: у шага есть условие, а если он пропустится, параллельная стрелка в «${byId.get(e.to_step!)?.key}» не сработает – люди её не получат` });
    }
    // 55 – два шага цепочки на одно абсолютное время
    if (e.kind === "done" && from && e.to_step) {
      const to = byId.get(e.to_step);
      if (to) {
        for (const b of branches) {
          const a = timeKey(from, b);
          if (a !== null && a === timeKey(to, b)) {
            push({ rule: 55, level: "error", stepId: to.id, edgeId: e.id, otherStep: from.id, branch: b ?? undefined, fix: "shift", message: `«${to.key}» стоит на то же время, что и предыдущий «${from.key}»${b ? ` (ветка ${b})` : ""} – второй выпадет; сдвиньте на минуту` });
            break;
          }
        }
      }
    }
  }

  for (const s of g.steps) {
    // 42 – нет входящих и не вход
    if (!entries.has(s.id) && !(incoming.get(s.id) ?? []).some((e) => e.from_step !== s.id)) {
      push({ rule: 42, level: "warn", stepId: s.id, message: `«${s.key}»: в шаг не ведёт ни одна стрелка и это не вход – воронка до него не дойдёт (если его шлёт внешний модуль – всё в порядке)` });
    }
    // 83 – условие, которого нет у движка bot-engine: в yaml не уходит
    if (s.conditions.link_click) {
      push({ rule: 83, level: "warn", stepId: s.id, message: `«${s.key}»: условие «кликнул ссылку» работает только в движке Студии – в yaml bot-engine его нет, там шаг уйдёт всем` });
    }
    // 84 – шагу нечего отправлять (или картинка/ролик не заданы)
    const empty = emptyIssue(s);
    if (empty) push({ rule: 84, level: empty.level, stepId: s.id, message: empty.text });
    // 13
    if (s.type === "phone_request" && s.contact.text_if_known && !s.contact.always_send_text) {
      push({ rule: 13, level: "error", stepId: s.id, fix: "always_send", message: `«${s.key}»: задан текст «телефон уже известен», но шаг при известном телефоне пропускается – включите «всегда слать текст»` });
    }
    let dash = false;
    const isSurvey = !!s.survey.qid;
    for (const slot of textSlots(s)) {
      for (const t of unknownTokens(slot.text)) {
        push({ rule: 1, level: "error", stepId: s.id, where: slot.where, token: t, fix: "token", message: `«${s.key}», ${slot.where}: ${t} – движок такого не знает, человек увидит скобки` });
      }
      if (slot.html) {
        for (const p of htmlProblems(slot.text)) push({ rule: 2, level: "error", stepId: s.id, where: slot.where, detail: p, message: `«${s.key}», ${slot.where}: ${p}` });
      }
      if (slot.text.includes("—")) dash = true;
    }
    if (dash) push({ rule: 4, level: "warn", stepId: s.id, message: `«${s.key}»: длинное тире «—» – в текстах только короткое «–»`, fix: "dashes" });

    // 3 – длина
    const { caption } = textLimit(s);
    if (s.type !== "video_note") {
      const len = plainText(s.content.tg.text).length;
      if (caption && len > 1024 && len <= 4096) {
        push({ rule: 3, level: "warn", stepId: s.id, len, detail: "caption", message: `«${s.key}»: подпись ${len} знаков > 1024 – медиа и текст уйдут двумя сообщениями` });
      } else if (len > 4096) {
        push({ rule: 3, level: "error", stepId: s.id, len, detail: "tg", message: `«${s.key}»: текст ${len} знаков > 4096 – Telegram не отправит` });
      }
    }
    if (s.content.max.text) {
      const len = plainText(s.content.max.text).length;
      if (len > 4000) push({ rule: 3, level: "error", stepId: s.id, len, detail: "max", message: `«${s.key}»: текст MAX ${len} знаков > 4000` });
    }

    // 45/46 и 30 – кнопки
    for (const [where, bs] of [["TG", s.content.tg.buttons], ["MAX", s.content.max.buttons ?? []]] as const) {
      const platform = where === "TG" ? "tg" as const : "max" as const;
      bs.forEach((b, i) => {
        const btn = { platform, index: i, text: b.text };
        if (!b.text.trim()) push({ rule: 45, level: "error", stepId: s.id, btn, where, message: `«${s.key}», ${where}: у кнопки ${i + 1} нет подписи` });
        if (!isSurvey && b.url === null && !b.callback_data) {
          push({ rule: 45, level: "warn", stepId: s.id, btn, where, fix: "target", message: `«${s.key}», ${where}: кнопка «${b.text}» никуда не ведёт – выберите цель` });
        } else if (b.url !== null && !linkFilled(b.url)) {
          push({ rule: 45, level: "error", stepId: s.id, btn, where, detail: "empty_url", message: `«${s.key}», ${where}: у кнопки «${b.text}» не заполнена ссылка («${b.url}»)` });
        }
        if (/vip|вип/i.test(b.text) && b.url?.includes("{gift_url}")) {
          push({ rule: 30, level: "error", stepId: s.id, btn, where, fix: "vip", message: `«${s.key}», ${where}: ВИП-кнопка «${b.text}» ведёт на подарок – нужна касса ({vip_link})` });
        }
      });
    }
  }
  // 90 – импорт из Miro не уверен: «не поняла время», «кнопка без ссылки», «элемент не отнесён»…
  const miroUrl = (item: string | null) => (g.miro ? `https://miro.com/app/board/${encodeURIComponent(g.miro.board_id)}/?moveToWidget=${item || g.miro.frame_id}` : undefined);
  for (const s of g.steps) {
    for (const d of s.miro?.doubts ?? []) {
      if (d.level !== "check") continue;
      push({ rule: 90, level: "warn", stepId: s.id, fix: "miro_ok", detail: d.text, token: `${d.code}:${d.text}`, link: miroUrl(d.item_id),
        message: `«${s.key}», из Miro: ${d.text}` });
    }
  }
  for (const d of g.miro?.doubts ?? []) {
    if (d.level !== "check") continue;
    push({ rule: 90, level: "warn", fix: "miro_ok", detail: d.text, token: `${d.code}:${d.text}`, link: miroUrl(d.item_id),
      message: `Из Miro: ${d.text}` });
  }
  // 82 – окна веток: дыры в сутках, пересечения, окно через полночь, удалённый первый шаг
  for (const p of branchProblems(g)) push({ rule: 82, level: p.level, branch: p.branchId, message: p.text });
  return out;
}

export function activeConditions(s: Step): boolean {
  const c = s.conditions;
  return !!(c.attendance || c.not_paid || c.paid || c.not_course_paid || c.course_paid || c.channel
    || c.survey_done || c.has_lm || c.no_link_click || c.link_click || c.no_manager_dm || c.max_subscription_gate
    || c.registered_windows?.length);
}

export function issueCounts(issues: Issue[]): { errors: number; warns: number } {
  return {
    errors: issues.filter((i) => i.level === "error").length,
    warns: issues.filter((i) => i.level === "warn").length,
  };
}
