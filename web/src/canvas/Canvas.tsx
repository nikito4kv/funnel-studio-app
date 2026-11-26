// Холст: карточки, полосы, стрелки, заметки. Все правки – через операции (store.apply).
import {
  applyNodeChanges, Background, BackgroundVariant, MiniMap, ReactFlow, useReactFlow,
  type Connection, type Edge as RFEdge, type EdgeChange, type FinalConnectionState, type Node as RFNode, type NodeChange,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { autoLanes } from "../model/lanes";
import * as ops from "../model/ops";
import type { EdgeKind, Funnel, Position, StepType } from "../model/types";
import { validate } from "../model/validate";
import { useSim } from "../sim/simStore";
import { newId, useStudio } from "../store";
import { CanvasOverlays, saveFirstLayout } from "../nav/CanvasOverlays";
import { ZoomClass } from "../nav/lod";
import { attachSmoothZoom } from "../nav/smoothZoom";
import { useSimple, useUi } from "../simple/mode";
import { buildFlow, LANE_PREFIX, NOTE_PREFIX, START } from "./flow";
import { DEFAULT_SIZE, elkPositions, laneAt, laneBands, stackLanes, type Size } from "./layout";
import { addNext } from "./addNext";
import { edgeTypes } from "./StudioEdge";
import { CanvasToolbar } from "./CanvasToolbar";
import { kindOfHandle, type StepNodeData } from "./nodes";
import { allNodeTypes, DiffLegend, useDiffEdges, useDiffNodes } from "../versions/overlay";

const MIN_ZOOM = 0.04;
const MAX_ZOOM = 2;

export const DND_MIME = "application/x-studio-palette";

export interface PaletteItem {
  kind: "step" | "note";
  type?: StepType;
  survey?: boolean;
}

/** Добавить шаг из палитры в точку холста (и положить в полосу под ней). */
export function addFromPalette(item: PaletteItem, pos: Position, sizes: Record<string, Size> = {}): void {
  const st = useStudio.getState();
  const g = st.funnel;
  if (!g) return;
  if (item.kind === "note") {
    const id = newId();
    if (st.apply((f) => ops.addNote(f, { id, text: "Заметка", position: pos, author: "я", createdAt: new Date().toISOString() }))) {
      st.select({ steps: [], edge: null, note: id });
    }
    return;
  }
  const id = newId();
  const lane = laneAt(laneBands(g, g.layout.positions, sizes), pos.y + 40);
  const ok = st.apply((f) => ops.addStep(f, { id, type: item.type!, survey: item.survey, position: pos, laneId: lane?.id ?? null }));
  if (ok) {
    st.select({ steps: [id], edge: null, note: null });
    st.requestTextFocus();
  }
}

function needsLayout(g: Funnel): boolean {
  return g.steps.some((s) => !g.layout.positions[s.id]);
}

export function Canvas() {
  const funnel = useStudio((s) => s.funnel)!;
  const platform = useStudio((s) => s.platform);
  const branch = useStudio((s) => s.branch);
  const selection = useStudio((s) => s.selection);
  const showNotes = useStudio((s) => s.showNotes);
  const centerOn = useStudio((s) => s.centerOn);
  const relayout = useStudio((s) => s.relayout);
  const pendingNote = useStudio((s) => s.pendingNote);
  const simMarks = useSim((s) => s.marks);
  const simple = useSimple();
  const minimap = useUi((s) => s.minimap);
  const { apply, select, setBase, set } = useStudio.getState();
  const rf = useReactFlow();

  const [sizes, setSizes] = useState<Record<string, Size>>({});
  const [ready, setReady] = useState(() => !needsLayout(funnel));
  // Рисовать только видимое – после первого показа (до него все карточки должны отрисоваться,
  // чтобы React Flow их измерил). Масштаб колесом React Flow не трогает (nav/smoothZoom.ts),
  // поэтому карточки, въехавшие в кадр, монтируются один раз – когда движение встало.
  const [visibleOnly, setVisibleOnly] = useState(false);
  const sizesRef = useRef(sizes);
  sizesRef.current = sizes;
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!wrap.current) return;
    return attachSmoothZoom(wrap.current, rf, { minZoom: MIN_ZOOM, maxZoom: MAX_ZOOM, ignore: ".nowheel, .search-box, .nav-pop" });
  }, [rf]);
  const alt = useRef(false);
  useEffect(() => { (window as unknown as { __rf: typeof rf }).__rf = rf; }, [rf]);
  const laidOut = useRef<string | null>(null);
  const dataCache = useRef(new Map<string, StepNodeData>());

  const issues = useMemo(() => validate(funnel), [funnel]);
  const flow = useMemo(
    () => buildFlow({ funnel, platform, branch, issues, selection, sizes, showNotes, simple, dataCache: dataCache.current, sim: simMarks }),
    [funnel, platform, branch, issues, selection, sizes, showNotes, simple, simMarks],
  );
  // Узлы живут локально: перетаскивание двигает только свою карточку (applyNodeChanges),
  // воронка в сторе меняется один раз – на отпускании. Раньше каждое движение мыши
  // пересобирало все карточки – на больших воронках холст дёргался.
  const [nodes, setNodes] = useState<RFNode[]>(flow.nodes);
  useEffect(() => { setNodes(flow.nodes); }, [flow.nodes]);
  // «Показать изменения» (версии): рамки добавленных/изменённых и тени удалённых
  const shownNodes = useDiffNodes(nodes);
  const shownEdges = useDiffEdges(flow.edges);

  // Alt при протягивании стрелки – параллельная (also_fire)
  useEffect(() => {
    const on = (e: KeyboardEvent) => { alt.current = e.altKey; };
    window.addEventListener("keydown", on);
    window.addEventListener("keyup", on);
    return () => { window.removeEventListener("keydown", on); window.removeEventListener("keyup", on); };
  }, []);

  // Первый вид: воронка целиком, только если так текст карточек читается (масштаб ≥ 60 %), иначе –
  // начало воронки в масштабе 80 %: «вписать всё» давало текст в 2–7 px даже на 14 шагах.
  // «Вся воронка» – Shift+1 и кнопкой внизу.
  const openView = useCallback(() => {
    const g = useStudio.getState().funnel;
    const start = rf.getNode(START);
    const el = wrap.current;
    const pos = g?.layout.positions ?? {};
    const boxes = (g?.steps ?? []).filter((s) => pos[s.id]).map((s) => ({ ...pos[s.id], ...(sizesRef.current[s.id] ?? DEFAULT_SIZE) }));
    const fit = el && boxes.length
      ? Math.min(el.clientWidth / (Math.max(...boxes.map((b) => b.x + b.width)) - Math.min(...boxes.map((b) => b.x)) + 200),
        el.clientHeight / (Math.max(...boxes.map((b) => b.y + b.height)) - Math.min(...boxes.map((b) => b.y)) + 120))
      : 1;
    if (!g || fit >= 0.6 || !start) { void rf.fitView({ padding: 0.08, maxZoom: 1 }); return; }
    const z = 0.8;
    void rf.setViewport({ x: 40 - start.position.x * z, y: 90 - start.position.y * z, zoom: z });
  }, [rf]);

  // ── автораскладка: при открытии воронки без координат и по кнопке «разложить заново» ──
  const doLayout = useCallback(async (g: Funnel, withHistory: boolean) => {
    let f = g;
    if (!f.lanes.length && f.steps.length) f = ops.setLanes(f, autoLanes(f, newId));
    const pos = stackLanes(f, await elkPositions(f, sizes), sizes);
    f = ops.setPositions(f, { positions: pos });
    if (withHistory) apply(() => f);
    else {
      setBase(f);
      // первая раскладка импортированной воронки сохраняется сразу – иначе её пересчитывали бы
      // при каждом открытии, а воронка висела бы «несохранённой» без единой правки
      void saveFirstLayout(f);
    }
    setReady(true);
    requestAnimationFrame(() => openView());
  }, [sizes, apply, setBase, rf, openView]);

  useEffect(() => {
    if (!needsLayout(funnel)) {
      if (!ready) setReady(true);
      return;
    }
    const measured = funnel.steps.every((s) => sizes[s.id]);
    const key = funnel.id + ":" + funnel.steps.length;
    if (measured && laidOut.current !== key) {
      laidOut.current = key;
      void doLayout(funnel, false);
    }
  }, [funnel, sizes, ready, doLayout]);

  const lastRelayout = useRef(relayout);
  useEffect(() => {
    if (relayout === lastRelayout.current) return;
    lastRelayout.current = relayout;
    const g = useStudio.getState().funnel;
    if (g) void doLayout({ ...g, layout: { positions: {} } }, true);
  }, [relayout, doLayout]);

  // первый показ уже разложенной воронки
  const fitted = useRef<string | null>(null);
  useEffect(() => {
    const name = useStudio.getState().name;
    if (ready && fitted.current !== name && funnel.steps.every((s) => sizes[s.id])) {
      fitted.current = name;
      const focus = new URLSearchParams(window.location.hash.slice(1)).get("focus");
      const target = focus ? funnel.steps.find((s) => s.key === focus) : undefined;
      requestAnimationFrame(() => {
        if (target) useStudio.getState().focusStep(target.id);
        else openView();
        setVisibleOnly(true);
      });
    }
  }, [ready, funnel, sizes, rf]);

  // фокус на шаг (из проверки, из поиска)
  useEffect(() => {
    if (!centerOn) return;
    const g = useStudio.getState().funnel;
    const p = g?.layout.positions[centerOn.id];
    if (!p) return;
    const s = sizes[centerOn.id] ?? DEFAULT_SIZE;
    rf.setCenter(p.x + s.width / 2 + 180, p.y + Math.min(s.height, 500) / 2, { zoom: 1, duration: 300 });
  }, [centerOn, rf, sizes]);

  // ── изменения от React Flow ──
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((nds) => applyNodeChanges(changes, nds));
    let newSizes: Record<string, Size> | null = null;
    const sel = useStudio.getState().selection;
    let steps = sel.steps;
    let note = sel.note;
    let selChanged = false;
    for (const c of changes) {
      if (c.type === "dimensions" && c.dimensions && !c.id.startsWith(LANE_PREFIX)) {
        newSizes ??= {};
        newSizes[c.id] = { width: c.dimensions.width, height: c.dimensions.height };
      } else if (c.type === "select") {
        selChanged = true;
        if (c.id.startsWith(NOTE_PREFIX)) {
          const id = c.id.slice(NOTE_PREFIX.length);
          if (c.selected) note = id;
          else if (note === id) note = null;
        } else if (!c.id.startsWith(LANE_PREFIX)) {
          steps = c.selected ? (steps.includes(c.id) ? steps : [...steps, c.id]) : steps.filter((x) => x !== c.id);
        }
      }
    }
    if (newSizes) {
      const ns = newSizes;
      setSizes((s) => {
        const changed = Object.entries(ns).some(([k, v]) => s[k]?.width !== v.width || s[k]?.height !== v.height);
        return changed ? { ...s, ...ns } : s;
      });
    }
    if (selChanged) select({ steps, note, edge: steps.length || note ? null : sel.edge });
  }, [select]);

  const onNodeDragStop = useCallback((_e: unknown, _n: RFNode, moved: RFNode[]) => {
    const g = useStudio.getState().funnel;
    if (!g) return;
    apply((f) => {
      let out = f;
      const positions: Record<string, Position> = {};
      for (const n of moved) {
        if (n.id.startsWith(NOTE_PREFIX)) {
          out = ops.updateNote(out, { noteId: n.id.slice(NOTE_PREFIX.length), patch: { position: n.position } });
        } else if (f.steps.some((s) => s.id === n.id)) {
          positions[n.id] = n.position;
        }
      }
      out = ops.setPositions(out, { positions });
      // карточка принадлежит полосе, куда её положили
      const bands = laneBands(out, out.layout.positions, sizes);
      if (bands.length) {
        for (const id of Object.keys(positions)) {
          const h = (sizes[id] ?? DEFAULT_SIZE).height;
          const lane = laneAt(bands, positions[id].y + Math.min(h, 300) / 2);
          const cur = out.lanes.find((l) => l.step_ids.includes(id)) ?? null;
          if (lane && lane.id !== cur?.id) out = ops.assignLane(out, { stepIds: [id], laneId: lane.id });
        }
      }
      return out;
    });
  }, [apply, sizes]);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    for (const c of changes) {
      if (c.type === "select" && c.selected && !c.id.startsWith("start-")) {
        select({ edge: c.id, steps: [], note: null });
      }
    }
  }, [select]);

  const onConnect = useCallback((c: Connection) => {
    if (!c.source || !c.target || c.target.startsWith("funnel:") || c.target.startsWith(NOTE_PREFIX)) return;
    let kind: EdgeKind = kindOfHandle(c.sourceHandle);
    if (kind === "done" && alt.current) kind = "also_fire";
    const id = newId();
    if (apply((f) => ops.addEdge(f, { id, from: c.source, kind, to: c.target }))) {
      select({ edge: id, steps: [], note: null });
    }
  }, [apply, select]);

  // стрелку отпустили в пустом месте – там появится новая карточка, уже связанная (как в Miro)
  const onConnectEnd = useCallback((e: MouseEvent | TouchEvent, st: FinalConnectionState) => {
    if (st.isValid || st.toNode || !st.fromNode || st.fromHandle?.type !== "source") return;
    const from = st.fromNode.id;
    if (!useStudio.getState().funnel?.steps.some((s) => s.id === from)) return;
    const pt = "changedTouches" in e ? e.changedTouches[0] : e;
    const p = rf.screenToFlowPosition({ x: pt.clientX, y: pt.clientY });
    let kind: EdgeKind = kindOfHandle(st.fromHandle.id);
    if (kind === "done" && alt.current) kind = "also_fire";
    addNext(from, { at: { x: p.x - 20, y: p.y - 40 }, kind, sizes });
  }, [rf, sizes]);

  const onReconnect = useCallback((old: RFEdge, c: Connection) => {
    if (!c.target || c.target === old.target) return;
    apply((f) => ops.retargetEdge(f, { edgeId: old.id, to: c.target }));
  }, [apply]);

  const onPaneClick = useCallback((e: React.MouseEvent) => {
    const pn = useStudio.getState().pendingNote;
    if (pn) {
      const pos = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      const id = newId();
      const ok = apply((f) => (pn === "revision"
        ? ops.addRevision(f, { id, text: "Что поправить", action: "other", position: pos, author: "я", createdAt: new Date().toISOString() })
        : ops.addNote(f, { id, text: "Заметка", position: pos, author: "я", createdAt: new Date().toISOString() })));
      set({ pendingNote: null });
      if (ok) select({ steps: [], edge: null, note: id });
      return;
    }
    select({ steps: [], edge: null, note: null });
  }, [apply, rf, select, set]);

  const onDrop = useCallback((e: React.DragEvent) => {
    const raw = e.dataTransfer.getData(DND_MIME);
    if (!raw) return;
    e.preventDefault();
    const pos = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    addFromPalette(JSON.parse(raw) as PaletteItem, { x: pos.x - 160, y: pos.y - 40 }, sizes);
  }, [rf, sizes]);

  return (
    <div ref={wrap} className={"canvas" + (pendingNote ? " canvas-pending" : "")} data-zoom="near" data-lod="off" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
      <ReactFlow
        nodes={shownNodes}
        edges={shownEdges}
        onlyRenderVisibleElements={visibleOnly}
        nodeTypes={allNodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeDragStop={onNodeDragStop}
        onConnect={onConnect}
        onReconnect={onReconnect}
        onConnectEnd={onConnectEnd}
        connectionRadius={28}
        onPaneClick={onPaneClick}
        onNodeDoubleClick={(_e, n) => {
          if (!n.id.startsWith(NOTE_PREFIX) && !n.id.startsWith(LANE_PREFIX)) useStudio.getState().requestTextFocus();
        }}
        deleteKeyCode={null}
        selectionKeyCode="Shift"
        multiSelectionKeyCode={["Control", "Meta"]}
        selectionOnDrag={false}
        elevateNodesOnSelect={false}
        minZoom={MIN_ZOOM}
        maxZoom={MAX_ZOOM}
        zoomOnScroll={false}
        zoomOnDoubleClick={false}
        style={{ opacity: ready ? 1 : 0 }}
        proOptions={{ hideAttribution: true }}
      >
        <ZoomClass />
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.4} color="var(--canvas-dot)" />
        <CanvasToolbar />
        {minimap && <MiniMap
          pannable
          zoomable
          position="bottom-right"
          className="minimap nowheel"
          ariaLabel="Мини-карта воронки"
          nodeColor={(n) => (n.type === "lane" ? "rgba(29,29,27,0.04)" : n.type === "circle" ? "#cfeadb" : n.type === "start" ? "#1d1d1b" : n.type === "note" ? "#fff3b0" : "#d9ded9")}
          nodeBorderRadius={4}
          maskColor="rgba(241, 241, 239, 0.72)"
        />}
      </ReactFlow>
      {!ready && <div className="canvas-loading">Раскладываю карточки…</div>}
      <DiffLegend />
      <CanvasOverlays />
      {pendingNote && <div className="canvas-hint">Кликните на холст – туда ляжет {pendingNote === "revision" ? "правка" : "заметка"} (Esc – отмена)</div>}
    </div>
  );
}
