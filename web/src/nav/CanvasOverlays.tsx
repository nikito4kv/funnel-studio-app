// Слой поверх холста: подсказка пустой воронки и поиск (Ctrl+F).
// И служебное: сохранение первой автораскладки импортированной воронки.
import { MousePointerClick, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { Funnel } from "../model/types";
import { useSimple, useUi } from "../simple/mode";
import { useStudio } from "../store";
import { searchSteps } from "./search";

/** Раскладка, посчитанная при первом открытии, сразу пишется на сервер и не пересчитывается. */
export async function saveFirstLayout(f: Funnel): Promise<void> {
  const name = useStudio.getState().name;
  if (!name) return;
  try {
    await api.save(name, f);
    const st = useStudio.getState();
    if (st.funnel === f && st.name === name) st.markSaved();
  } catch {
    /* не вышло – раскладка останется несохранённой правкой, как раньше */
  }
}

function EmptyHint() {
  const empty = useStudio((s) => !!s.funnel && s.funnel.steps.length === 0);
  const simple = useSimple();
  if (!empty) return null;
  return (
    <div className="empty-hint" data-testid="empty-hint">
      <span className="empty-hint-icon"><MousePointerClick size={20} strokeWidth={1.75} /></span>
      <div>
        <b>Воронка пока пустая</b>
        <ol>
          <li>Кликните «Сообщение с картинкой» слева – карточка появится на холсте.</li>
          <li>Напишите текст справа, добавьте картинку и кнопки.</li>
          <li>Следующее сообщение – кнопкой «Следующее сообщение» под карточкой.</li>
        </ol>
        {!simple && <div className="faint small">Режим разработчика: key шага задаётся в «Расширенных».</div>}
      </div>
    </div>
  );
}

export function SearchBox() {
  const open = useUi((s) => s.searchOpen);
  const setOpen = useUi((s) => s.setSearch);
  const funnel = useStudio((s) => s.funnel);
  const simple = useSimple();
  const [q, setQ] = useState("");
  const [cur, setCur] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const hits = useMemo(() => (funnel && open ? searchSteps(funnel, q) : []), [funnel, q, open]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && (k === "f" || k === "а")) {
        e.preventDefault();
        setOpen(true);
        requestAnimationFrame(() => { input.current?.focus(); input.current?.select(); });
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [setOpen]);

  useEffect(() => { if (open) requestAnimationFrame(() => input.current?.focus()); }, [open]);
  useEffect(() => setCur(0), [q]);

  if (!open || !funnel) return null;
  const go = (i: number) => {
    const h = hits[i];
    if (!h) return;
    setCur(i);
    useStudio.getState().focusStep(h.stepId);
  };
  return (
    <div className="search-box" data-testid="search" onKeyDown={(e) => e.stopPropagation()}>
      <div className="row">
        <Search size={18} strokeWidth={1.75} aria-hidden />
        <input ref={input} className="inp grow" placeholder="Найти шаг: слово из текста или название" value={q}
          data-testid="search-input"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") { setOpen(false); return; }
            if (e.key === "Enter") { e.preventDefault(); go(e.shiftKey ? Math.max(0, cur - 1) : (hits.length ? (cur + (useStudio.getState().selection.steps[0] === hits[cur]?.stepId ? 1 : 0)) % hits.length : 0)); }
            if (e.key === "ArrowDown") { e.preventDefault(); setCur((c) => Math.min(hits.length - 1, c + 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setCur((c) => Math.max(0, c - 1)); }
          }} />
        <span className="faint small">{q ? `${hits.length}${hits.length === 40 ? "+" : ""}` : <kbd>Esc</kbd>}</span>
        <button type="button" className="x" title="Закрыть (Esc)" aria-label="Закрыть" onClick={() => setOpen(false)}><X size={16} strokeWidth={2} /></button>
      </div>
      {q && (
        <div className="search-hits" data-testid="search-hits">
          {!hits.length && <div className="search-empty">Ничего не нашлось. Попробуйте другое слово из текста или название карточки.</div>}
          {hits.map((h, i) => (
            <button type="button" key={h.stepId} className={"search-hit" + (i === cur ? " on" : "")} onClick={() => go(i)} onMouseEnter={() => setCur(i)}>
              <span className="search-name">{simple ? h.name : `${h.key}${h.name !== h.key ? ` – ${h.name}` : ""}`}</span>
              <span className="search-where muted">{h.lane ? `${h.lane} · ` : ""}{h.where}</span>
              <span className="search-snip">{h.snippet[0]}<mark>{h.snippet[1]}</mark>{h.snippet[2]}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function CanvasOverlays() {
  return (
    <>
      <EmptyHint />
      <SearchBox />
    </>
  );
}
