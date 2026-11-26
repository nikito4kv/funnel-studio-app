// Панель «⚠ Проверка», выгрузка yaml с круговой проверкой, импорт из bot-engine.
import { CircleCheck, TriangleAlert, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type ExportResult, type SourceItem } from "../api";
import * as ops from "../model/ops";
import type { Funnel } from "../model/types";
import { validate } from "../model/validate";
import { useStudio } from "../store";
import { IssueRow } from "../simple/IssueRow";
import { useSimple } from "../simple/mode";
import { ExportMedia } from "./ExportMedia";
import { useMiro } from "../miro/MiroImport";

export function ValidationPanel({ funnel }: { funnel: Funnel }) {
  const issues = useMemo(() => validate(funnel), [funnel]);
  const apply = useStudio((s) => s.apply);
  const simple = useSimple();
  const errors = issues.filter((i) => i.level === "error");
  const warns = issues.filter((i) => i.level === "warn");
  const dashes = issues.filter((i) => i.fix === "dashes").length;
  const row = (i: (typeof issues)[number]) => <IssueRow key={i.id} issue={i} funnel={funnel} />;
  return (
    <div className="panel-body" data-testid="validation-panel">
      <div className="panel-title"><span><TriangleAlert size={18} strokeWidth={1.75} />Проверка</span><span className="muted">ошибок {errors.length} · предупреждений {warns.length}</span></div>
      {!issues.length && <div className="empty-state"><CircleCheck size={28} strokeWidth={1.5} className="ok-ic" /><b>Замечаний нет</b><p>Студия проверяет тексты, кнопки, время и стрелки. Здесь появится то, что стоит поправить.</p></div>}
      {dashes > 0 && (
        <button type="button" className="fix-all" onClick={() => apply((g) => ops.fixDashes(g))}>
          Исправить все длинные тире ({dashes} {dashes === 1 ? "шаг" : "шагов"})
        </button>
      )}
      {errors.length > 0 && <div className="issue-group">{simple ? "Ошибки – люди не получат сообщение или получат не то" : "Ошибки – движок споткнётся или люди не получат сообщение"}</div>}
      {errors.map(row)}
      {warns.length > 0 && <div className="issue-group">Предупреждения</div>}
      {warns.map(row)}
      <div className="field-hint">{simple ? "Клик по строке – показать шаг на холсте. «Исправить» чинит сразу, Ctrl+Z – отменить." : "Клик по строке – показать шаг на холсте. Правила – приложение А исследования."}</div>
    </div>
  );
}

export function ExportDialog({ funnel, onClose }: { funnel: Funnel; onClose: () => void }) {
  const [res, setRes] = useState<ExportResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.exportYaml(funnel).then(setRes).catch((e) => setErr(String(e.message ?? e)));
  }, [funnel]);
  const download = () => {
    if (!res) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([res.yaml], { type: "text/yaml" }));
    const base = funnel.source?.path.split("/").pop() || `${funnel.id}.yaml`;
    a.download = base;
    a.click();
  };
  let status: React.ReactNode = <div className="muted">Выгружаю и проверяю движком…</div>;
  if (err) status = <div className="err-box">{err}</div>;
  else if (res) {
    if (!res.ok && res.export_error) status = <div className="err-box">Выгрузить нельзя: {res.export_error}</div>;
    else if (!res.engine.ok) status = <div className="err-box">Движок не читает выгрузку: {res.engine.error}</div>;
    else if (res.compare && res.compare.same === null) {
      status = <div className="warn-box big" data-testid="export-nocompare">Движок читает выгрузку (шагов {res.engine.steps}). {res.compare.error}</div>;
    } else if (res.compare?.same) status = <div className="ok-box big" data-testid="export-same"><CircleCheck size={16} strokeWidth={2} />Движок читает так же, как оригинал <span className="mono">{res.compare.path}</span> – шагов {res.engine.steps}</div>;
    else if (res.compare) {
      status = (
        <div>
          <div className="warn-box big">Движок читает выгрузку (шагов {res.engine.steps}), но она отличается от <span className="mono">{res.compare.path}</span>: {res.compare.differences.length}</div>
          <table className="diff">
            <thead><tr><th>шаг</th><th>поле</th><th>было</th><th>стало</th></tr></thead>
            <tbody>
              {res.compare.differences.map((d, i) => (
                <tr key={i}>
                  <td className="mono">{d.step ?? "–"}</td>
                  <td className="mono">{d.field ?? d.text}</td>
                  <td>{d.original === null || d.original === undefined ? "" : String(JSON.stringify(d.original)).slice(0, 140)}</td>
                  <td>{d.exported === null || d.exported === undefined ? "" : String(JSON.stringify(d.exported)).slice(0, 140)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    } else status = <div className="ok-box big"><CircleCheck size={16} strokeWidth={2} />Движок читает выгрузку – шагов {res.engine.steps} (сравнивать не с чем: воронка создана в Студии)</div>;
  }
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} data-testid="export-dialog">
        <div className="modal-head">
          <h3>Выгрузка yaml · {funnel.id}</h3>
          <button type="button" className="x" aria-label="Закрыть" onClick={onClose}><X size={15} strokeWidth={2} /></button>
        </div>
        {status}
        {res?.yaml && (
          <>
            <div className="row">
              <button type="button" onClick={() => navigator.clipboard?.writeText(res.yaml)}>Скопировать</button>
              <button type="button" onClick={download}>Скачать .yaml</button>
              <span className="muted">{res.yaml.split("\n").length} строк · заметки, правки, полосы и раскладка в yaml не попадают</span>
            </div>
            <ExportMedia funnel={funnel} media={res.media ?? []} />
            <pre className="yaml">{res.yaml}</pre>
          </>
        )}
      </div>
    </div>
  );
}

export function ImportDialog({ onOpen, onClose }: { onOpen: (name: string) => void; onClose: () => void }) {
  const [sources, setSources] = useState<SourceItem[] | null>(null);
  const [q, setQ] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [launch, setLaunch] = useState("demo");
  const [fname, setFname] = useState("funnel");
  useEffect(() => { api.sources().then(setSources).catch((e) => setErr(e.message)); }, []);
  const shown = (sources ?? []).filter((s) => !q || s.path.toLowerCase().includes(q.toLowerCase()));
  const open = async (s: SourceItem) => {
    try {
      const r = await api.importSource(s.path);
      onOpen(r.name);
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const create = async () => {
    try {
      const r = await api.create(launch, fname, `${launch}_${fname}`);
      onOpen(r.name);
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} data-testid="import-dialog">
        <div className="modal-head">
          <h3>Открыть воронку</h3>
          <button type="button" className="x" aria-label="Закрыть" onClick={onClose}><X size={15} strokeWidth={2} /></button>
        </div>
        {err && <div className="err-box">{err}</div>}
        <div className="row">
          <b>Новая пустая:</b>
          <input className="inp" value={launch} onChange={(e) => setLaunch(e.target.value)} placeholder="запуск" />
          <span>/</span>
          <input className="inp" value={fname} onChange={(e) => setFname(e.target.value)} placeholder="воронка" />
          <button type="button" onClick={create}>Создать</button>
        </div>
        <div className="row">
          <b>Из Miro:</b>
          <span className="muted grow">фрейм запуска по ссылке – сообщения, картинки, кнопки, стрелки и время</span>
          <button type="button" data-testid="miro-from-open" onClick={() => { onClose(); useMiro.getState().show(); }}>Импорт из Miro…</button>
        </div>
        <div className="row">
          <b>Импорт из bot-engine</b>
          <input className="inp grow" placeholder="поиск: webinar, leadmagnet…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="sources">
          {!sources && !err && <div className="muted">Загружаю список…</div>}
          {shown.map((s) => (
            <button type="button" key={s.path} className="source" onClick={() => open(s)}>
              <span className="source-name">{s.path.replace(/^launches\//, "").replace(/\.ya?ml$/, "").replace("/", " · ")}</span>
              <span className="grow" />
              {s.imported && <span className="tag">уже в Студии – открыть</span>}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
