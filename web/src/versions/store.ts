// Состояние версий: список версий, дифф «черновик ↔ опубликованная» для шапки и холста,
// режим «Показать изменения», сравнения в панели «История». Отдельный стор, чтобы не трогать
// основной (store.ts): граф редактора читается из него только на чтение.
import { create } from "zustand";
import type { Funnel } from "../model/types";
import { useStudio } from "../store";
import { versionsApi, type Diff, type LiveDiff, type VersionsInfo } from "./api";
import { engineApi, type FunnelEngine } from "./engine";

export interface Compare {
  title: string;
  diff: Diff;
  /** под какой версией в списке показать */
  at: number;
}

interface VState {
  name: string | null;
  info: VersionsInfo | null;
  /** текущий граф редактора против опубликованной (для «изменено N») */
  live: LiveDiff | null;
  /** против чего подсвечивать холст: null – опубликованная, иначе номер версии */
  base: number | null;
  /** дифф для подсветки холста (= live, если base не задан) */
  overlay: LiveDiff | null;
  showChanges: boolean;
  publishOpen: boolean;
  compare: Compare | null;
  error: string | null;
  /** что с воронкой в боте (бейдж шапки, «Проверить в боте»); null – ещё не знаем */
  engine: FunnelEngine | null;

  refresh: (name: string) => Promise<void>;
  setBase: (n: number | null) => void;
  set: (p: Partial<Pick<VState, "showChanges" | "publishOpen" | "compare">>) => void;
}

let seq = 0;
let timer: ReturnType<typeof setTimeout> | null = null;

/** Части графа, от которых зависит дифф: раскладка, полосы и заметки в него не входят. */
function signature(g: Funnel): unknown[] {
  return [g.steps, g.edges, g.branch_router, g.entry_variants, g.webinar_room, g.air_email_profile,
    g.max_video_note_caption, g.resend_entry_on_repeat, g.id, g.title, g.unresolved];
}
let lastSig: unknown[] | null = null;
let lastName: string | null = null;

async function computeLive(force = false): Promise<void> {
  const { name, funnel } = useStudio.getState();
  if (!name || !funnel) return;
  const sig = signature(funnel);
  if (!force && name === lastName && lastSig && sig.every((x, i) => x === lastSig![i])) return;
  lastSig = sig;
  lastName = name;
  const my = ++seq;
  const base = useVersions.getState().base;
  try {
    const live = await versionsApi.liveDiff(name, funnel);
    const overlay = base === null ? live : await versionsApi.liveDiff(name, funnel, base);
    if (my !== seq) return;
    useVersions.setState({ live, overlay, error: null });
  } catch (e) {
    if (my === seq) useVersions.setState({ error: (e as Error).message });
  }
}

export function scheduleLive(delay = 400): void {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => { timer = null; void computeLive(); }, delay);
}

export function recomputeLive(): Promise<void> {
  if (timer) clearTimeout(timer);
  return computeLive(true);
}

export const useVersions = create<VState>((set, get) => ({
  name: null,
  info: null,
  live: null,
  base: null,
  overlay: null,
  showChanges: false,
  publishOpen: false,
  compare: null,
  error: null,
  engine: null,

  refresh: async (name) => {
    if (get().name !== name) set({ name, info: null, live: null, overlay: null, base: null, compare: null, showChanges: false, engine: null });
    const eng = engineApi.funnel(name)
      .then((engine) => { if (useStudio.getState().name === name) set({ engine }); })
      .catch(() => undefined);   // нет связи с сервером – бейдж бота просто не покажем
    try {
      const info = await versionsApi.list(name);
      if (useStudio.getState().name === name) set({ info, error: null });
    } catch (e) {
      set({ error: (e as Error).message });
    }
    await Promise.all([recomputeLive(), eng]);
  },
  setBase: (n) => {
    set({ base: n, showChanges: true });
    void recomputeLive();
  },
  set: (p) => set(p),
}));

// Дифф пересчитывается сам, когда меняется граф редактора (с задержкой – не на каждый символ).
useStudio.subscribe((s, prev) => {
  if (s.name !== prev.name) {
    if (s.name) void useVersions.getState().refresh(s.name);
    return;
  }
  if (s.funnel !== prev.funnel) scheduleLive();
});

// Ручка для автоматизации: window.__versions.getState()
(window as unknown as { __versions: typeof useVersions }).__versions = useVersions;
