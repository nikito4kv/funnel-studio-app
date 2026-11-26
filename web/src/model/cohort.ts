// Условие «человек пришёл по такой-то ссылке» (CohortMatch движка) – чистые помощники
// для редактора вариантов: список полей, правка одного поля, фраза для человека.
import { defaultCohort } from "./defaults";
import type { CohortMatch, EntryVariant, StepVariant, Uuid } from "./types";

export type CohortField = "utm_source" | "utm_medium" | "utm_campaign" | "trigger" | "lm_post_id" | "lm_post_id_in";

export const COHORT_FIELDS: { key: CohortField; label: string; hint: string }[] = [
  { key: "utm_medium", label: "метка utm_medium", hint: "например posev – посевы, avito, target" },
  { key: "utm_source", label: "метка utm_source", hint: "откуда трафик: vk, tg, blogger…" },
  { key: "utm_campaign", label: "метка utm_campaign", hint: "название кампании" },
  { key: "trigger", label: "вход (trigger)", hint: "reg, lm, ref… – как человек вошёл" },
  { key: "lm_post_id", label: "пост", hint: "номер поста канала, под которым нажали кнопку" },
  { key: "lm_post_id_in", label: "один из постов", hint: "номера постов через запятую" },
];

export const PLATFORM_LABEL: Record<"tg" | "max" | "vk", string> = { tg: "Telegram", max: "MAX", vk: "VK" };

/** Поля, которые заданы в условии. */
export function whenFields(w: CohortMatch): CohortField[] {
  return COHORT_FIELDS.map((f) => f.key).filter((k) => (k === "lm_post_id_in" ? w.lm_post_id_in.length > 0 : !!w[k]));
}

export function whenValue(w: CohortMatch, f: CohortField): string {
  return f === "lm_post_id_in" ? w.lm_post_id_in.join(", ") : (w[f] ?? "");
}

/** Задать поле условия; пустое значение – поле не проверяется. */
export function setWhen(w: CohortMatch, f: CohortField, value: string): CohortMatch {
  if (f === "lm_post_id_in") {
    const list = value.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean);
    return { ...w, lm_post_id_in: list };
  }
  const v = value.trim();
  return { ...w, [f]: v ? v : null };
}

export function clearWhen(w: CohortMatch, f: CohortField): CohortMatch {
  return f === "lm_post_id_in" ? { ...w, lm_post_id_in: [] } : { ...w, [f]: null };
}

export function isEmptyWhen(w: CohortMatch): boolean {
  return whenFields(w).length === 0 && w.platforms.length === 0;
}

/** «если utm_medium = posev и платформа MAX» */
export function whenPhrase(w: CohortMatch): string {
  const parts = whenFields(w).map((f) => {
    const label = COHORT_FIELDS.find((x) => x.key === f)!.label;
    return f === "lm_post_id_in" ? `${label}: ${whenValue(w, f)}` : `${label} = ${whenValue(w, f)}`;
  });
  if (w.platforms.length) parts.push(`платформа ${w.platforms.map((p) => PLATFORM_LABEL[p]).join(" или ")}`);
  return parts.length ? parts.join(" и ") : "всем (условие не задано)";
}

export function newStepVariant(): StepVariant {
  return { when: { ...defaultCohort(), utm_medium: "posev" }, text: null, text_max: null, buttons: null, claim_button_text: null };
}

export function newEntryVariant(entry: Uuid): EntryVariant {
  return { when: { ...defaultCohort(), utm_medium: "posev" }, entry_step: entry };
}
