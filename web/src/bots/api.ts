// Раздел «Боты»: клиент сервера (studio/server/bots.py). Токен уходит на сервер один раз – при
// добавлении бота – и обратно не приходит никогда: в ответах только «…последние 4».
import { ApiError } from "../api";

export interface FunnelRef { name: string; label: string }

export interface WhereItem {
  kind: "studio" | "repo";
  slug: string;
  title: string;
  funnels: FunnelRef[];
  entry_funnel?: string | null;
}

export interface BotCard {
  id: number;
  username: string;
  first_name: string;
  last4: string;
  added_at: string | null;
  avatar: string;
  /** не тестовый бот – правка профиля только после подтверждения «было → стало» */
  careful: boolean;
  where: WhereItem[];
  fresh?: boolean;
}

export interface OtherBot { username: string; test: boolean; where: WhereItem[] }

export interface MaxGroup {
  key: string;
  username: string;
  launches: { slug: string; title: string; prefix: string }[];
  studio: { slug: string; title: string }[];
}

export interface BotsList {
  key: { ok: boolean; source: "env" | "file" | null; error: string | null };
  engine: { configured: boolean; error: string | null };
  bots: BotCard[];
  others: OtherBot[];
  max: MaxGroup[];
}

export interface BotCommand { command: string; description: string }

export interface BotProfile extends BotCard {
  name: string;
  description: string;
  short_description: string;
  commands: BotCommand[];
  flags: {
    can_join_groups?: boolean;
    can_read_all_group_messages?: boolean;
    supports_inline_queries?: boolean;
    has_main_web_app?: boolean;
  };
  limits: { name: number; description: number; short_description: number };
}

export type ProfileField = "name" | "description" | "short_description" | "commands" | "photo";

export interface SaveResult { ok: true; field: ProfileField; was: unknown; now: unknown }

/** 409 от сервера у боевого бота: нужно подтверждение, «было → стало». */
export class NeedConfirm extends Error {
  was: unknown;
  now: unknown;
  where: string[];
  constructor(message: string, was: unknown, now: unknown, where: string[]) {
    super(message);
    this.was = was;
    this.now = now;
    this.where = where;
  }
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(url, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Сервер Студии не отвечает");
  }
  const data = await r.json().catch(() => ({}));
  if (r.status === 409 && data.need_confirm) throw new NeedConfirm(data.message, data.was, data.now, data.where ?? []);
  if (!r.ok) throw new ApiError(data.message ?? `HTTP ${r.status}`);
  return data as T;
}

export const botsApi = {
  list: () => call<BotsList>("GET", "api/bots"),
  index: () => call<{ bots: { id: number; username: string; first_name: string }[] }>("GET", "api/bots/index"),
  add: (token: string) => call<BotCard>("POST", "api/bots", { token }),
  remove: (id: number) => call<{ ok: boolean }>("DELETE", `api/bots/${id}`),
  profile: (id: number) => call<BotProfile>("GET", `api/bots/${id}`),
  save: (id: number, field: ProfileField, value: unknown, confirm = false) =>
    call<SaveResult>("PUT", `api/bots/${id}/profile/${field}`, { value, confirm }),
  descriptionPicUrl: (media: string) => `api/bots-tools/description-pic?media=${encodeURIComponent(media)}`,
};
