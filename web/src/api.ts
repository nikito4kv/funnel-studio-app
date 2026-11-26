// Клиент сервера Студии (studio/server). Адреса относительные – работают и из web/dist,
// и через прокси Vite в режиме разработки.
import type { Funnel } from "./model/types";

export class ApiError extends Error {
  errors: { message: string; step_id: string | null; step_key: string | null }[];
  constructor(message: string, errors: ApiError["errors"] = []) {
    super(message);
    this.errors = errors;
  }
}

export async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let r: Response;
  try {
    r = await fetch(url, init);
  } catch {
    throw new ApiError("Сервер Студии не отвечает – запущен ли `uv run python -m studio.server`?");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(data.message ?? `HTTP ${r.status}`, data.errors ?? []);
  return data as T;
}

export interface FunnelListItem {
  name: string;
  id: string;
  title: string;
  launch: string;
  source_path: string;
  steps: number;
  edges: number;
  updated_at: string;
}

export interface SourceItem {
  path: string;
  launch: string;
  file: string;
  name: string;
  imported: boolean;
}

export interface Difference {
  step: string | null;
  field: string | null;
  original: unknown;
  exported: unknown;
  text: string;
}

export interface ExportResult {
  ok: boolean;
  yaml: string;
  export_error: string | null;
  engine: { ok: boolean; error: string | null; steps: number };
  /** same: null – сверить не вышло (исходный файл не читается копией движка Студии), см. error */
  compare: { path: string; same: boolean | null; differences: Difference[]; error?: string | null } | null;
  /** Загруженные в Студию файлы, которые нужно положить в static запуска. */
  media?: ExportMediaItem[];
}

export interface ExportMediaItem {
  ref: string;
  name: string;
  size: number;
  steps: string[];
}

export interface MediaInfo {
  name: string;
  ref: string;
  url: string;
  kind: "image" | "video" | "audio";
  size: number;
  width: number | null;
  height: number | null;
}

export const api = {
  funnels: () => call<FunnelListItem[]>("GET", "api/funnels"),
  sources: () => call<SourceItem[]>("GET", "api/sources"),
  importSource: (path: string, overwrite = false) =>
    call<{ name: string; existed: boolean }>("POST", "api/import", { path, overwrite }),
  create: (launch: string, name: string, id: string) => call<{ name: string }>("POST", "api/funnels", { launch, name, id }),
  get: (name: string) => call<Funnel>("GET", `api/funnels/${encodeURIComponent(name)}`),
  save: (name: string, g: Funnel) => call<{ ok: boolean; history: number }>("PUT", `api/funnels/${encodeURIComponent(name)}`, g),
  exportYaml: (g: Funnel) => call<ExportResult>("POST", "api/export", { graph: g }),
  /** Архив выгрузки: yaml + загруженные файлы + инструкция. */
  exportArchive: async (g: Funnel): Promise<{ blob: Blob; name: string }> => {
    let r: Response;
    try {
      r = await fetch("api/export/archive", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ graph: g }) });
    } catch {
      throw new ApiError("Сервер Студии не отвечает");
    }
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new ApiError(data.message ?? `HTTP ${r.status}`, data.errors ?? []);
    }
    const cd = r.headers.get("Content-Disposition") ?? "";
    const name = /filename="([^"]+)"/.exec(cd)?.[1] ?? "funnel.zip";
    return { blob: await r.blob(), name };
  },
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return call<MediaInfo>("POST", "api/media", fd);
  },
};

/** Ссылка на медиа шага для превью: ./static/x.jpg → files/<запуск>/static/x.jpg. */
export function mediaUrl(ref: string | null | undefined, launch: string): string | null {
  if (!ref) return null;
  if (ref.startsWith("./static/")) return `files/${launch || "_"}/static/${ref.slice("./static/".length)}`;
  if (/^https?:\/\//.test(ref)) return ref;
  return null; // file_id Telegram – показать нечего
}
