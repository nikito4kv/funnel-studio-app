// Примерные значения плейсхолдеров для превью «как увидит человек» и превращение
// Telegram-HTML в безопасную разметку пузыря сообщения.
import type { Branch, Funnel, Platform, Step } from "../model/types";

const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];

export interface SampleCtx {
  platform: Platform;
  branch: Branch | null;
  /** Дата эфира для примера (по умолчанию – завтра). */
  date?: Date;
}

function hhmm(total: number): string {
  const m = ((total % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

function toMin(t: string): number {
  const [h, m] = t.split(":").map(Number);
  return h * 60 + m;
}

/** Все токены движка → примерные значения для этого шага. */
export function sampleValues(step: Step, g: Funnel, ctx: SampleCtx): Record<string, string> {
  const d = ctx.date ?? new Date(Date.now() + 86400000);
  const main = ctx.branch?.webinar_time ?? "11:00";
  const repeat = step.schedule.anchor === "repeat" && ctx.branch ? hhmm(toMin(main) + ctx.branch.repeat_offset_minutes) : main;
  const date = `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
  const words = `${d.getDate()} ${MONTHS[d.getMonth()]} в ${repeat} МСК`;
  const unix = String(Math.floor(d.getTime() / 1000));
  const launch = g.source?.launch || "zapusk";
  const pl = ctx.platform;
  return {
    "{Имя}": "Анна",
    "{имя}": "анна",
    "{channel.link}": pl === "max" ? "https://max.ru/join/kanal" : "https://t.me/+kanal",
    "{channel.title}": "Закрытый канал",
    "{channel.tg_link}": "https://t.me/+kanal",
    "{channel.max_link}": "https://max.ru/join/kanal",
    "{channel.vk_link}": "https://vk.com/kanal",
    "{stream_url}": "https://start.bizon365.ru/room/efir",
    "{vip_link}": "https://bot.example.com/govip",
    "{gift_url}": "https://example.com/podarok",
    "{webinar.time}": repeat,
    "{webinar.main_time}": main,
    "{webinar.date}": date,
    "{webinar.tgtime}": words,
    "{webinar.short}": words,
    "{webinar.unix}": unix,
    "{webinar.main_unix}": unix,
    "{webinar.datetime_msk}": `${d.getFullYear()}-${date.slice(3)}-${date.slice(0, 2)}T${main}`,
    "{ticket}": "1234",
    "{tickets_count}": "2",
    "{tickets_all}": "1234, 1301",
    "{ticket_image}": "",
    "{ref_link}": "https://t.me/bot?start=ref_anna",
    "{game_url}": "https://bot.example.com/game",
    "{slug}": launch,
    "{display_name}": g.title || launch,
    "{platform_tag}": pl === "max" ? "_max" : "",
    "{platform}": pl,
    "{tg_id}": "123456789",
    "{max_id}": "987654321",
    "{user_id}": pl === "max" ? "987654321" : "123456789",
    "{base_url}": "https://bot.example.com",
    "{go_prefix}": `/${launch}`,
    "{utm_source}": "vk",
    "{utm_medium}": "posev",
    "{utm_campaign}": "sep",
    "{utm_content}": "",
    "{utm_term}": "",
    "{lm_title}": "7 новых способов заработка с Claude",
    "{lm_url}": "https://example.com/guide",
    "{lm_post_id}": "2880",
    "{lm_intro}": "Посмотрите короткий урок, пока готовится гайд.",
    "{src_suffix}": "",
    "{diploma_number}": "0042",
    "{diploma_code}": "AB12CD",
    "{diploma_url}": "https://platform.example.com/d/AB12CD",
    "{diploma_image}": "",
  };
}

export function fill(text: string, values: Record<string, string>): string {
  return text.replace(/\{[^{}\s]+\}/g, (t) => (t in values ? values[t] : t));
}

const ALLOWED = new Set(["B", "STRONG", "I", "EM", "U", "INS", "S", "STRIKE", "DEL", "CODE", "PRE", "A", "BLOCKQUOTE", "TG-SPOILER"]);

function linkify(text: string, into: Node): void {
  const re = /https?:\/\/[^\s<>«»"]+/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    const at = m.index ?? 0;
    if (at > last) into.appendChild(document.createTextNode(text.slice(last, at)));
    const a = document.createElement("span");
    a.className = "tl";
    a.textContent = m[0];
    into.appendChild(a);
    last = at + m[0].length;
  }
  if (last < text.length) into.appendChild(document.createTextNode(text.slice(last)));
}

function copy(src: Node, dst: Node): void {
  src.childNodes.forEach((n) => {
    if (n.nodeType === Node.TEXT_NODE) {
      linkify(n.textContent ?? "", dst);
      return;
    }
    if (n.nodeType !== Node.ELEMENT_NODE) return;
    const el = n as Element;
    if (el.tagName === "A") {
      const a = document.createElement("span");
      a.className = "tl";
      a.title = el.getAttribute("href") ?? "";
      copy(el, a);
      dst.appendChild(a);
    } else if (el.tagName === "TG-SPOILER") {
      const sp = document.createElement("span");
      sp.className = "spoiler";
      copy(el, sp);
      dst.appendChild(sp);
    } else if (ALLOWED.has(el.tagName)) {
      const out = document.createElement(el.tagName.toLowerCase());
      copy(el, out);
      dst.appendChild(out);
    } else {
      copy(el, dst); // tg-time, tg-emoji – только содержимое, как увидит человек
    }
  });
}

/** Текст шага с подставленными примерами → HTML пузыря. */
export function bubbleHtml(text: string, values: Record<string, string>): string {
  const doc = new DOMParser().parseFromString(`<body>${fill(text, values)}</body>`, "text/html");
  const out = document.createElement("div");
  copy(doc.body, out);
  return out.innerHTML;
}
