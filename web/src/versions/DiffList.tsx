// Список «было / стало» по группам. Клик по строке – карточка на холсте (удалённая – её тень).
import { useReactFlow } from "@xyflow/react";
import { useStudio } from "../store";
import type { Change, Diff, DiffGroup, Warning } from "./api";
import { useVersions } from "./store";

export const GROUP_TITLE: Record<DiffGroup, string> = {
  settings: "Настройки воронки",
  added: "Добавленные шаги",
  removed: "Удалённые и снятые шаги",
  renamed: "Переименованные шаги",
  changed: "Изменённые шаги",
  edges: "Стрелки",
};
const ORDER: DiffGroup[] = ["settings", "added", "removed", "renamed", "changed", "edges"];

/** Показать шаг изменения на холсте. Удалённого шага нет в графе – центрируемся на его тени. */
export function usePickChange() {
  const rf = useReactFlow();
  return (c: { step_id: string | null; edge_id?: string | null }) => {
    const st = useStudio.getState();
    const v = useVersions.getState();
    if (!c.step_id || !st.funnel) return;
    if (st.funnel.steps.some((s) => s.id === c.step_id)) {
      st.focusStep(c.step_id);
      if (c.edge_id && st.funnel.edges.some((e) => e.id === c.edge_id)) st.select({ edge: c.edge_id, steps: [c.step_id] });
      return;
    }
    const ghost = v.overlay?.ghosts.find((g) => g.id === c.step_id);
    if (ghost?.position) {
      v.set({ showChanges: true });
      rf.setCenter(ghost.position.x + 160, ghost.position.y + 100, { zoom: 1, duration: 300 });
    } else {
      st.toast("Этого шага нет в черновике – показать нечего", "info");
    }
  };
}

export function DiffList({ diff, warnings, empty = "Изменений нет." }: { diff: Diff; warnings?: Warning[]; empty?: string }) {
  const pick = usePickChange();
  const by = new Map<DiffGroup, Change[]>();
  for (const c of diff.changes) by.set(c.group, [...(by.get(c.group) ?? []), c]);
  return (
    <div className="diff-list" data-testid="diff-list">
      {!diff.changes.length && <div className="muted">{empty}</div>}
      {warnings && warnings.length > 0 && (
        <div className="diff-group">
          <div className="diff-group-title warn">Что будет с людьми внутри воронки · {warnings.length}</div>
          {warnings.map((w, i) => (
            <button type="button" key={i} className="diff-row diff-warn" onClick={() => pick(w)}>{w.text}</button>
          ))}
        </div>
      )}
      {ORDER.filter((g) => by.has(g)).map((g) => (
        <div className="diff-group" key={g}>
          <div className={`diff-group-title dg-${g}`}>{GROUP_TITLE[g]} · {by.get(g)!.length}</div>
          {by.get(g)!.map((c, i) => (
            <button type="button" key={i} className={`diff-row dr-${g}`} onClick={() => pick(c)}
              title={c.step_id ? "показать на холсте" : undefined}>
              {c.text}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}
