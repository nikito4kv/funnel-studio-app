// Горячие клавиши холста («?» – шпаргалка, Shift+1 – вписать всё, + / − – масштаб) и сама
// шпаргалка. Остальные клавиши (Ctrl+S, Delete, Ctrl+Z / Y, Ctrl+C / V, Ctrl+F) обрабатывают
// App.tsx и поиск; пробел + перетаскивание двигает холст силами React Flow.
import { useReactFlow } from "@xyflow/react";
import { X } from "lucide-react";
import { useEffect } from "react";
import { useUi } from "../simple/mode";
import { useStudio } from "../store";

function typing(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

export const SHORTCUTS: { title: string; rows: [string[], string][] }[] = [
  {
    title: "Основное",
    rows: [
      [["Ctrl", "S"], "Сохранить черновик"],
      [["Ctrl", "Z"], "Отменить"],
      [["Ctrl", "Y"], "Повторить (или Ctrl+Shift+Z)"],
      [["Ctrl", "F"], "Найти шаг по тексту"],
      [["?"], "Эта шпаргалка"],
    ],
  },
  {
    title: "Карточки",
    rows: [
      [["Delete"], "Удалить выделенное"],
      [["Ctrl", "C"], "Копировать карточки"],
      [["Ctrl", "V"], "Вставить копию"],
      [["Shift", "протянуть"], "Выделить рамкой"],
      [["Двойной клик"], "Курсор в текст карточки"],
      [["Alt", "тянуть стрелку"], "Стрелка «одновременно» (пунктир)"],
      [["Esc"], "Снять выделение"],
    ],
  },
  {
    title: "Холст",
    rows: [
      [["Колесо"], "Масштаб (точка под курсором на месте)"],
      [["Пробел", "тянуть"], "Двигать холст"],
      [["Shift", "1"], "Вписать всю воронку"],
      [["+"], "Приблизить"],
      [["−"], "Отдалить"],
    ],
  },
];

/** Клавиши холста. Рендерится внутри ReactFlowProvider. */
export function CanvasKeys() {
  const rf = useReactFlow();
  const setKeys = useUi((s) => s.setShortcuts);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (typing(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "?" || (e.shiftKey && e.code === "Slash")) { e.preventDefault(); setKeys(true); return; }
      if (!useStudio.getState().funnel) return;
      if (e.shiftKey && (e.code === "Digit1" || e.key === "!")) { e.preventDefault(); void rf.fitView({ padding: 0.06, duration: 300 }); return; }
      if (e.key === "+" || e.key === "=") { e.preventDefault(); void rf.zoomIn({ duration: 180 }); return; }
      if (e.key === "-" || e.key === "−" || e.key === "_") { e.preventDefault(); void rf.zoomOut({ duration: 180 }); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rf, setKeys]);
  // пробел зажат – курсор «рука», как в Miro и Figma
  useEffect(() => {
    const set = (on: boolean) => document.querySelector(".canvas")?.classList.toggle("space-pan", on);
    const down = (e: KeyboardEvent) => { if (e.code === "Space" && !typing(e.target)) set(true); };
    const up = (e: KeyboardEvent) => { if (e.code === "Space") set(false); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", () => set(false));
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, []);
  return null;
}

export function Shortcuts() {
  const open = useUi((s) => s.shortcuts);
  const setKeys = useUi((s) => s.setShortcuts);
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setKeys(false); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [open, setKeys]);
  if (!open) return null;
  return (
    <div className="modal-back" onClick={() => setKeys(false)}>
      <div className="modal keys-modal" onClick={(e) => e.stopPropagation()} data-testid="shortcuts" role="dialog" aria-label="Горячие клавиши">
        <div className="modal-head">
          <h3>Горячие клавиши</h3>
          <button type="button" className="x" aria-label="Закрыть" onClick={() => setKeys(false)}><X size={16} strokeWidth={2} /></button>
        </div>
        <div className="keys-grid">
          {SHORTCUTS.map((g) => (
            <div className="keys-group" key={g.title}>
              <div className="keys-title">{g.title}</div>
              {g.rows.map(([keys, what]) => (
                <div className="keys-row" key={what}>
                  <span className="keys-keys">{keys.map((k, i) => <kbd key={i}>{k}</kbd>)}</span>
                  <span>{what}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
