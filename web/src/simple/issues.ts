// Замечания проверки словами постановщика + автоматические исправления.
// Разработчику показывается исходное сообщение (с key и токенами), постановщику – это.
import { toMin } from "../model/branches";
import * as ops from "../model/ops";
import { PLACEHOLDERS } from "../model/placeholders";
import type { Funnel, Step } from "../model/types";
import type { Issue } from "../model/validate";
import { humanizeTokens, PLACEHOLDER_HUMAN } from "./labels";
import { stepName } from "./names";

function nm(g: Funnel, id: string | undefined): string {
  const s = id ? g.steps.find((x) => x.id === id) : undefined;
  return s ? `«${stepName(s)}»` : "шаг";
}

function whereWords(where: string | undefined): string {
  if (!where) return "";
  return where
    .replace(/^текст TG$/, "в тексте")
    .replace(/^текст MAX$/, "в тексте для MAX")
    .replace(/^TG, кнопка (\d+)$/, "на кнопке $1")
    .replace(/^TG, ссылка кнопки (\d+)$/, "в ссылке кнопки $1")
    .replace(/^MAX, кнопка (\d+)$/, "на кнопке $1 в MAX")
    .replace(/^MAX, ссылка кнопки (\d+)$/, "в ссылке кнопки $1 в MAX")
    .replace(/^вариант (\d+)$/, "в тексте варианта $1")
    .replace(/^(.*)$/, (m) => (m.startsWith("в ") || m.startsWith("на ") ? m : `в поле «${m}»`));
}

/** Формулировка для постановщика. */
export function simpleIssueText(i: Issue, g: Funnel): string {
  const s = nm(g, i.stepId);
  switch (i.rule) {
    case 1:
      return `${s}: ${whereWords(i.where)} стоит вставка ${i.token} – бот её не знает, человек увидит фигурные скобки. Выберите, что подставить.`;
    case 2:
      return `${s}: ${whereWords(i.where)} сломано оформление (${humanizeTokens(i.detail ?? "")}). Откройте карточку и проверьте жирный/курсив/ссылки.`;
    case 3:
      if (i.detail === "caption") return `${s}: подпись к картинке длиннее 1024 знаков (${i.len}) – картинка и текст придут двумя сообщениями. Можно сократить текст.`;
      if (i.detail === "max") return `${s}: текст для MAX длиннее 4000 знаков (${i.len}) – MAX его не отправит. Сократите.`;
      return `${s}: текст длиннее 4096 знаков (${i.len}) – Telegram его не отправит. Сократите или разбейте на два сообщения.`;
    case 4:
      return `${s}: в тексте длинное тире «—», а у нас принято короткое «–».`;
    case 13:
      return `${s}: есть текст «номер уже известен», но шаг таким людям не отправляется – включите «всегда слать текст».`;
    case 30:
      return `${s}: кнопка «${i.btn?.text}» про ВИП, а ведёт на подарок – должна вести на оплату ВИП.`;
    case 39:
      return `${s}: стрелка ведёт в шаг, которого больше нет. Протяните её заново.`;
    case 40:
      return `${s}: у двух шагов одинаковое служебное имя – бот увидит только первый. Исправление даст этому шагу новое имя.`;
    case 41:
      return `${s}: у шага есть «только если…», и если он пропустится, одновременная стрелка в ${nm(g, i.otherStep)} не сработает – люди её не получат.`;
    case 42:
      return `${s}: к шагу не ведёт ни одна стрелка – до него никто не дойдёт (если его отправляет отдельный модуль бота – всё в порядке).`;
    case 45:
      if (i.fix === "target") return `${s}: кнопка «${i.btn?.text}» никуда не ведёт. Выберите, куда она ведёт.`;
      if (i.detail === "empty_url") return `${s}: у кнопки «${i.btn?.text}» не вписана ссылка – человек нажмёт и никуда не попадёт. Откройте карточку и вставьте адрес.`;
      return `${s}: у кнопки №${(i.btn?.index ?? 0) + 1} нет подписи.`;
    case 55:
      return `${s} стоит на то же время, что и предыдущий ${nm(g, i.otherStep)} – второй не придёт. Сдвиньте на минуту.`;
    case 82:
      return `Ветки по времени регистрации: ${i.message}`;
    case 90:
      return i.stepId ? `${s}: ${humanizeTokens(i.detail ?? "")}` : `Доска Miro: ${humanizeTokens(i.detail ?? "")}`;
    case 83:
      return `${s}: условие «кликнул ссылку» работает, только когда воронку исполняет движок Студии. В файл для старого бота оно не попадёт – там шаг получат все.`;
    default:
      return humanizeTokens(i.message);
  }
}

/** Варианты для исправлений с выбором. */
export const TOKEN_CHOICES = PLACEHOLDERS.filter((p) => p.palette).map((p) => ({ value: p.token, label: PLACEHOLDER_HUMAN[p.token] ?? p.label }));

export const TARGET_CHOICES = [
  { value: "{channel.link}", label: "на канал" },
  { value: "{stream_url}", label: "на эфир" },
  { value: "{vip_link}", label: "на оплату ВИП" },
  { value: "{gift_url}", label: "на подарок" },
];

function addMin(t: string, d: number): string {
  const m = (((toMin(t) + d) % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

/** Сдвинуть время шага на минуту позже (для той ветки, где совпало, если время своё у ветки). */
function shiftStep(g: Funnel, s: Step, branch: string | undefined): Funnel {
  const sc = s.schedule;
  if (sc.webinar_offset_minutes !== null) {
    if (branch && sc.webinar_offset_by_branch && branch in sc.webinar_offset_by_branch) {
      return ops.setSchedule(g, { stepId: s.id, patch: { webinar_offset_by_branch: { ...sc.webinar_offset_by_branch, [branch]: sc.webinar_offset_by_branch[branch] + 1 } } });
    }
    return ops.setSchedule(g, { stepId: s.id, patch: { webinar_offset_minutes: sc.webinar_offset_minutes + 1 } });
  }
  if (sc.absolute_time_msk) return ops.setSchedule(g, { stepId: s.id, patch: { absolute_time_msk: addMin(sc.absolute_time_msk, 1) } });
  return g;
}

/** Можно ли исправить одним нажатием (без выбора). */
export function needsChoice(i: Issue): boolean {
  return i.fix === "token" || i.fix === "target";
}

/** Применить исправление. choice – выбранный токен / цель для правил 1 и 45. */
export function applyFix(g: Funnel, i: Issue, choice?: string): Funnel {
  const step = i.stepId ? g.steps.find((s) => s.id === i.stepId) : undefined;
  switch (i.fix) {
    case "dashes":
      return ops.fixDashes(g, { stepIds: i.stepId ? [i.stepId] : undefined });
    case "token":
      if (!step || !i.token || !choice) return g;
      return ops.replaceToken(g, { stepId: step.id, from: i.token, to: choice });
    case "target":
      if (!step || !i.btn || !choice) return g;
      return ops.setButtonUrl(g, { stepId: step.id, platform: i.btn.platform, index: i.btn.index, url: choice });
    case "vip":
      if (!step || !i.btn) return g;
      return ops.setButtonUrl(g, { stepId: step.id, platform: i.btn.platform, index: i.btn.index, url: "{vip_link}" });
    case "always_send":
      if (!step) return g;
      return ops.setContact(g, { stepId: step.id, patch: { always_send_text: true } });
    case "rename":
      if (!step) return g;
      return ops.setKey(g, { stepId: step.id, key: ops.uniqueKey(g, `${step.key}_2`) });
    case "shift":
      if (!step) return g;
      return shiftStep(g, step, i.branch);
    case "miro_ok":
      return ops.dismissMiroDoubt(g, { stepId: i.stepId ?? null, key: i.token ?? "" });
    default:
      return g;
  }
}
