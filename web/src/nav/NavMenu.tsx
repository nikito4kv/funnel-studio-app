// Панель холста: «Перейти к…» (полосы воронки) и поиск.
import { useReactFlow } from "@xyflow/react";
import { ChevronDown, Map as MapIcon, Navigation, Play, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { LANE_PREFIX, START } from "../canvas/flow";
import type { LaneNodeData } from "../canvas/nodes";
import { useUi } from "../simple/mode";
import { useStudio } from "../store";

/** Показать полосу: целиком, если влезает не мельче 0.3, иначе – её начало в масштабе 0.3. */
export function goToBox(rf: ReturnType<typeof useReactFlow>, box: { x: number; y: number; width: number; height: number }): void {
  const el = document.querySelector(".canvas") as HTMLElement | null;
  const w = el?.clientWidth ?? 1000;
  const h = el?.clientHeight ?? 800;
  const fit = Math.min(w / box.width, h / box.height) * 0.94;
  const z = Math.min(1, Math.max(fit, 0.3));
  let x: number;
  let y: number;
  if (z === fit || box.width * z <= w) x = (w - box.width * z) / 2 - box.x * z;
  else x = 40 - box.x * z;
  if (box.height * z <= h) y = (h - box.height * z) / 2 - box.y * z;
  else y = 30 - box.y * z;
  void rf.setViewport({ x, y, zoom: z }, { duration: 350 });
}

export function NavMenu() {
  const rf = useReactFlow();
  const funnel = useStudio((s) => s.funnel);
  const setSearch = useUi((s) => s.setSearch);
  const simplifyFar = useUi((s) => s.simplifyFar);
  const setSimplifyFar = useUi((s) => s.setSimplifyFar);
  const minimap = useUi((s) => s.minimap);
  const setMinimap = useUi((s) => s.setMinimap);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", esc); };
  }, [open]);
  if (!funnel) return null;
  const pos = funnel.layout.positions;
  const lanes = [...funnel.lanes].sort((a, b) => (pos[a.id]?.y ?? a.order * 1e5) - (pos[b.id]?.y ?? b.order * 1e5));
  const goLane = (id: string) => {
    const n = rf.getNode(LANE_PREFIX + id);
    if (!n) return;
    const d = n.data as LaneNodeData;
    goToBox(rf, { x: n.position.x, y: n.position.y, width: d.width, height: d.height });
    setOpen(false);
  };
  const goStart = () => {
    const n = rf.getNode(START);
    if (n) goToBox(rf, { x: n.position.x - 40, y: n.position.y - 60, width: 1800, height: 900 });
    setOpen(false);
  };
  return (
    <div className="nav-menu tb-group" ref={ref}>
      <button type="button" className="ghost" data-testid="goto" onClick={() => setOpen(!open)} data-tip="Перейти к полосе воронки" aria-expanded={open}>
        <Navigation size={15} strokeWidth={1.75} />Перейти<ChevronDown size={14} strokeWidth={2} />
      </button>
      <button type="button" className="ghost icon" data-testid="search-button" onClick={() => setSearch(true)} data-tip="Найти шаг по тексту" data-kbd="Ctrl+F" aria-label="Поиск">
        <Search size={16} strokeWidth={1.75} />
      </button>
      {open && (
        <div className="pop nav-pop right" data-testid="goto-list">
          <button type="button" onClick={() => { void rf.fitView({ padding: 0.06, duration: 350 }); setOpen(false); }}>
            <MapIcon size={16} strokeWidth={1.75} /><span className="grow">Вся воронка</span><kbd>Shift+1</kbd>
          </button>
          {rf.getNode(START) && <button type="button" onClick={goStart}><Play size={16} strokeWidth={1.75} /><span className="grow">Вход в воронку</span></button>}
          {lanes.length > 0 && <div className="pop-title">Полосы</div>}
          {lanes.map((l) => (
            <button type="button" key={l.id} onClick={() => goLane(l.id)} disabled={!pos[l.id]}>
              <span className="grow">{l.title}</span>
              <span className="menu-meta">{l.step_ids.length}</span>
            </button>
          ))}
          {!lanes.length && <div className="pop-empty">Полос нет – их можно завести в панели воронки (клик по пустому месту холста).</div>}
          <div className="pop-sep" />
          <label className="nav-check">
            <input type="checkbox" data-testid="minimap-toggle" checked={minimap} onChange={(e) => setMinimap(e.target.checked)} />
            Мини-карта в углу
          </label>
          <label className="nav-check" data-tip="Издалека показывать вместо карточки значок, название и время">
            <input type="checkbox" data-testid="simplify-far" checked={simplifyFar} onChange={(e) => setSimplifyFar(e.target.checked)} />
            Упрощать карточки при отдалении
          </label>
        </div>
      )}
    </div>
  );
}
