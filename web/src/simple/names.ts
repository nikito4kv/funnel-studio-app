// Как назвать шаг человеку: название карточки, иначе первая строка текста, иначе тип.
// Технический key постановщику не показываем – он для разработчика.
import type { Step } from "../model/types";
import { plainText } from "../model/validate";
import { humanizeTokens, stepTypeLabel } from "./labels";

const MAX = 48;

function cut(s: string, n = MAX): string {
  return s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s;
}

/** Первая непустая строка текста шага – без разметки, плейсхолдеры словами. */
export function firstLine(step: Step, n = MAX): string {
  const src = step.content.tg.text || step.content.max.text || step.sms.text || step.call.text || "";
  let line = plainText(src).split("\n").map((x) => x.trim()).find(Boolean) ?? "";
  // «{Имя}, привет!» – обращение по имени названию шага ничего не даёт
  const m = /^(\P{L}*?)\{[Ии]мя\}\s*[,!.]?\s*(.+)$/u.exec(line);
  if (m) line = m[1] + m[2].charAt(0).toUpperCase() + m[2].slice(1);
  return cut(humanizeTokens(line), n);
}

/** Имя шага для людей. */
export function stepName(step: Step, n = MAX): string {
  if (step.title.trim()) return cut(step.title.trim(), n);
  const line = firstLine(step, n);
  if (line) return line;
  if (step.survey.qid) return `вопрос ${step.survey.qid}`;
  return stepTypeLabel(step.type, true).replace(/^\S+\s/, "");
}

/** Имя шага в нужном режиме: разработчику – key, постановщику – человеческое. */
export function stepRef(step: Step | undefined | null, simple: boolean): string {
  if (!step) return "?";
  return simple ? stepName(step) : step.key;
}
