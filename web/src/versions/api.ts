// Клиент API версий (studio/server/versions.py).
import { api, ApiError } from "../api";
import type { Funnel } from "../model/types";
import type { EngineReq, EngineSend } from "./engine";

export type DiffGroup = "settings" | "added" | "removed" | "renamed" | "changed" | "edges";
export type StepMark = "added" | "removed" | "renamed" | "changed" | "tombstoned";

export interface Change {
  group: DiffGroup;
  kind: string;
  text: string;
  step_id: string | null;
  step_key: string | null;
  edge_id: string | null;
  field: string | null;
}

export interface Ghost {
  id: string;
  key: string;
  type: string;
  title: string;
  text: string;
  position: { x: number; y: number } | null;
}

export interface Warning {
  step_id: string | null;
  text: string;
}

export interface Diff {
  changes: Change[];
  steps: Record<string, StepMark>;
  ghosts: Ghost[];
  summary: Record<DiffGroup | "total", number>;
  warnings: Warning[];
}

export interface LiveDiff extends Diff {
  against: number | null;
}

export interface VersionRow {
  number: number;
  status: "published" | "archived";
  author: string;
  comment: string;
  created_at: string;
  parent: number | null;
  export_dir: string;
  report: { summary?: Diff["summary"]; warnings?: number; previous?: number | null };
  /** отправки этой версии в движок (по порядку) */
  engine?: EngineSend[];
}

export interface VersionsInfo {
  name: string;
  published: number | null;
  next: number;
  draft: { base_version: number | null; updated_at: string; saves: number; migrated_from: string | null };
  versions: VersionRow[];
  /** запуск движка, выбранный у воронки (запоминается) */
  engine_target?: string | null;
}

export interface Blocker {
  kind: "validation" | "removed" | "model" | "engine" | "nothing" | "empty";
  text: string;
  step_id?: string | null;
  step_key?: string;
  step?: { id: string; key: string; type: string; title: string; type_label: string; position: { x: number; y: number } | null };
}

export interface PublishCheck {
  number: number;
  published: number | null;
  base_version: number | null;
  diff: Diff;
  blockers: Blocker[];
  warnings: Warning[];
  /** пустые шаги-предупреждения: сообщение с картинкой без картинки и т.п. */
  step_warnings?: Warning[];
  /** проблемы выгрузки для bot-engine, которые НЕ блокируют запуск в боте Студии */
  repo_warnings?: string[];
  /** совпадает ли копия движка Студии с bot-engine (same: null – сверить нельзя) */
  engine_drift?: EngineDrift;
  export: { ok: boolean; export_error: string | null; engine: { ok: boolean; error: string | null; steps: number }; compare: { path: string; same: boolean | null; differences: number; error?: string | null } | null } | null;
}

export interface EngineDrift {
  same: boolean | null;
  text: string;
  new?: Record<string, string[]>;
  gone?: Record<string, string[]>;
  studio_commit?: string;
  engine_commit?: string;
}

export interface PublishResult {
  number: number;
  export_dir: string;
  archived: number | null;
  /** есть, если публиковали и в движок: ok=false – версия Студии записана, а в движок не ушла */
  engine?: EngineSend;
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
  if (!r.ok) {
    const e = new ApiError(data.message ?? `HTTP ${r.status}`, data.errors ?? []) as ApiError & { blockers?: Blocker[]; engine?: unknown };
    e.blockers = data.blockers;
    e.engine = data.engine;
    throw e;
  }
  return data as T;
}

const f = (name: string) => `api/funnels/${encodeURIComponent(name)}`;

export const versionsApi = {
  list: (name: string) => call<VersionsInfo>("GET", `${f(name)}/versions`),
  get: (name: string, n: number) => call<Funnel>("GET", `${f(name)}/versions/${n}`),
  diff: (name: string, a: string | number, b: string | number) =>
    call<Diff>("GET", `${f(name)}/diff?a=${a}&b=${b}`),
  liveDiff: (name: string, graph: Funnel, against: number | "published" = "published") =>
    call<LiveDiff>("POST", `${f(name)}/diff`, { graph, against }),
  check: (name: string, validationErrors: number) =>
    call<PublishCheck>("POST", `${f(name)}/publish/check`, { validation_errors: validationErrors }),
  publish: (name: string, a: { comment: string; number: number; validation_errors: number; author?: string; engine?: EngineReq | null }) =>
    call<PublishResult>("POST", `${f(name)}/publish`, a),
  rollback: (name: string, version: number) =>
    call<{ ok: boolean; graph: Funnel }>("POST", `${f(name)}/rollback`, { version }),
  save: api.save,
};

/** Время версии – по Москве: «24.09.2026 14:05 МСК». */
export function mskTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  }).formatToParts(d);
  const g = (t: string) => p.find((x) => x.type === t)?.value ?? "";
  return `${g("day")}.${g("month")}.${g("year")} ${g("hour")}:${g("minute")} МСК`;
}
