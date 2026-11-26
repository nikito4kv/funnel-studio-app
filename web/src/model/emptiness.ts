// Пустые шаги: бот «выполнит» шаг, а человеку не уйдёт ничего (живой тест 24.09.2026).
// Зеркало studio/model/emptiness.py – формулировки те же (сверяет emptiness.test.ts).
// Правило 84 панели «Проверка»; блокирует «Запустить в боте» проверка публикации сервера.
import type { Step, StepType } from "./types";

const MESSAGE_TYPES = new Set<StepType>(["text", "photo", "video", "animation", "video_note", "rich", "phone_request", "email_request"]);

/** Что увидит человек: текст без тегов и невидимых символов. */
export function visible(html: string | null | undefined): string {
  if (!html) return "";
  return html.replace(/<[^<>]+>/g, "").replace(/&nbsp;/g, " ").replace(/[\s​‌‍⁠﻿]+/g, " ").trim();
}

export function stepName(s: Step): string {
  return s.title.trim() || s.key;
}

export interface EmptyIssue {
  level: "error" | "warn";
  text: string;
}

export function emptyIssue(s: Step): EmptyIssue | null {
  if (!MESSAGE_TYPES.has(s.type)) return null;
  const name = stepName(s);
  const m = s.media;
  const tg = visible(s.content.tg.text);
  const max = s.content.max.text === null ? tg : visible(s.content.max.text);
  const variants = s.content.variants.some((v) => visible(v.text));
  const has = {
    image: !!m.image || (m.images ?? []).some(Boolean),
    video: !!m.video,
    animation: !!m.animation,
    video_note: !!m.video_note || !!m.video_fallback,
    rich: !!m.rich,
  };
  const media = Object.values(has).some(Boolean);
  if (!tg && !media) {
    if (max || variants) {
      const where = max ? "в Telegram" : "для людей вне вариантов формулировок";
      return { level: "warn", text: `Шаг «${name}» ${where} пустой – там бот ничего не отправит` };
    }
    return { level: "error", text: `Шаг «${name}» пустой – бот ничего не отправит` };
  }
  if (s.type === "photo" && !has.image) return { level: "warn", text: `Шаг «${name}» – сообщение с картинкой, но картинки нет: уйдёт только текст` };
  if ((s.type === "video" || s.type === "animation") && !(has.video || has.animation)) {
    return { level: "warn", text: `Шаг «${name}» – сообщение с видео, но ролика нет: уйдёт только текст` };
  }
  if (s.type === "video_note" && !has.video_note) return { level: "warn", text: `Шаг «${name}» – кружок, но ролика нет: уйдёт только текст` };
  return null;
}
