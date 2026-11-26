// Строка панели проверки: текст (для постановщика – простыми словами), переход к шагу и
// кнопка «Исправить» у всего, что чинится автоматически.
import { ExternalLink } from "lucide-react";
import { useState } from "react";
import type { Funnel } from "../model/types";
import { RULE_TITLE, type Issue } from "../model/validate";
import { useStudio } from "../store";
import { applyFix, needsChoice, simpleIssueText, TARGET_CHOICES, TOKEN_CHOICES } from "./issues";
import { useSimple } from "./mode";

export function IssueRow({ issue: i, funnel }: { issue: Issue; funnel: Funnel }) {
  const simple = useSimple();
  const apply = useStudio((s) => s.apply);
  const focusStep = useStudio((s) => s.focusStep);
  const select = useStudio((s) => s.select);
  const [choice, setChoice] = useState("");
  const choices = i.fix === "token" ? TOKEN_CHOICES : i.fix === "target" ? TARGET_CHOICES : [];
  const go = () => {
    if (i.stepId) focusStep(i.stepId);
    if (i.edgeId && i.rule === 41) select({ edge: i.edgeId, steps: [] });
  };
  const fix = (c?: string) => apply((g) => applyFix(g, i, c));
  return (
    <div className={`issue issue-${i.level}`} data-testid="issue">
      <button type="button" className="issue-text" onClick={go} title={simple ? "показать шаг" : RULE_TITLE[i.rule]}>
        {!simple && <span className="issue-rule">№{i.rule}</span>}
        <span>{simple ? simpleIssueText(i, funnel) : i.message}</span>
      </button>
      {(i.fix || i.link) && (
        <div className="issue-fix">
          {i.link && (
            <a className="issue-link linkish" href={i.link} target="_blank" rel="noreferrer" data-testid="miro-link"
              data-tip="Открыть этот элемент на доске Miro">
              <ExternalLink size={13} strokeWidth={2} />открыть в Miro
            </a>
          )}
          {!i.fix ? null : needsChoice(i) ? (
            <>
              <select className="inp" value={choice} onChange={(e) => setChoice(e.target.value)} data-testid="fix-choice">
                <option value="">{i.fix === "token" ? "подставить…" : "куда ведёт…"}</option>
                {choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
              <button type="button" className="fix-btn" disabled={!choice} onClick={() => fix(choice)} data-testid="fix-button">Исправить</button>
            </>
          ) : (
            <button type="button" className="fix-btn" onClick={() => fix()} data-testid={i.fix === "miro_ok" ? "miro-ok" : "fix-button"}
              data-tip={i.fix === "miro_ok" ? "Проверил – убрать замечание. Повторный импорт его не вернёт" : undefined}>
              {i.fix === "shift" ? "Сдвинуть на минуту" : i.fix === "miro_ok" ? "Проверено" : "Исправить"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
