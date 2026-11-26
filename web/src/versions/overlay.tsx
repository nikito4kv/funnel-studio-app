// Режим «Показать изменения» на холсте: добавленные карточки – зелёная рамка, изменённые –
// жёлтая, удалённые – бледная «тень» на старом месте. Узлы холста не пересобираются: к узлу
// добавляется только className, и объект узла кэшируется – перетаскивание остаётся быстрым.
import type { Edge as RFEdge, Node, Node as RFNode, NodeProps } from "@xyflow/react";
import { memo, useMemo, useRef } from "react";
import { nodeTypes } from "../canvas/nodes";
import { STEP_TYPE_LABEL } from "../model/ops";
import type { StepType } from "../model/types";
import type { Ghost, StepMark } from "./api";
import { useVersions } from "./store";

export const GHOST_PREFIX = "ghost:";

interface GhostData extends Record<string, unknown> {
  ghost: Ghost;
}

const GhostNode = memo(function GhostNode({ data }: NodeProps<Node<GhostData>>) {
  const g = data.ghost;
  return (
    <div className="ghost-card" title="шаг удалён – так он выглядел в сравниваемой версии">
      <div className="ghost-head">
        <span>{STEP_TYPE_LABEL[g.type as StepType] ?? g.type}</span>
        <span className="ghost-tag">удалён</span>
      </div>
      <div className="ghost-key">{g.title ? <><b>{g.title}</b> · </> : null}{g.key}</div>
      {g.text && <div className="ghost-text">{g.text}</div>}
    </div>
  );
});

/** Типы узлов холста + «тень» удалённого шага. Объект стабильный (React Flow требует). */
export const allNodeTypes = { ...nodeTypes, ghost: GhostNode };

const MARK_CLASS: Record<StepMark, string> = {
  added: "diff-added", changed: "diff-changed", renamed: "diff-changed", tombstoned: "diff-changed", removed: "",
};

export function useDiffNodes(nodes: RFNode[]): RFNode[] {
  const show = useVersions((s) => s.showChanges);
  const overlay = useVersions((s) => s.overlay);
  const cache = useRef<{ key: unknown; map: WeakMap<RFNode, RFNode> }>({ key: null, map: new WeakMap() });
  const ghosts = useMemo<RFNode[]>(() => (show && overlay
    ? overlay.ghosts.filter((g) => g.position).map((g) => ({
      id: GHOST_PREFIX + g.id, type: "ghost", position: g.position!, data: { ghost: g } satisfies GhostData,
      draggable: false, selectable: false, connectable: false, focusable: false, zIndex: -1,
    }))
    : []), [show, overlay]);
  if (!show || !overlay) return nodes;
  if (cache.current.key !== overlay) cache.current = { key: overlay, map: new WeakMap() };
  const map = cache.current.map;
  const out = nodes.map((n) => {
    const mark = overlay.steps[n.id];
    if (!mark) return n;
    let m = map.get(n);
    if (!m) {
      m = { ...n, className: [n.className, MARK_CLASS[mark]].filter(Boolean).join(" ") };
      map.set(n, m);
    }
    return m;
  });
  return ghosts.length ? [...ghosts, ...out] : out;
}

export function useDiffEdges(edges: RFEdge[]): RFEdge[] {
  const show = useVersions((s) => s.showChanges);
  const overlay = useVersions((s) => s.overlay);
  return useMemo(() => {
    if (!show || !overlay) return edges;
    const added = new Set(overlay.changes.filter((c) => c.kind === "edge_added").map((c) => c.edge_id));
    const changed = new Set(overlay.changes
      .filter((c) => c.kind === "edge_changed" || c.kind === "edge_retargeted").map((c) => c.edge_id));
    if (!added.size && !changed.size) return edges;
    return edges.map((e) => (added.has(e.id) || changed.has(e.id)
      ? { ...e, className: added.has(e.id) ? "diff-edge-added" : "diff-edge-changed" }
      : e));
  }, [edges, show, overlay]);
}

/** Легенда цветов поверх холста – видна, пока включено «Изменения». */
export function DiffLegend() {
  const show = useVersions((s) => s.showChanges);
  const overlay = useVersions((s) => s.overlay);
  const base = useVersions((s) => s.base);
  if (!show || !overlay) return null;
  const count = (m: StepMark[]) => Object.values(overlay.steps).filter((x) => m.includes(x)).length;
  const against = base !== null ? `v${base}` : overlay.against ? `опубликованной v${overlay.against}` : "пустой воронки";
  return (
    <div className="diff-legend" data-testid="diff-legend">
      <b>Изменения относительно {against}</b>
      <span><i className="lg lg-added" /> добавлено {count(["added"])}</span>
      <span><i className="lg lg-changed" /> изменено {count(["changed", "renamed", "tombstoned"])}</span>
      <span><i className="lg lg-removed" /> удалено {count(["removed"])}</span>
      <span className="muted">клик по строке диффа – к карточке</span>
    </div>
  );
}
