// Импорт из Miro – адреса сервера (studio/server/miro_api.py).
import { call } from "../api";
import type { Diff } from "../versions/api";

export interface MiroDoubtRow {
  step_key: string | null;
  step_id: string | null;
  code: string;
  text: string;
  item_id: string | null;
  level: "check" | "info";
}

export interface MiroResult {
  name: string;
  frame: string;
  created: boolean;
  stats: {
    messages: number; edges: number; check: number; steps_to_check: number; images: number; parser: string;
    notes?: number; days?: number; board?: BoardAccounting;
  };
  doubts: MiroDoubtRow[];
  diff?: Diff;
  report_text?: string[];
}

/** Счёт элементов доски: куда попал каждый (studio/miro/build.py `_notes`). */
export interface BoardAccounting {
  items: number;
  in_steps: number;
  settings: number;
  notes: number;
  skipped: number;
  skipped_reasons: Record<string, number>;
  unaccounted: number;
  connectors?: Record<string, number>;
}

/** «Доска: 553 элемента – в шагах 523, в настройках 13, заметками 16, пропущено 1 (пустая фигура без текста)». */
export function boardLine(b: BoardAccounting | undefined): string {
  if (!b) return "";
  const why = Object.entries(b.skipped_reasons).map(([k, v]) => (v > 1 ? `${k} – ${v}` : k)).join(", ");
  let s = `Доска: ${b.items} элем. – в шагах ${b.in_steps}, в настройках ${b.settings}, заметками ${b.notes}, пропущено ${b.skipped}`;
  if (why) s += ` (${why})`;
  if (b.unaccounted) s += `, не учтено ${b.unaccounted}`;
  return s;
}

export interface MiroJob {
  job: string;
  state: "running" | "done" | "error";
  stage: string;
  progress: { done: number; total: number };
  result?: MiroResult;
  message?: string;
}

export const miroApi = {
  status: () => call<{ token: boolean; claude: boolean }>("GET", "/api/miro/status"),
  start: (link: string, launch: string, name: string) =>
    call<{ job: string }>("POST", "/api/miro/import", { link, launch, name }),
  job: (id: string) => call<MiroJob>("GET", `/api/miro/jobs/${id}`),
  apply: (id: string) => call<{ ok: boolean; name: string }>("POST", `/api/miro/jobs/${id}/apply`),
};

/** Правила доски для постановщика – как на реальных досках запусков (docs/miro_rules.md). */
export const MIRO_RULES: { title: string; text: string }[] = [
  { title: "Одна колонка – одно сообщение", text: "сверху картинка, под ней зелёный прямоугольник с текстом, под ним белые кнопки. Кружок – зелёный круг, в нём сценарий ролика." },
  { title: "Кнопка – белый прямоугольник под сообщением", text: "первая строка – подпись, вторая – ссылка. Кнопка без ссылки со стрелкой – «нажал – и дальше» (например «ПРОЙТИ ОПРОС»)." },
  { title: "Порядок – стрелками", text: "от сообщения (или его кнопки) к следующему. Первое сообщение – стрелка от кнопки «СТАРТ» в первой колонке." },
  { title: "Время и условие – синий стикер у стрелки", text: "одной фразой от предыдущего сообщения: «через 3 минуты если не перешёл в канал», «через 4 часа если не купил VIP», «сразу»." },
  { title: "Условие самого сообщения – жёлтый стикер рядом с ним", text: "«если контакт ранее оставлял – пропускаем этот этап». Заметки для людей (страницы, письма) – оранжевым: в бот они не идут." },
];
