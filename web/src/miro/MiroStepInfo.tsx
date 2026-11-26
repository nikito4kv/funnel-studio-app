// Панель шага: карточка собрана из Miro – ссылка на элемент доски и то, в чём импорт не уверен.
import { Check, ExternalLink, ScanSearch } from "lucide-react";
import * as ops from "../model/ops";
import type { Funnel, Step } from "../model/types";
import { useStudio } from "../store";

export function MiroStepInfo({ step, funnel }: { step: Step; funnel: Funnel }) {
  const apply = useStudio((s) => s.apply);
  const m = step.miro;
  if (!m) return null;
  const url = ops.miroItemUrl(funnel, m.item_id);
  const checks = m.doubts.filter((d) => d.level === "check");
  const infos = m.doubts.filter((d) => d.level === "info");
  return (
    <div className={`miro-box${checks.length ? " has-checks" : ""}`} data-testid="miro-step">
      <div className="miro-box-head">
        <ScanSearch size={14} strokeWidth={1.75} />
        <span>Собрано из Miro</span>
        <span className="grow" />
        {url && (
          <a className="linkish" href={url} target="_blank" rel="noreferrer" data-testid="miro-open-item"
            data-tip="Открыть это сообщение на доске Miro">
            открыть в Miro<ExternalLink size={12} strokeWidth={2} />
          </a>
        )}
      </div>
      {checks.map((d) => (
        <div className="miro-doubt" key={ops.miroDoubtKey(d)} data-testid="miro-doubt">
          <span>{d.text}</span>
          <div className="miro-doubt-actions">
            {d.item_id && d.item_id !== m.item_id && (
              <a className="linkish" href={ops.miroItemUrl(funnel, d.item_id) ?? undefined} target="_blank" rel="noreferrer">на доске</a>
            )}
            <button type="button" className="fix-btn" data-testid="miro-doubt-ok"
              data-tip="Проверил – убрать замечание. Повторный импорт его не вернёт"
              onClick={() => apply((g) => ops.dismissMiroDoubt(g, { stepId: step.id, key: ops.miroDoubtKey(d) }))}>
              <Check size={13} strokeWidth={2} />Проверено
            </button>
          </div>
        </div>
      ))}
      {infos.length > 0 && (
        <details className="miro-infos">
          <summary>сделано по правилам ({infos.length})</summary>
          <ul>{infos.map((d) => <li key={ops.miroDoubtKey(d)}>{d.text}</li>)}</ul>
        </details>
      )}
    </div>
  );
}
