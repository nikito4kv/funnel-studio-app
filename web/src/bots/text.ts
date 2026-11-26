// Раздел «Боты»: слова и чистая логика (тестируется vitest без браузера).
import type { BotCommand, BotProfile, ProfileField, WhereItem } from "./api";

export const FIELD_LABEL: Record<ProfileField, string> = {
  name: "Имя",
  description: "Описание",
  short_description: "Короткое описание",
  commands: "Команды меню",
  photo: "Аватар",
};

/** «12 / 64» и не вышли ли за предел. */
export function counter(value: string, limit: number): { text: string; over: boolean } {
  const n = [...value].length;
  return { text: `${n} / ${limit}`, over: n > limit };
}

/** «в Студии: запуск studio-test» / «в bot-engine: запуск webinar – Spreadsheet Automation Workshop». */
export function whereLine(w: WhereItem): string {
  const title = w.title && w.title !== w.slug ? ` – ${w.title}` : "";
  return w.kind === "studio" ? `в Студии: запуск ${w.slug}${title}` : `в bot-engine: запуск ${w.slug}${title}`;
}

/** Строка из «/команда – описание» по строке на команду. Пустые строки выкидываем, «/» снимаем. */
export function cleanCommands(list: BotCommand[]): BotCommand[] {
  return list
    .map((c) => ({ command: c.command.trim().replace(/^\/+/, "").trim(), description: c.description.trim() }))
    .filter((c) => c.command || c.description);
}

/** Та же проверка, что на сервере: ошибка словами или null. */
export function commandsError(list: BotCommand[]): string | null {
  const clean = cleanCommands(list);
  const seen = new Set<string>();
  for (const c of clean) {
    if (!/^[a-z0-9_]{1,32}$/.test(c.command)) return `«/${c.command}»: только маленькие латинские буквы, цифры и «_», до 32 символов`;
    if (!c.description) return `/${c.command}: нужно описание – его видно в меню бота`;
    if (c.description.length > 256) return `/${c.command}: описание длиннее 256 символов`;
    if (seen.has(c.command)) return `/${c.command} повторяется`;
    seen.add(c.command);
  }
  if (clean.length > 100) return "команд больше 100 – Telegram столько не примет";
  return null;
}

/** Значение поля для «было → стало». */
export function showValue(field: ProfileField, v: unknown): string {
  if (field === "photo") return v ? "новая картинка" : "нет аватара";
  if (field === "commands") {
    const list = (v as BotCommand[] | null) ?? [];
    return list.length ? list.map((c) => `/${c.command} – ${c.description}`).join("\n") : "(команд нет)";
  }
  const s = String(v ?? "");
  return s ? s : "(пусто)";
}

export function sameValue(field: ProfileField, a: unknown, b: unknown): boolean {
  if (field === "commands") return JSON.stringify(cleanCommands((a as BotCommand[]) ?? [])) === JSON.stringify(cleanCommands((b as BotCommand[]) ?? []));
  return String(a ?? "").trim() === String(b ?? "").trim();
}

/** Первая буква для кружка без аватара. */
export function initial(name: string, username: string): string {
  const s = (name || username || "?").trim();
  return [...s][0]?.toUpperCase() ?? "?";
}

// ── чего нельзя через API: пошагово для @BotFather ──

export interface HintStep { text: string; copy?: string }
export interface Hint { id: string; title: string; why: string; steps: HintStep[]; state?: string }

export function botfatherHints(username: string, flags: BotProfile["flags"] = {}): Hint[] {
  const at = `@${username}`;
  const pick: HintStep = { text: `Выберите бота ${at} из списка`, copy: at };
  return [
    {
      id: "descpic",
      title: "Картинка в пустом чате",
      why: "Картинка над описанием – её видит человек до /start. Через Студию её поставить нельзя, только в @BotFather.",
      steps: [
        { text: "Откройте @BotFather", copy: "@BotFather" },
        { text: "Отправьте команду", copy: "/setdescriptionpic" },
        pick,
        { text: "Пришлите картинку 640×360 – её можно подготовить ниже и скачать" },
      ],
    },
    {
      id: "inline",
      title: "Встроенный режим (inline)",
      why: "Чтобы бота можно было позвать в любом чате через «@имя_бота …». Для воронок обычно не нужен.",
      state: flags.supports_inline_queries ? "сейчас включён" : "сейчас выключен",
      steps: [
        { text: "Откройте @BotFather", copy: "@BotFather" },
        { text: "Отправьте команду", copy: "/setinline" },
        pick,
        { text: "Пришлите подсказку для поля ввода (например «Поиск…») – режим включится" },
      ],
    },
    {
      id: "domain",
      title: "Домен и мини-приложение",
      why: "Вход через Telegram на сайте и мини-приложение (кнопка «Открыть» в профиле бота).",
      state: flags.has_main_web_app ? "мини-приложение есть" : "мини-приложения нет",
      steps: [
        { text: "Для входа на сайте: в @BotFather команда", copy: "/setdomain" },
        pick,
        { text: "Пришлите домен сайта, например", copy: "example.com" },
        { text: "Для мини-приложения: команда", copy: "/newapp" },
        { text: "Дальше @BotFather спросит название, описание, картинку 640×360 и адрес страницы" },
      ],
    },
    {
      id: "privacy",
      title: "Приватность в группах",
      why: "Включена – в группах бот видит только команды и ответы ему. Выключена – читает всё. Воронкам в личке это не важно.",
      state: flags.can_read_all_group_messages ? "сейчас бот читает все сообщения групп" : "сейчас бот видит в группах только команды",
      steps: [
        { text: "Откройте @BotFather", copy: "@BotFather" },
        { text: "Отправьте команду", copy: "/setprivacy" },
        pick,
        { text: "Нажмите Enable (только команды) или Disable (всё)" },
      ],
    },
  ];
}

export const TOKEN_STEPS: HintStep[] = [
  { text: "Откройте в Telegram @BotFather", copy: "@BotFather" },
  { text: "Отправьте команду", copy: "/mybots" },
  { text: "Нажмите на нужного бота" },
  { text: "Нажмите «API Token» – бот пришлёт строку вида 123456789:AAH…" },
  { text: "Скопируйте её целиком и вставьте сюда" },
];
