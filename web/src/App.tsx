// Экран Студии: шапка · палитра | холст | панель свойств/проверки · строка состояния.
import { ReactFlowProvider } from "@xyflow/react";
import { CircleAlert, History, Play, SlidersHorizontal, TriangleAlert, Workflow } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, type FunnelListItem } from "./api";
import { Canvas } from "./canvas/Canvas";
import * as ops from "./model/ops";
import { HistoryPanel } from "./panels/HistoryPanel";
import { EdgePanel, FunnelPanel, NotePanel, RemoveDialog } from "./panels/OtherPanels";
import { SimulatorPanel } from "./panels/SimulatorPanel";
import { StepPanel } from "./panels/StepPanel";
import { isDirty, newId, useStudio } from "./store";
import { Header, Palette, StatusBar, Toasts } from "./ui/Chrome";
import { ExportDialog, ImportDialog, ValidationPanel } from "./ui/Dialogs";
import { MiroImportHost } from "./miro/MiroImport";
import { CanvasKeys, Shortcuts } from "./ui/Shortcuts";
import { TooltipLayer } from "./design/Tooltip";
import { issueCounts, validate } from "./model/validate";
import { VersionsLayer } from "./versions/PublishDialog";
import { useVersions } from "./versions/store";
import { useSim } from "./sim/simStore";
import { useUi } from "./simple/mode";
import { Tour } from "./simple/Tour";
import { BotsLayer } from "./bots/BotsScreen";
import { useBots } from "./bots/store";

function isTyping(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable;
}

function RightPanel() {
  const funnel = useStudio((s) => s.funnel)!;
  const selection = useStudio((s) => s.selection);
  const tab = useStudio((s) => s.rightTab);
  const set = useStudio((s) => s.set);
  let body: React.ReactNode;
  if (tab === "check") body = <ValidationPanel funnel={funnel} />;
  else if (tab === "history") body = <HistoryPanel />;
  else if (tab === "sim") body = <SimulatorPanel funnel={funnel} />;
  else if (selection.edge) {
    const e = funnel.edges.find((x) => x.id === selection.edge);
    body = e ? <EdgePanel edge={e} funnel={funnel} /> : <FunnelPanel funnel={funnel} />;
  } else if (selection.note) {
    const n = funnel.notes.find((x) => x.id === selection.note);
    body = n ? <NotePanel note={n} funnel={funnel} /> : <FunnelPanel funnel={funnel} />;
  } else if (selection.steps.length === 1) {
    const s = funnel.steps.find((x) => x.id === selection.steps[0]);
    body = s ? <StepPanel key={s.id} step={s} funnel={funnel} /> : <FunnelPanel funnel={funnel} />;
  } else if (selection.steps.length > 1) {
    body = (
      <div className="panel-body">
        <div className="panel-title"><span>Выделено карточек: {selection.steps.length}</span></div>
        <div className="empty-state">
          <p>Перетащите – сдвинутся вместе. <kbd>Delete</kbd> – удалить, <kbd>Ctrl+C</kbd> <kbd>Ctrl+V</kbd> – копия.</p>
        </div>
      </div>
    );
  } else body = <FunnelPanel funnel={funnel} />;
  const issues = useMemo(() => validate(funnel), [funnel]);
  const { errors, warns } = issueCounts(issues);
  const tabBtn = (id: typeof tab, Icon: typeof Play, label: string, testId?: string, extra?: React.ReactNode) => (
    <button type="button" role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""} onClick={() => set({ rightTab: id })} data-testid={testId}>
      {id === "check" && <Icon size={14} strokeWidth={2} aria-hidden className={errors ? "ic-err" : warns ? "ic-warn" : "ic-ok"} />}{label}{extra}
    </button>
  );
  return (
    <aside className="right">
      <div className="right-tabs" role="tablist">
        {tabBtn("props", SlidersHorizontal, "Свойства")}
        {tabBtn("check", errors ? CircleAlert : TriangleAlert, "Проверка", "tab-check",
          errors + warns ? <span className={"count-badge" + (errors ? " err" : " warn")}>{errors + warns}</span> : null)}
        {tabBtn("sim", Play, "Симулятор", "tab-sim")}
        {tabBtn("history", History, "История", "history-tab")}
      </div>
      <div className="right-scroll">{body}</div>
    </aside>
  );
}

export default function App() {
  const funnel = useStudio((s) => s.funnel);
  const name = useStudio((s) => s.name);
  const exportOpen = useStudio((s) => s.exportOpen);
  const importOpen = useStudio((s) => s.importOpen);
  const removeDialog = useStudio((s) => s.removeDialog);
  const uiMode = useUi((s) => s.mode);
  const { load, set, toast } = useStudio.getState();
  const [list, setList] = useState<FunnelListItem[]>([]);
  const [saving, setSaving] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  const refresh = useCallback(() => api.funnels().then(setList).catch((e) => setLoadErr(e.message)), []);

  const open = useCallback(async (n: string) => {
    if (isDirty(useStudio.getState()) && !window.confirm("Есть несохранённые правки – открыть другую воронку без сохранения?")) return;
    try {
      const f = await api.get(n);
      load(n, f);
      set({ importOpen: false });
      const hash = new URLSearchParams(window.location.hash.slice(1));
      hash.set("f", n);
      history.replaceState(null, "", "#" + hash.toString());
      void refresh();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }, [load, set, toast, refresh]);

  useEffect(() => {
    void refresh().then(() => {
      const n = new URLSearchParams(window.location.hash.slice(1)).get("f");
      if (n) void open(n);
    });
  }, [refresh, open]);

  const save = useCallback(async (auto = false) => {
    const st = useStudio.getState();
    if (!st.funnel || !st.name) return;
    setSaving(true);
    const snapshot = st.funnel;
    try {
      await api.save(st.name, snapshot);
      if (useStudio.getState().funnel === snapshot) useStudio.getState().markSaved();
      // автосохранение – молча: статус в шапке и так говорит «Черновик сохранён»
      if (!auto) toast(useVersions.getState().engine?.configured
        ? "Черновик сохранён. В бот он не ушёл – для бота «Запустить в боте…»"
        : "Черновик сохранён", "ok");
      void refresh();
    } catch (e) {
      const err = e as ApiError;
      toast(err.message, "error");
      const sid = err.errors?.find((x) => x.step_id)?.step_id;
      if (sid && !auto) useStudio.getState().focusStep(sid);
    } finally {
      setSaving(false);
    }
  }, [toast, refresh]);

  // Автосохранение черновика: через 3 с тишины после правки. В бот черновик не уходит –
  // для этого «Запустить в боте». Ошибка сохранения – тостом, как у кнопки.
  const draft = useStudio((s) => s.funnel);
  const savedRef = useStudio((s) => s.saved);
  useEffect(() => {
    if (!draft || draft === savedRef) return;
    const t = window.setTimeout(() => { if (isDirty(useStudio.getState())) void save(true); }, 3000);
    return () => window.clearTimeout(t);
  }, [draft, savedRef, save]);

  // клавиатура: undo/redo, удалить, копировать/вставить, сохранить
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const st = useStudio.getState();
      const mod = e.ctrlKey || e.metaKey;
      const k = e.key.toLowerCase();
      if (mod && (k === "s" || k === "ы")) { e.preventDefault(); void save(); return; }
      // Esc закрывает открытое окно (выгрузка, импорт, удаление, запуск, все ветки, превью)
      if (e.key === "Escape" && document.querySelector(".modal-back")) {
        const v = useVersions.getState();
        const sim = useSim.getState();
        if (st.exportOpen || st.importOpen || st.removeDialog) st.set({ exportOpen: false, importOpen: false, removeDialog: null });
        else if (v.publishOpen) v.set({ publishOpen: false });
        else if (sim.matrixOpen) sim.closeMatrix();
        else document.querySelector<HTMLButtonElement>(".modal-back .modal-head .x")?.click();
        e.preventDefault();
        return;
      }
      if (isTyping(e.target)) return;
      if (!st.funnel) return;
      if (mod && (k === "z" || k === "я")) { e.preventDefault(); if (e.shiftKey) st.redo(); else st.undo(); return; }
      if (mod && (k === "y" || k === "н")) { e.preventDefault(); st.redo(); return; }
      if (e.key === "Escape") { st.set({ pendingNote: null }); st.select({ steps: [], edge: null, note: null }); return; }
      if (e.key === "Delete" || e.key === "Backspace") {
        const sel = st.selection;
        if (sel.edge) { if (st.apply((g) => ops.removeEdge(g, { edgeId: sel.edge! }))) st.select({ edge: null }); return; }
        if (sel.note) { if (st.apply((g) => ops.removeNote(g, { noteId: sel.note! }))) st.select({ note: null }); return; }
        if (sel.steps.length) {
          const g = st.funnel;
          const free = sel.steps.filter((id) => !ops.removeBlockers(g, id).length);
          const blocked = sel.steps.filter((id) => ops.removeBlockers(g, id).length);
          if (free.length) st.apply((f) => free.reduce((acc, id) => (acc.steps.some((s) => s.id === id) && !ops.removeBlockers(acc, id).length ? ops.removeStep(acc, { stepId: id }) : acc), f));
          if (blocked.length) st.set({ removeDialog: blocked[0] });
          st.select({ steps: blocked.slice(0, 1) });
        }
        return;
      }
      if (mod && (k === "c" || k === "с") && st.selection.steps.length) {
        st.set({ clipboard: st.selection.steps });
        st.toast(`Скопировано карточек: ${st.selection.steps.length}`, "info");
        return;
      }
      if (mod && (k === "v" || k === "м") && st.clipboard.length) {
        const map = new Map<string, string>();
        const ok = st.apply((g) => ops.duplicateSteps(g, {
          stepIds: st.clipboard.filter((id) => g.steps.some((s) => s.id === id)),
          newId: (old) => { const v = newId(); map.set(old, v); return v; },
          offset: { x: 60, y: 60 },
        }));
        if (ok) st.select({ steps: st.clipboard.map((id) => map.get(id)).filter((x): x is string => !!x), edge: null, note: null });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (isDirty(useStudio.getState())) e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  const labels = list.map((f) => ({ name: f.name, label: `${f.launch} / ${f.name.split("__")[1]} (${f.steps})` }));

  return (
    <ReactFlowProvider>
      <div className={`app mode-${uiMode}`}>
        <Header funnels={labels} onOpen={open} onSave={() => void save()} onExport={() => set({ exportOpen: true })} saving={saving} />
        <div className="main">
          <Palette />
          {funnel ? <Canvas key={name ?? ""} /> : (
            <div className="empty">
              {loadErr ? <div className="err-box">{loadErr}</div> : (
                <div className="empty-state start-screen">
                  <Workflow size={36} strokeWidth={1.5} />
                  <b>Откройте воронку</b>
                  <p>Выберите её в списке сверху – или создайте новую, или перенесите готовую из bot-engine.</p>
                  <button type="button" className="primary" onClick={() => set({ importOpen: true })}>Новая воронка или импорт</button>
                  <button type="button" className="ghost" onClick={() => useBots.getState().show()} data-testid="start-bots">Боты</button>
                </div>
              )}
            </div>
          )}
          {funnel && <RightPanel />}
        </div>
        <StatusBar />
        {exportOpen && funnel && <ExportDialog funnel={funnel} onClose={() => set({ exportOpen: false })} />}
        {importOpen && <ImportDialog onOpen={open} onClose={() => set({ importOpen: false })} />}
        <MiroImportHost onOpen={open} />
        {removeDialog && funnel && <RemoveDialog stepId={removeDialog} funnel={funnel} />}
        <VersionsLayer />
        <BotsLayer onOpenFunnel={(n) => void open(n)} />
        <Toasts />
        <Shortcuts />
        <CanvasKeys />
        <TooltipLayer />
        {funnel && <Tour />}
      </div>
    </ReactFlowProvider>
  );
}
