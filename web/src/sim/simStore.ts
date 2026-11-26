// Состояние симулятора – отдельно от редактора: прогон не меняет граф и не пишется в историю.
// Отметки для холста (`marks`) – объекты стабильны, пока прогон не пересчитан: карточки без
// изменений не перерисовываются (см. sameData в canvas/flow.ts).
import { create } from "zustand";
import type { Funnel } from "../model/types";
import { useStudio } from "../store";
import { nowMsk } from "./format";
import { TYPICAL, type MatrixResult, type Persona, type SimMark, type SimResult, type TestRunStatus } from "./types";

async function post<T>(url: string, body: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch {
    throw new Error("Сервер Студии не отвечает");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.message ?? `HTTP ${r.status}`);
  return data as T;
}

export type DndMode = "launch" | "on" | "off";

interface SimState {
  registeredAt: string;
  platform: "tg" | "max";
  persona: Persona;
  dnd: DndMode;
  result: SimResult | null;
  matrix: MatrixResult | null;
  matrixOpen: boolean;
  loading: boolean;
  matrixLoading: boolean;
  error: string | null;
  marks: Map<string, SimMark>;
  testrun: TestRunStatus | null;
  testrunBusy: boolean;

  setParams: (p: Partial<Pick<SimState, "registeredAt" | "platform" | "dnd">>) => void;
  setPersona: (p: Partial<Persona>) => void;
  resetPersona: () => void;
  run: () => Promise<void>;
  runMatrix: () => Promise<void>;
  closeMatrix: () => void;
  clear: () => void;
  loadTestrun: () => Promise<void>;
  sendTestrun: () => Promise<string>;
}

function dndValue(m: DndMode): boolean | null {
  return m === "launch" ? null : m === "on";
}

function currentGraph(): { name: string; graph: Funnel } {
  const st = useStudio.getState();
  if (!st.funnel || !st.name) throw new Error("воронка не открыта");
  return { name: st.name, graph: st.funnel };
}

export const useSim = create<SimState>((set, get) => ({
  registeredAt: nowMsk(),
  platform: "tg",
  persona: { ...TYPICAL },
  dnd: "launch",
  result: null,
  matrix: null,
  matrixOpen: false,
  loading: false,
  matrixLoading: false,
  error: null,
  marks: new Map(),
  testrun: null,
  testrunBusy: false,

  setParams: (p) => set(p),
  setPersona: (p) => set((s) => ({ persona: { ...s.persona, ...p } })),
  resetPersona: () => set({ persona: { ...TYPICAL } }),

  run: async () => {
    const { registeredAt, platform, persona, dnd } = get();
    set({ loading: true, error: null });
    try {
      const { name, graph } = currentGraph();
      const res = await post<SimResult>(`api/funnels/${encodeURIComponent(name)}/simulate`, {
        graph, registered_at: registeredAt, platform, persona, dnd: dndValue(dnd),
      });
      set({ result: res, marks: new Map(Object.entries(res.marks)), loading: false });
      // фишки времени на карточках – в часах той ветки, в которую попал человек
      if (res.branch?.id && useStudio.getState().branch !== res.branch.id) useStudio.getState().set({ branch: res.branch.id });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  runMatrix: async () => {
    const { registeredAt, platform, persona, dnd } = get();
    set({ matrixLoading: true, matrixOpen: true, error: null });
    try {
      const { name, graph } = currentGraph();
      const res = await post<MatrixResult>(`api/funnels/${encodeURIComponent(name)}/simulate/matrix`, {
        graph, date: registeredAt.slice(0, 10), platform, persona, dnd: dndValue(dnd),
      });
      set({ matrix: res, matrixLoading: false });
    } catch (e) {
      set({ error: (e as Error).message, matrixLoading: false, matrixOpen: false });
    }
  },

  closeMatrix: () => set({ matrixOpen: false }),
  clear: () => set({ result: null, marks: new Map(), error: null }),

  loadTestrun: async () => {
    try {
      const r = await fetch("api/testrun");
      set({ testrun: (await r.json()) as TestRunStatus });
    } catch {
      set({ testrun: { ready: false, proxy: false, hint: "сервер Студии не отвечает" } });
    }
  },

  sendTestrun: async () => {
    const { registeredAt, persona } = get();
    set({ testrunBusy: true });
    try {
      const { name, graph } = currentGraph();
      const r = await post<{ sent: number; errors: { key: string; error: string }[] }>(
        `api/funnels/${encodeURIComponent(name)}/testrun`, { graph, registered_at: registeredAt, persona });
      return r.errors.length ? `Отправлено ${r.sent}, с ошибками: ${r.errors.length}` : `Отправлено сообщений: ${r.sent}`;
    } finally {
      set({ testrunBusy: false });
    }
  },
}));

// Открыли другую воронку – прогон прошлой больше не про неё.
useStudio.subscribe((s, prev) => {
  if (s.name !== prev.name) useSim.getState().clear();
});

// Ручка для автоматизации (e2e, замер плавности, скриншоты): window.__sim.getState()
if (typeof window !== "undefined") (window as unknown as { __sim: typeof useSim }).__sim = useSim;
