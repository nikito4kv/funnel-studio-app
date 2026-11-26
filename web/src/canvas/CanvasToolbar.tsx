// Панели поверх холста – всё, что меняет ВИД холста, а не воронку:
//   сверху слева  – ветка (фишки времени в часах этой ветки), TG / MAX, заметки, «Изменения»;
//   сверху справа – «Перейти» и поиск;
//   снизу слева   – отменить / повторить, масштаб, «вписать всё».
import { Panel, useReactFlow, useStore } from "@xyflow/react";
import { Maximize, Minus, Plus, Redo2, Undo2 } from "lucide-react";
import type { Platform } from "../model/types";
import { NavMenu } from "../nav/NavMenu";
import { branchShort } from "../simple/labels";
import { useSimple } from "../simple/mode";
import { useStudio } from "../store";
import { ChangesToggle } from "../versions/VersionControls";

function ViewBar() {
  const funnel = useStudio((s) => s.funnel);
  const platform = useStudio((s) => s.platform);
  const branch = useStudio((s) => s.branch);
  const showNotes = useStudio((s) => s.showNotes);
  const set = useStudio((s) => s.set);
  const simple = useSimple();
  if (!funnel) return null;
  const branches = funnel.branch_router?.branches ?? [];
  return (
    <div className="canvas-bar nowheel">
      {branches.length > 0 && (
        <div className="seg" data-tip="Время на карточках – в часах выбранной ветки" data-testid="branch-switch">
          <span className="seg-label">Ветка</span>
          {branches.map((b) => (
            <button type="button" key={b.id} className={branch === b.id ? "seg-on" : ""} onClick={() => set({ branch: b.id })}>
              {simple ? branchShort(b, branches) : `${b.id} · ${b.webinar_time}`}
            </button>
          ))}
        </div>
      )}
      <div className="seg" data-testid="platform-switch" data-tip="Какой текст и кнопки показывать на карточках">
        {(["tg", "max"] as Platform[]).map((p) => (
          <button type="button" key={p} className={platform === p ? "seg-on" : ""} onClick={() => set({ platform: p })}>{p === "tg" ? "TG" : "MAX"}</button>
        ))}
      </div>
      <label className={"check toggle" + (showNotes ? " on" : "")} data-tip="Слой заметок и правок">
        <input type="checkbox" checked={showNotes} onChange={(e) => set({ showNotes: e.target.checked })} />
        <span>Заметки</span>
      </label>
      <ChangesToggle />
    </div>
  );
}

function ZoomBar() {
  const rf = useReactFlow();
  const zoom = useStore((s) => s.transform[2]);
  const past = useStudio((s) => s.past.length);
  const future = useStudio((s) => s.future.length);
  const { undo, redo } = useStudio.getState();
  return (
    <div className="canvas-bar canvas-bar-bottom nowheel">
      <div className="tb-group">
        <button type="button" className="ghost icon" onClick={undo} disabled={!past} aria-label="Отменить" data-tip="Отменить" data-kbd="Ctrl+Z" data-tip-side="top" data-testid="undo">
          <Undo2 size={16} strokeWidth={1.75} />
        </button>
        <button type="button" className="ghost icon" onClick={redo} disabled={!future} aria-label="Повторить" data-tip="Повторить" data-kbd="Ctrl+Y" data-tip-side="top" data-testid="redo">
          <Redo2 size={16} strokeWidth={1.75} />
        </button>
      </div>
      <div className="tb-group">
        <button type="button" className="ghost icon" onClick={() => void rf.zoomOut({ duration: 180 })} aria-label="Отдалить" data-tip="Отдалить" data-kbd="−" data-tip-side="top">
          <Minus size={16} strokeWidth={1.75} />
        </button>
        <button type="button" className="ghost zoom-pct" onClick={() => void rf.zoomTo(1, { duration: 180 })} data-tip="Масштаб 100 %" data-tip-side="top" data-testid="zoom-pct">
          {Math.round(zoom * 100)} %
        </button>
        <button type="button" className="ghost icon" onClick={() => void rf.zoomIn({ duration: 180 })} aria-label="Приблизить" data-tip="Приблизить" data-kbd="+" data-tip-side="top">
          <Plus size={16} strokeWidth={1.75} />
        </button>
        <button type="button" className="ghost icon" onClick={() => void rf.fitView({ padding: 0.06, duration: 300 })} aria-label="Вписать всё" data-tip="Вписать всю воронку" data-kbd="Shift+1" data-tip-side="top" data-testid="fit-view">
          <Maximize size={15} strokeWidth={1.75} />
        </button>
      </div>
    </div>
  );
}

export function CanvasToolbar() {
  return (
    <>
      <Panel position="top-left" className="canvas-panel"><ViewBar /></Panel>
      <Panel position="top-right" className="canvas-panel"><div className="canvas-bar nowheel"><NavMenu /></div></Panel>
      <Panel position="bottom-left" className="canvas-panel"><ZoomBar /></Panel>
    </>
  );
}
