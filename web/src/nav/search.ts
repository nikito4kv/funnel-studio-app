// Поиск шага по тексту и названию (Ctrl+F). Чистые функции – проверяются тестом.
import type { Funnel, Step } from "../model/types";
import { plainText } from "../model/validate";
import { humanizeTokens } from "../simple/labels";
import { stepName } from "../simple/names";

export interface SearchHit {
  stepId: string;
  name: string;
  key: string;
  lane: string | null;
  /** Где нашлось: «название», «текст», «кнопка», «MAX», «вариант»… */
  where: string;
  /** Кусок текста вокруг совпадения: [до, совпадение, после]. */
  snippet: [string, string, string];
}

export function norm(s: string): string {
  return s.toLowerCase().replace(/ё/g, "е");
}

function fields(s: Step): { where: string; text: string }[] {
  const out: { where: string; text: string }[] = [];
  if (s.title) out.push({ where: "название", text: s.title });
  const plain = (t: string) => humanizeTokens(plainText(t));
  if (s.content.tg.text) out.push({ where: "текст", text: plain(s.content.tg.text) });
  if (s.content.max.text) out.push({ where: "текст MAX", text: plain(s.content.max.text) });
  for (const b of [...s.content.tg.buttons, ...(s.content.max.buttons ?? [])]) out.push({ where: "кнопка", text: b.text });
  if (s.content.tg.claim_button_text) out.push({ where: "кнопка", text: s.content.tg.claim_button_text });
  s.content.variants.forEach((v) => {
    if (v.text) out.push({ where: "вариант для источника", text: plain(v.text) });
  });
  if (s.sms.text) out.push({ where: "SMS", text: humanizeTokens(s.sms.text) });
  if (s.call.text) out.push({ where: "звонок", text: s.call.text });
  out.push({ where: "техническое имя", text: s.key });
  return out;
}

function snippet(text: string, at: number, len: number): [string, string, string] {
  const flat = text.replace(/\s+/g, " ");
  // позиции в «сплющенном» тексте могут сдвинуться – ищем заново
  const i = norm(flat).indexOf(norm(text.slice(at, at + len)).replace(/\s+/g, " "));
  const p = i >= 0 ? i : 0;
  const before = flat.slice(Math.max(0, p - 30), p);
  return [(p > 30 ? "…" : "") + before, flat.slice(p, p + len), flat.slice(p + len, p + len + 60) + (flat.length > p + len + 60 ? "…" : "")];
}

/** Шаги, где встречается запрос. Сначала совпадения в названии, потом в тексте. */
export function searchSteps(g: Funnel, query: string, limit = 40): SearchHit[] {
  const q = norm(query.trim());
  if (!q) return [];
  const laneOf = new Map<string, string>();
  for (const l of g.lanes) for (const id of l.step_ids) laneOf.set(id, l.title);
  const hits: (SearchHit & { rank: number })[] = [];
  for (const s of g.steps) {
    for (const f of fields(s)) {
      const at = norm(f.text).indexOf(q);
      if (at < 0) continue;
      hits.push({
        stepId: s.id, name: stepName(s), key: s.key, lane: laneOf.get(s.id) ?? null,
        where: f.where, snippet: snippet(f.text, at, q.length), rank: f.where === "название" ? 0 : f.where === "техническое имя" ? 2 : 1,
      });
      break;
    }
  }
  hits.sort((a, b) => a.rank - b.rank);
  return hits.slice(0, limit).map(({ rank: _r, ...h }) => h);
}
