// «Импорт из Miro»: ссылка на фрейм → запуск и имя → ход импорта → итог.
// Новая воронка сохраняется сразу; повторный импорт той же доски показывает «было / стало»
// и ждёт «Применить» (ручные правки в полях, которых нет на доске, не теряются).
import { CircleCheck, Import, LayoutTemplate, TriangleAlert, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { create } from "zustand";
import { api, type SourceItem } from "../api";
import { isDirty, useStudio } from "../store";
import { DiffList } from "../versions/DiffList";
import { boardLine, miroApi, MIRO_RULES, type MiroJob } from "./api";
import "./miro.css";

interface Prefill { link: string; launch: string; name: string }

export const useMiro = create<{ open: boolean; prefill: Prefill | null; show: (p?: Prefill | null) => void; hide: () => void }>((set) => ({
  open: false,
  prefill: null,
  show: (p = null) => set({ open: true, prefill: p }),
  hide: () => set({ open: false, prefill: null }),
}));

/** Открыть импорт: для воронки из Miro – «обновить из Miro» с той же ссылкой. */
export function openMiroImport() {
  const st = useStudio.getState();
  const f = st.funnel;
  if (f?.miro && st.name) {
    const [launch, name] = st.name.split("__");
    useMiro.getState().show({ link: f.miro.link, launch, name });
  } else useMiro.getState().show();
}

export function MiroRules({ open = false }: { open?: boolean }) {
  return (
    <details className="miro-rules" open={open} data-testid="miro-rules">
      <summary><LayoutTemplate size={14} strokeWidth={1.75} />Как рисовать в Miro, чтобы Студия поняла</summary>
      <ol>
        {MIRO_RULES.map((r) => <li key={r.title}><b>{r.title}</b> – {r.text}</li>)}
      </ol>
    </details>
  );
}

export function MiroImportHost({ onOpen }: { onOpen: (name: string) => Promise<void> | void }) {
  const open = useMiro((s) => s.open);
  if (!open) return null;
  return <MiroImportDialog onOpen={onOpen} />;
}

function MiroImportDialog({ onOpen }: { onOpen: (name: string) => Promise<void> | void }) {
  const prefill = useMiro((s) => s.prefill);
  const hide = useMiro((s) => s.hide);
  const toast = useStudio((s) => s.toast);
  const [link, setLink] = useState(prefill?.link ?? "");
  const [launch, setLaunch] = useState(prefill?.launch ?? "");
  const [name, setName] = useState(prefill?.name ?? "miro");
  const [launches, setLaunches] = useState<string[]>([]);
  const [token, setToken] = useState<boolean | null>(null);
  const [job, setJob] = useState<MiroJob | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    miroApi.status().then((s) => setToken(s.token)).catch(() => setToken(null));
    api.sources().then((src: SourceItem[]) => setLaunches([...new Set(src.map((s) => s.launch))].sort())).catch(() => {});
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") hide(); };
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("keydown", esc); if (timer.current) window.clearTimeout(timer.current); };
  }, [hide]);

  const poll = (id: string) => {
    miroApi.job(id).then((j) => {
      setJob(j);
      if (j.state === "running") timer.current = window.setTimeout(() => poll(id), 400);
      else setBusy(false);
    }).catch((e) => { setErr((e as Error).message); setBusy(false); });
  };

  const start = async () => {
    setErr(null);
    setJob(null);
    // несохранённое – сначала в черновик: повторный импорт сливается с сохранённым
    const st = useStudio.getState();
    if (st.funnel && st.name && isDirty(st)) {
      try {
        await api.save(st.name, st.funnel);
        st.markSaved();
      } catch (e) {
        setErr(`Не удалось сохранить текущую воронку: ${(e as Error).message}`);
        return;
      }
    }
    setBusy(true);
    try {
      const r = await miroApi.start(link.trim(), launch.trim(), name.trim());
      poll(r.job);
    } catch (e) {
      setErr((e as Error).message);
      setBusy(false);
    }
  };

  const res = job?.state === "done" ? job.result : undefined;
  const checks = useMemo(() => (res?.doubts ?? []).filter((d) => d.level === "check"), [res]);
  const openFunnel = async () => {
    if (!res) return;
    hide();
    await onOpen(res.name);
  };
  const apply = async () => {
    if (!job || !res) return;
    setBusy(true);
    try {
      await miroApi.apply(job.job);
      hide();
      const st = useStudio.getState();
      const f = await api.get(res.name);
      if (st.name === res.name) st.load(res.name, f);
      else await onOpen(res.name);
      toast("Черновик обновлён из Miro", "ok");
    } catch (e) {
      setErr((e as Error).message);
      setBusy(false);
    }
  };

  const pct = job && job.progress.total ? Math.round((100 * job.progress.done) / job.progress.total) : null;
  return (
    <div className="modal-back" onClick={hide}>
      <div className="modal miro-modal" onClick={(e) => e.stopPropagation()} data-testid="miro-dialog" role="dialog" aria-label="Импорт из Miro">
        <div className="modal-head">
          <h3>{prefill ? "Обновить из Miro" : "Импорт из Miro"}</h3>
          <button type="button" className="x" aria-label="Закрыть" onClick={hide}><X size={15} strokeWidth={2} /></button>
        </div>
        {token === false && (
          <div className="warn-box">На сервере Студии не задан токен Miro (MIRO_TOKEN) – импорт не сработает.</div>
        )}
        {!res && (
          <>
            <label className="field">
              <span className="field-label">Ссылка на фрейм</span>
              <input className="inp" data-testid="miro-link-input" autoFocus value={link} onChange={(e) => setLink(e.target.value)}
                placeholder="https://miro.com/app/board/…/?moveToWidget=…" disabled={busy} />
              <span className="field-hint">В Miro: правый клик по фрейму → «Copy link to object».</span>
            </label>
            <div className="row">
              <label className="field grow">
                <span className="field-label">Запуск</span>
                <input className="inp" list="miro-launches" data-testid="miro-launch" value={launch} onChange={(e) => setLaunch(e.target.value)}
                  placeholder="например webinar" disabled={busy || !!prefill} />
                <datalist id="miro-launches">{launches.map((l) => <option key={l} value={l} />)}</datalist>
              </label>
              <label className="field grow">
                <span className="field-label">Имя воронки</span>
                <input className="inp" data-testid="miro-name" value={name} onChange={(e) => setName(e.target.value)} disabled={busy || !!prefill} />
              </label>
            </div>
            {!busy && <MiroRules />}
          </>
        )}
        {err && <div className="err-box" data-testid="miro-error">{err}</div>}
        {job?.state === "error" && <div className="err-box" data-testid="miro-error">{job.message}</div>}
        {busy && job?.state !== "done" && (
          <div className="miro-progress" data-testid="miro-progress">
            <span>{job?.stage ?? "Начинаю…"}{job && job.progress.total ? ` ${job.progress.done} из ${job.progress.total}` : ""}</span>
            <div className="bar"><div className="bar-fill" style={{ width: `${pct ?? 15}%` }} /></div>
          </div>
        )}
        {res && (
          <div className="miro-result" data-testid="miro-result">
            <div className={checks.length ? "warn-box big" : "ok-box big"}>
              {checks.length ? <TriangleAlert size={16} strokeWidth={2} /> : <CircleCheck size={16} strokeWidth={2} />}
              <span data-testid="miro-summary">
                «{res.frame}»: собрано {res.stats.messages} сообщ., картинок {res.stats.images}
                {res.stats.days ? `, дней ${res.stats.days}` : ""}
                {res.stats.notes ? `, заметок на холсте ${res.stats.notes}` : ""}
                {checks.length ? `, ${checks.length} мест проверить` : " – проверять нечего"}
              </span>
            </div>
            {res.stats.board && <div className="field-hint" data-testid="miro-board-count">{boardLine(res.stats.board)}</div>}
            {!res.created && (res.report_text ?? []).length > 0 && (
              <ul className="miro-report">{res.report_text!.map((t) => <li key={t}>{t}</li>)}</ul>
            )}
            {!res.created && res.diff && (
              <>
                <div className="sec-group-title">Было / стало</div>
                <DiffList diff={res.diff} empty="Доска не изменилась – черновик останется как есть." />
              </>
            )}
            {checks.length > 0 && (
              <details className="miro-checks" open={res.created}>
                <summary>Что проверить ({checks.length})</summary>
                <ul>{checks.slice(0, 40).map((d, i) => <li key={i}><b>{d.step_key ?? "воронка"}</b>: {d.text}</li>)}</ul>
                <span className="field-hint">Эти места подсвечены на холсте и в панели «Проверка» – там же ссылка на элемент доски.</span>
              </details>
            )}
          </div>
        )}
        <div className="row end">
          {!res && <button type="button" onClick={hide}>Отмена</button>}
          {!res && (
            <button type="button" className="primary" data-testid="miro-go" disabled={busy || !link.trim() || !launch.trim() || !name.trim()} onClick={() => void start()}>
              <Import size={15} strokeWidth={2} />{busy ? "Импортирую…" : "Импортировать"}
            </button>
          )}
          {res?.created && <button type="button" className="primary" data-testid="miro-open-funnel" onClick={() => void openFunnel()}>Открыть воронку</button>}
          {res && !res.created && (
            <>
              <button type="button" onClick={hide}>Не применять</button>
              <button type="button" className="primary" data-testid="miro-apply" disabled={busy || !res.diff?.changes.length} onClick={() => void apply()}>Применить</button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
