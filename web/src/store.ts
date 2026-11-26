// Состояние редактора: граф + история undo/redo + выделение и режимы экрана.
// Граф меняется только через apply(операция) – операции из model/ops.ts.
import { create } from "zustand";
import { OpError } from "./model/ops";
import type { Funnel, Platform } from "./model/types";

const HISTORY_LIMIT = 200;
const COALESCE_MS = 1500;

export interface Selection {
  steps: string[];
  edge: string | null;
  note: string | null;
}

export interface Toast {
  id: number;
  text: string;
  kind: "error" | "info" | "ok";
}

export type RightTab = "props" | "check" | "sim" | "history";

interface State {
  name: string | null;
  funnel: Funnel | null;
  saved: Funnel | null;
  past: Funnel[];
  future: Funnel[];
  lastCoalesce: { key: string; at: number } | null;

  selection: Selection;
  platform: Platform;
  branch: string | null;
  showNotes: boolean;
  rightTab: RightTab;
  focusText: boolean;         // «двойной клик / новая карточка – курсор в текст панели» (гасит редактор)
  centerOn: { id: string; n: number } | null;
  clipboard: string[];
  toasts: Toast[];
  exportOpen: boolean;
  importOpen: boolean;
  pendingNote: "note" | "revision" | null;   // «кликните на холст, куда положить заметку»
  relayout: number;            // запрос «разложить заново» (холст слушает счётчик)
  removeDialog: string | null; // шаг, который нельзя удалить насовсем – спросить, куда перенаправить

  load: (name: string, f: Funnel) => void;
  setBase: (f: Funnel) => void;
  markSaved: () => void;
  apply: (fn: (g: Funnel) => Funnel, opts?: { coalesce?: string }) => boolean;
  undo: () => void;
  redo: () => void;
  select: (s: Partial<Selection>) => void;
  set: (p: Partial<Pick<State, "platform" | "branch" | "showNotes" | "rightTab" | "exportOpen" | "importOpen" | "pendingNote" | "clipboard" | "removeDialog">>) => void;
  focusStep: (id: string) => void;
  requestTextFocus: () => void;
  textFocused: () => void;
  requestRelayout: () => void;
  toast: (text: string, kind?: Toast["kind"]) => void;
  dropToast: (id: number) => void;
}

let toastId = 0;

/** Правка «сделана» ставится сама, когда связанную карточку изменили (исследование, §5.4). */
function autoDoneRevisions(prev: Funnel, next: Funnel): Funnel {
  const open = next.notes.filter((n) => n.kind === "revision" && n.status === "open" && n.step_id);
  if (!open.length) return next;
  const before = new Map(prev.steps.map((s) => [s.id, s]));
  const changed = new Set(next.steps.filter((s) => before.has(s.id) && before.get(s.id) !== s).map((s) => s.id));
  if (!changed.size) return next;
  return {
    ...next,
    notes: next.notes.map((n) => (n.kind === "revision" && n.status === "open" && n.step_id && changed.has(n.step_id)
      ? { ...n, status: "done" as const } : n)),
  };
}

export const useStudio = create<State>((set, get) => ({
  name: null,
  funnel: null,
  saved: null,
  past: [],
  future: [],
  lastCoalesce: null,
  selection: { steps: [], edge: null, note: null },
  platform: "tg",
  branch: null,
  showNotes: true,
  rightTab: "props",
  focusText: false,
  centerOn: null,
  clipboard: [],
  toasts: [],
  exportOpen: false,
  importOpen: false,
  pendingNote: null,
  relayout: 0,
  removeDialog: null,

  load: (name, f) => set({
    name, funnel: f, saved: f, past: [], future: [], lastCoalesce: null,
    selection: { steps: [], edge: null, note: null },
    branch: f.branch_router?.branches[0]?.id ?? null,
  }),

  // без записи в историю: автораскладка при открытии и т.п.
  setBase: (f) => set({ funnel: f }),

  markSaved: () => set((s) => ({ saved: s.funnel })),

  apply: (fn, opts) => {
    const { funnel, past, lastCoalesce } = get();
    if (!funnel) return false;
    let next: Funnel;
    try {
      next = fn(funnel);
    } catch (e) {
      if (e instanceof OpError) {
        get().toast(e.message, "error");
        return false;
      }
      throw e;
    }
    if (next === funnel) return true;
    next = autoDoneRevisions(funnel, next);
    const now = Date.now();
    const merge = opts?.coalesce && lastCoalesce && lastCoalesce.key === opts.coalesce && now - lastCoalesce.at < COALESCE_MS;
    set({
      funnel: next,
      past: merge ? past : [...past, funnel].slice(-HISTORY_LIMIT),
      future: [],
      lastCoalesce: opts?.coalesce ? { key: opts.coalesce, at: now } : null,
    });
    return true;
  },

  undo: () => {
    const { past, funnel, future } = get();
    if (!past.length || !funnel) return;
    set({ funnel: past[past.length - 1], past: past.slice(0, -1), future: [funnel, ...future], lastCoalesce: null });
  },

  redo: () => {
    const { past, funnel, future } = get();
    if (!future.length || !funnel) return;
    set({ funnel: future[0], future: future.slice(1), past: [...past, funnel], lastCoalesce: null });
  },

  select: (s) => set((st) => ({ selection: { ...st.selection, ...s } })),
  set: (p) => set(p),
  focusStep: (id) => set((s) => ({
    selection: { steps: [id], edge: null, note: null },
    centerOn: { id, n: (s.centerOn?.n ?? 0) + 1 },
  })),
  requestTextFocus: () => set({ focusText: true, rightTab: "props" }),
  textFocused: () => set({ focusText: false }),
  requestRelayout: () => set((s) => ({ relayout: s.relayout + 1 })),
  toast: (text, kind = "info") => {
    const id = ++toastId;
    set((s) => ({ toasts: [...s.toasts, { id, text, kind }] }));
    setTimeout(() => get().dropToast(id), kind === "error" ? 7000 : 3500);
  },
  dropToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

export const newId = (): string => crypto.randomUUID();

export function isDirty(s: { funnel: Funnel | null; saved: Funnel | null }): boolean {
  return !!s.funnel && s.funnel !== s.saved;
}
