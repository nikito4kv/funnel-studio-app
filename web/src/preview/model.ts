// Что увидит человек: шаг → список сообщений в чате (без DOM – проверяется тестом).
// Правила – как у отправителей движка:
//   • подпись к медиа длиннее 1024 знаков – медиа и текст уходят двумя сообщениями (TG);
//   • MAX: null = «как в TG», "" / [] = «в MAX этого нет»;
//   • кружок в TG – без подписи; в MAX кружков нет, уходит видео (с подписью, если так настроено);
//   • запрос телефона в MAX без кнопки контакта – просьба написать номер сообщением.
import type { Button, Funnel, Platform, Step } from "../model/types";
import { plainText } from "../model/validate";

/** Что уходит на платформу (как effective() карточки холста): MAX null = как в TG, "" / [] = нет. */
function effective(step: Step, platform: Platform) {
  const tg = step.content.tg;
  const mx = step.content.max;
  if (platform === "tg") return { text: tg.text, buttons: tg.buttons, claim: tg.claim_button_text };
  return {
    text: mx.text ?? tg.text,
    buttons: mx.buttons ?? tg.buttons,
    claim: mx.claim_button_text === null ? tg.claim_button_text : (mx.claim_button_text || null),
  };
}

export interface PreviewButton {
  text: string;
  kind: "url" | "action" | "answer";
  target: string | null;
}

export interface PreviewMsg {
  kind: "text" | "media" | "circle" | "sms" | "call" | "info";
  /** image / video / animation / video_note – путь из шага */
  media?: string | null;
  mediaKind?: "image" | "video" | "animation" | "video_note" | "rich";
  text?: string;
  buttons?: PreviewButton[];
}

export interface Preview {
  messages: PreviewMsg[];
  /** Кнопка под полем ввода (запрос контакта в TG). */
  reply: string | null;
  warnings: string[];
  notes: string[];
}

function toButtons(bs: Button[], claim: string | null): PreviewButton[] {
  const out: PreviewButton[] = bs.map((b) => ({
    text: b.text || "…",
    kind: b.url ? "url" : b.callback_data ? "action" : "answer",
    target: b.url ?? b.callback_data ?? null,
  }));
  if (claim) out.push({ text: claim, kind: "action", target: null });
  return out;
}

/** Контент с учётом варианта для источника трафика (index – номер варианта, null – обычный). */
export function contentFor(step: Step, platform: Platform, variant: number | null) {
  const eff = effective(step, platform);
  const v = variant !== null ? step.content.variants[variant] : undefined;
  if (!v) return eff;
  const text = platform === "max" ? (v.text_max ?? v.text ?? eff.text) : (v.text ?? eff.text);
  return {
    ...eff,
    text,
    buttons: v.buttons ?? eff.buttons,
    claim: v.claim_button_text ?? eff.claim,
  };
}

export function buildPreview(step: Step, g: Funnel, platform: Platform, variant: number | null = null): Preview {
  const c = contentFor(step, platform, variant);
  const survey = !!step.survey.qid;
  const buttons = toButtons(c.buttons, c.claim);
  if (survey && step.survey.multi) buttons.push({ text: step.survey.done_button, kind: "answer", target: null });
  const out: Preview = { messages: [], reply: null, warnings: [], notes: [] };
  const text = c.text ?? "";
  const hiddenMax = platform === "max" && step.content.max.text === "" && !!step.content.tg.text;
  const len = plainText(text).length;
  const m = step.media;
  const media = m.image ?? m.images?.[0] ?? null;

  switch (step.type) {
    case "jump":
      out.messages.push({ kind: "info", text: "Шаг убран – человеку ничего не приходит, он идёт дальше." });
      return out;
    case "air_pin":
      out.messages.push({ kind: "info", text: "📌 Закреп с отсчётом до эфира – его текст ведёт сам бот." });
      return out;
    case "call":
      out.messages.push({ kind: "call", text: step.call.audio_id ? `Автозвонок: запись №${step.call.audio_id}` : step.call.text ? `Автозвонок голосом: «${step.call.text}»` : "Автозвонок" });
      return out;
    case "sms":
      out.messages.push({ kind: "sms", text: step.sms.text ?? "" });
      return out;
    case "video_note": {
      if (platform === "tg") {
        out.messages.push({ kind: "circle", media: m.video_note, mediaKind: "video_note", buttons: buttons.length ? buttons : undefined });
        if (text) out.notes.push("Текст карточки – сценарий кружка: в Telegram он не отправляется.");
        return out;
      } else {
        const caption = g.max_video_note_caption && !hiddenMax ? text : "";
        out.messages.push({ kind: "media", media: m.video_note, mediaKind: "video", text: caption || undefined, buttons: buttons.length ? buttons : undefined });
        out.notes.push("В MAX кружков нет – уходит обычное видео" + (caption ? " с подписью." : " без подписи."));
        return out;
      }
    }
    default:
      break;
  }

  const mediaKind = step.type === "video" ? "video" : step.type === "animation" ? "animation" : step.type === "rich" ? "rich" : media ? "image" : null;
  const mediaRef = step.type === "video" ? m.video : step.type === "animation" ? m.animation : step.type === "rich" ? m.rich : media;
  if (mediaKind && (mediaRef || step.type === "photo")) {
    const split = platform === "tg" && len > 1024;
    if (split) {
      out.warnings.push(`Подпись ${len} знаков длиннее 1024 – картинка и текст уйдут двумя сообщениями.`);
      out.messages.push({ kind: "media", media: mediaRef, mediaKind });
      out.messages.push({ kind: "text", text, buttons });
    } else {
      out.messages.push({ kind: "media", media: mediaRef, mediaKind, text: text || undefined, buttons });
    }
  } else if (text || buttons.length) {
    out.messages.push({ kind: "text", text, buttons });
  }
  if (hiddenMax) out.notes.push("В MAX текст этого шага скрыт.");
  if (platform === "tg" && len > 4096) out.warnings.push(`Текст ${len} знаков длиннее 4096 – Telegram не отправит.`);
  if (platform === "max" && len > 4000) out.warnings.push(`Текст ${len} знаков длиннее 4000 – MAX не отправит.`);

  if (step.type === "phone_request") {
    if (platform === "max" && step.contact.manual_phone_max) {
      if (step.contact.manual_phone_max_text) out.messages.push({ kind: "text", text: step.contact.manual_phone_max_text });
      out.notes.push("В MAX нет кнопки «поделиться контактом» – человек пишет номер сообщением.");
    } else if (platform === "max") {
      out.notes.push("В MAX кнопки контакта нет, шаг обычно пропускается, как будто номер уже есть.");
    } else {
      out.reply = step.contact.contact_button;
    }
  }
  if (step.type === "email_request") out.notes.push("Бот ждёт почту обычным сообщением.");
  if (!out.messages.length) out.messages.push({ kind: "info", text: "В этом шаге пока нечего показать – нет ни текста, ни картинки." });
  return out;
}
