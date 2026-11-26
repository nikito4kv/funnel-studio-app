// Плейсхолдеры движка (common/launch_engine/funnel/render.py) – палитра вставки и проверка.

export interface PlaceholderInfo {
  token: string;
  label: string;
  hint: string;
  /** Показывать в палитре (остальные – известны проверке, но редкие). */
  palette?: boolean;
}

export const PLACEHOLDERS: PlaceholderInfo[] = [
  { token: "{Имя}", label: "Имя", hint: "имя человека", palette: true },
  { token: "{channel.link}", label: "Канал", hint: "ссылка на канал – своя на каждой платформе", palette: true },
  { token: "{stream_url}", label: "Эфир", hint: "ссылка на эфир (зал запуска)", palette: true },
  { token: "{vip_link}", label: "ВИП", hint: "ссылка на оплату ВИП (считает клик)", palette: true },
  { token: "{gift_url}", label: "Подарок", hint: "ссылка на подарок", palette: true },
  { token: "{webinar.time}", label: "Время эфира", hint: "ЧЧ:ММ эфира; в шаге повтора – время повтора", palette: true },
  { token: "{webinar.main_time}", label: "Основное время", hint: "ЧЧ:ММ основного эфира дня – даже в повторе", palette: true },
  { token: "{webinar.tgtime}", label: "Дата-время (tg-time)", hint: "«17 сентября в 11:00 МСК» с локальным временем в Telegram", palette: true },
  { token: "{webinar.date}", label: "Дата эфира", hint: "ДД.ММ", palette: true },
  { token: "{webinar.short}", label: "Дата словами", hint: "«17 сентября в 11:00 МСК»" },
  { token: "{webinar.unix}", label: "unix эфира", hint: "для <tg-time unix=…>" },
  { token: "{webinar.main_unix}", label: "unix основного эфира", hint: "для <tg-time unix=…>" },
  { token: "{webinar.datetime_msk}", label: "дата-время ISO", hint: "ГГГГ-ММ-ДДTЧЧ:ММ" },
  { token: "{ticket}", label: "Номер билета", hint: "номер билета розыгрыша", palette: true },
  { token: "{tickets_count}", label: "Билетов", hint: "сколько билетов у человека" },
  { token: "{tickets_all}", label: "Все билеты", hint: "список билетов" },
  { token: "{ticket_image}", label: "Картинка билета", hint: "картинка билета (в поле картинки)" },
  { token: "{ref_link}", label: "Реф-ссылка", hint: "личная ссылка «пригласи друга»", palette: true },
  { token: "{game_url}", label: "Игра", hint: "ссылка на игру" },
  { token: "{channel.title}", label: "Название канала", hint: "" },
  { token: "{channel.tg_link}", label: "Канал TG", hint: "ссылка на TG-канал на любой платформе" },
  { token: "{channel.max_link}", label: "Канал MAX", hint: "ссылка на MAX-канал на любой платформе" },
  { token: "{channel.vk_link}", label: "Канал VK", hint: "" },
  { token: "{имя}", label: "имя (строчными)", hint: "" },
  { token: "{slug}", label: "slug запуска", hint: "" },
  { token: "{display_name}", label: "название запуска", hint: "" },
  { token: "{platform_tag}", label: "метка платформы", hint: "_vk / _max в deep-link" },
  { token: "{platform}", label: "платформа", hint: "tg / max / vk" },
  { token: "{tg_id}", label: "tg_id", hint: "" },
  { token: "{max_id}", label: "max_id", hint: "" },
  { token: "{user_id}", label: "user_id", hint: "tg_id или max_id" },
  { token: "{base_url}", label: "base_url", hint: "" },
  { token: "{go_prefix}", label: "go_prefix", hint: "" },
  { token: "{utm_source}", label: "utm_source", hint: "метка, с которой человек пришёл" },
  { token: "{utm_medium}", label: "utm_medium", hint: "" },
  { token: "{utm_campaign}", label: "utm_campaign", hint: "" },
  { token: "{utm_content}", label: "utm_content", hint: "" },
  { token: "{utm_term}", label: "utm_term", hint: "" },
  { token: "{lm_title}", label: "Лидмагнит", hint: "название гайда" },
  { token: "{lm_url}", label: "Ссылка на лидмагнит", hint: "" },
  { token: "{lm_post_id}", label: "id поста", hint: "" },
  { token: "{lm_intro}", label: "Вступление к гайду", hint: "" },
  { token: "{src_suffix}", label: "метка источника", hint: "для передачи в другой бот" },
  { token: "{diploma_number}", label: "Номер диплома", hint: "" },
  { token: "{diploma_code}", label: "Код диплома", hint: "" },
  { token: "{diploma_url}", label: "Ссылка на диплом", hint: "" },
  { token: "{diploma_image}", label: "Картинка диплома", hint: "" },
];

export const KNOWN_TOKENS = new Set(PLACEHOLDERS.map((p) => p.token));
export const TOKEN_RE = /\{[^{}\s]+\}/g;

export function unknownTokens(text: string | null | undefined): string[] {
  if (!text) return [];
  const out: string[] = [];
  for (const m of text.matchAll(TOKEN_RE)) if (!KNOWN_TOKENS.has(m[0])) out.push(m[0]);
  return out;
}

/** Автозамена при вводе: что человек пишет по привычке досок → токен движка. */
export const AUTO_REPLACE: { pattern: string; to: string }[] = [
  { pattern: "[ИМЯ]", to: "{Имя}" },
  { pattern: "[Имя]", to: "{Имя}" },
  { pattern: "ИМЯ,", to: "{Имя}," },
  { pattern: "ОСНОВНОЕ ВРЕМЯ", to: "{webinar.main_time}" },
  { pattern: "ВРЕМЯ ПОВТОРА", to: "{webinar.time}" },
  { pattern: "—", to: "–" },
];

/** Если текст перед курсором кончается шаблоном – вернуть [длина шаблона, замена]. */
export function autoReplaceAt(before: string): { remove: number; insert: string } | null {
  for (const r of AUTO_REPLACE) {
    if (before.endsWith(r.pattern)) return { remove: r.pattern.length, insert: r.to };
  }
  return null;
}

/** Применить все автозамены к целому тексту (вставка из буфера). */
export function autoReplaceAll(text: string): string {
  let out = text;
  for (const r of AUTO_REPLACE) out = out.split(r.pattern).join(r.to);
  return out;
}
