// Словарь простого режима: все слова интерфейса, которые для постановщика звучат иначе, чем
// для разработчика. Держим в одном месте, чтобы тексты не расползались по панелям.
//
// Правило: в режиме «Разработчик» интерфейс – как на этапе 1 (key, токены, HTML), в режиме
// «Постановщик» – человеческие слова. Функции принимают флаг simple и возвращают нужный вариант.
import { EDGE_KIND_LABEL, STEP_TYPE_LABEL } from "../model/ops";
import type { Branch, EdgeKind, StepType } from "../model/types";

// ── плейсхолдеры ─────────────────────────────────────────────────────────────

/** Токен движка → как его называет человек. Полнота сверяется тестом с render.py. */
export const PLACEHOLDER_HUMAN: Record<string, string> = {
  "{Имя}": "имя человека",
  "{имя}": "имя (строчными)",
  "{channel.link}": "ссылка на канал",
  "{channel.title}": "название канала",
  "{channel.tg_link}": "ссылка на канал в Telegram",
  "{channel.max_link}": "ссылка на канал в MAX",
  "{channel.vk_link}": "ссылка на группу VK",
  "{stream_url}": "ссылка на эфир",
  "{vip_link}": "оплата ВИП",
  "{gift_url}": "подарок",
  "{webinar.time}": "время эфира",
  "{webinar.main_time}": "время основного эфира",
  "{webinar.date}": "дата эфира",
  "{webinar.tgtime}": "дата и время эфира",
  "{webinar.short}": "дата и время словами",
  "{webinar.unix}": "время эфира для отсчёта",
  "{webinar.main_unix}": "время основного эфира для отсчёта",
  "{webinar.datetime_msk}": "дата и время эфира (служебно)",
  "{ticket}": "номер билета",
  "{tickets_count}": "сколько билетов",
  "{tickets_all}": "все номера билетов",
  "{ticket_image}": "картинка билета",
  "{ref_link}": "реф-ссылка",
  "{game_url}": "ссылка на игру",
  "{slug}": "код запуска",
  "{display_name}": "название запуска",
  "{platform_tag}": "метка платформы",
  "{platform}": "платформа",
  "{tg_id}": "номер человека в Telegram",
  "{max_id}": "номер человека в MAX",
  "{user_id}": "номер человека",
  "{base_url}": "адрес бота",
  "{go_prefix}": "префикс ссылок бота",
  "{utm_source}": "откуда пришёл: источник",
  "{utm_medium}": "откуда пришёл: вид рекламы",
  "{utm_campaign}": "откуда пришёл: кампания",
  "{utm_content}": "откуда пришёл: объявление",
  "{utm_term}": "откуда пришёл: ключ",
  "{lm_title}": "название гайда",
  "{lm_url}": "ссылка на гайд",
  "{lm_post_id}": "номер поста",
  "{lm_intro}": "вступление к гайду",
  "{src_suffix}": "метка источника для другого бота",
  "{diploma_number}": "номер диплома",
  "{diploma_code}": "код диплома",
  "{diploma_url}": "ссылка на диплом",
  "{diploma_image}": "картинка диплома",
};

/** Как показать токен: в простом режиме – человеческим словом, в режиме разработчика – как есть. */
export function placeholderLabel(token: string, simple: boolean): string {
  if (simple) return PLACEHOLDER_HUMAN[token] ?? `неизвестная вставка ${token}`;
  return token.slice(1, -1);
}

/** Заменить все {токены} в простом тексте на человеческие слова в «ёлочках». */
export function humanizeTokens(text: string): string {
  return text.replace(/\{[^{}\s]+\}/g, (t) => (PLACEHOLDER_HUMAN[t] ? `«${PLACEHOLDER_HUMAN[t]}»` : t));
}

/** Куда ведёт кнопка – строка под подписью кнопки на карточке. */
export function buttonTarget(url: string | null, callback: string | null, webApp: boolean, simple: boolean): string | null {
  if (url) {
    const tok = url.trim();
    if (simple && PLACEHOLDER_HUMAN[tok]) return `→ ${PLACEHOLDER_HUMAN[tok]}${webApp ? " (внутри Telegram)" : ""}`;
    if (simple) return `→ ${humanizeTokens(url).replace(/^https?:\/\//, "")}${webApp ? " (внутри Telegram)" : ""}`;
    return `${webApp ? "мини-апп · " : ""}${url}`;
  }
  if (callback) return simple ? "→ действие в боте" : `действие: ${callback}`;
  return null;
}

// ── типы шагов и стрелки ────────────────────────────────────────────────────

export function stepTypeLabel(t: StepType, simple: boolean): string {
  if (simple && t === "jump") return "⤳ Шаг убран";
  return STEP_TYPE_LABEL[t];
}

const EDGE_SIMPLE: Record<EdgeKind, string> = {
  done: "дальше по времени",
  contact: "дал номер",
  skip: "не дал",
  skip_if_phone_known: "номер уже есть",
  email: "прислал почту",
  skip_if_email_known: "почта уже есть",
  callback: "нажал кнопку",
  survey_next: "ответил – следующий вопрос",
  jump: "дальше",
  also_fire: "одновременно",
  chain_to_funnel: "в другую воронку",
};

export function edgeKindLabel(k: EdgeKind, simple: boolean): string {
  return simple ? EDGE_SIMPLE[k] : EDGE_KIND_LABEL[k];
}

/** Подписи точек выхода на карточке. Для запроса почты «не дал» звучит как «не прислал». */
export function exitLabel(k: EdgeKind, stepType: StepType, simple: boolean, devLabel: string): string {
  if (!simple) return devLabel;
  if (stepType === "email_request" && k === "skip") return "не прислал";
  return EDGE_SIMPLE[k];
}

// ── слова панелей ────────────────────────────────────────────────────────────

export const WORDS = {
  conditionTitle: (simple: boolean) => (simple ? "Только если…" : "Условие"),
  conditionHint: (simple: boolean) => (simple ? "иначе шаг пропустится" : "иначе шаг пропускается"),
  alsoFire: (simple: boolean) => (simple ? "одновременно" : "параллельно"),
  alsoFireAdd: (simple: boolean) => (simple ? "одновременно отправить ещё…" : "параллельная стрелка (пунктир) в…"),
  tombstoneCard: (simple: boolean, to: string | null) => (simple
    ? `шаг убран – люди пойдут дальше в «${to ?? "…"}»`
    : "⤳ шаг снят – люди переходят дальше по стрелке"),
  tombstoneHint: (simple: boolean, to: string | null) => (simple
    ? `Шаг убран. Те, кто его ждал, пойдут дальше в «${to ?? "…"}».`
    : "Надгробие: шаг снят, люди с таймером на него идут по стрелке «переход»."),
};

// ── ветки по времени регистрации ────────────────────────────────────────────

function hour(t: string): number {
  return Number(t.split(":")[0]);
}

/** «дневная» / «вечерняя» / «утренняя» / «основная» – по времени эфира ветки. */
export function branchKind(b: Pick<Branch, "webinar_time">, total: number): string {
  if (total <= 1) return "основная";
  const h = hour(b.webinar_time);
  if (h < 11) return "утренняя";
  if (h < 16) return "дневная";
  return "вечерняя";
}

/** «дневная ветка · эфир 13:00» – для панелей и входа в воронку. */
export function branchLong(b: Pick<Branch, "webinar_time">, all: Pick<Branch, "webinar_time">[]): string {
  return `${branchKind(b, all.length)} ветка · эфир ${b.webinar_time}`;
}

/** «дневная · 13:00» – для переключателя в шапке и фишек. */
export function branchShort(b: Pick<Branch, "webinar_time">, all: Pick<Branch, "webinar_time">[]): string {
  return `${branchKind(b, all.length)} · ${b.webinar_time}`;
}

/** id ветки → короткое человеческое имя (для фишек «своё для каждой ветки»). */
export function branchNames(branches: Branch[] | undefined, simple: boolean): Record<string, string> | undefined {
  if (!simple || !branches?.length) return undefined;
  const out: Record<string, string> = {};
  for (const b of branches) out[b.id] = branchShort(b, branches);
  return out;
}

export const DAY_WORD: Record<string, string> = { today: "сегодня", tomorrow: "завтра", fixed: "дата" };
