// Варианты для источников трафика:
//   • у шага – «если метка utm_medium = posev → другой текст / другие кнопки» (variants);
//   • у воронки – «если пост = 2880 → начать с другого шага» (entry_variants).
// Первый подошедший вариант выигрывает – частные условия ставим выше общих.
import { ArrowDown, ArrowUp, Plus, X } from "lucide-react";
import { COHORT_FIELDS, clearWhen, isEmptyWhen, newEntryVariant, newStepVariant, PLATFORM_LABEL, setWhen, whenFields, whenPhrase, whenValue, type CohortField } from "../model/cohort";
import { useState } from "react";
import * as ops from "../model/ops";
import type { CohortMatch, EntryVariant, Funnel, Step, StepVariant } from "../model/types";
import { useSimple } from "../simple/mode";
import { stepRef } from "../simple/names";
import { useStudio } from "../store";
import { ButtonsEditor } from "./ButtonsEditor";
import { TextEditor } from "./TextEditor";
import { Section } from "./ui";

function CohortEditor({ when, onChange }: { when: CohortMatch; onChange: (w: CohortMatch) => void }) {
  // поле со стёртым значением не исчезает, пока его редактируют
  const [shown, setShown] = useState<CohortField[]>([]);
  const set = whenFields(when);
  const used = COHORT_FIELDS.map((f) => f.key).filter((k) => set.includes(k) || shown.includes(k));
  const free = COHORT_FIELDS.filter((f) => !used.includes(f.key));
  return (
    <div className="cohort" data-testid="cohort">
      {used.map((f) => {
        const info = COHORT_FIELDS.find((x) => x.key === f)!;
        return (
          <div className="row" key={f}>
            <span className="cohort-label" title={info.hint}>{info.label} =</span>
            <input className="inp grow" value={whenValue(when, f)} placeholder={info.hint}
              onChange={(e) => onChange(setWhen(when, f, e.target.value))} data-testid={`cohort-${f}`} />
            <button type="button" className="x" title="убрать условие" aria-label="Убрать условие" onClick={() => { setShown(shown.filter((x) => x !== f)); onChange(clearWhen(when, f)); }}><X size={15} strokeWidth={2} /></button>
          </div>
        );
      })}
      <div className="row">
        {free.length > 0 && (
          <select className="inp" value="" onChange={(e) => {
            const f = e.target.value as CohortField;
            if (f) setShown([...shown, f]);
          }}>
            <option value="">ещё условие…</option>
            {free.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
          </select>
        )}
        <span className="muted small">платформа:</span>
        {(["tg", "max", "vk"] as const).map((p) => (
          <label className="check" key={p}>
            <input type="checkbox" checked={when.platforms.includes(p)}
              onChange={(e) => onChange({ ...when, platforms: e.target.checked ? [...when.platforms, p] : when.platforms.filter((x) => x !== p) })} />
            <span>{PLATFORM_LABEL[p]}</span>
          </label>
        ))}
      </div>
      {isEmptyWhen(when) && <div className="warn-box">Условие не задано – вариант сработает у всех.</div>}
    </div>
  );
}

function move<T>(list: T[], i: number, d: number): T[] {
  const j = i + d;
  if (j < 0 || j >= list.length) return list;
  const next = [...list];
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

export function VariantsEditor({ step }: { step: Step }) {
  const apply = useStudio((s) => s.apply);
  const simple = useSimple();
  const list = step.content.variants;
  const save = (variants: StepVariant[], co?: string) =>
    apply((g) => ops.setVariants(g, { stepId: step.id, variants }), co ? { coalesce: `var:${step.id}:${co}` } : undefined);
  const patch = (i: number, p: Partial<StepVariant>, co?: string) => save(list.map((v, j) => (j === i ? { ...v, ...p } : v)), co);
  return (
    <Section title={`Другой текст для источника трафика${list.length ? ` (${list.length})` : ""}`} open={list.length > 0}
      hint={simple ? "например, для посевов – свой текст" : "variants"} testId="sec-variants">
      {!list.length && <div className="muted">Всем приходит один текст. Можно задать другой для тех, кто пришёл по определённой ссылке.</div>}
      {list.map((v, i) => (
        <div className="variant" key={i} data-testid="variant">
          <div className="row">
            <b>Вариант {i + 1}</b>
            <span className="muted small grow">если {whenPhrase(v.when)}</span>
            <button type="button" className="icon" title="выше – проверяется раньше" aria-label="Выше" onClick={() => save(move(list, i, -1))}><ArrowUp size={14} strokeWidth={2} /></button>
            <button type="button" className="icon" title="ниже" aria-label="Ниже" onClick={() => save(move(list, i, 1))}><ArrowDown size={14} strokeWidth={2} /></button>
            <button type="button" className="icon x" title="удалить вариант" onClick={() => save(list.filter((_, j) => j !== i))}><X size={15} strokeWidth={2} /></button>
          </div>
          <CohortEditor when={v.when} onChange={(w) => patch(i, { when: w }, `when${i}`)} />
          <div className="variant-part">
            <label className="check">
              <input type="checkbox" checked={v.text !== null} onChange={(e) => patch(i, { text: e.target.checked ? step.content.tg.text : null })} />
              <span>свой текст</span>
            </label>
            {v.text !== null && <TextEditor value={v.text} docKey={`${step.id}:var${i}`} minRows={4} onChange={(t) => patch(i, { text: t }, `text${i}`)} />}
            {v.text === null && <div className="muted small">текст – как обычно</div>}
          </div>
          <div className="variant-part">
            <label className="check">
              <input type="checkbox" checked={v.text_max !== null} onChange={(e) => patch(i, { text_max: e.target.checked ? (v.text ?? step.content.max.text ?? step.content.tg.text) : null })} />
              <span>отдельный текст для MAX</span>
            </label>
            {v.text_max !== null && <TextEditor value={v.text_max} docKey={`${step.id}:varmax${i}`} minRows={3} limit={4000} onChange={(t) => patch(i, { text_max: t }, `tmax${i}`)} />}
          </div>
          <div className="variant-part">
            <label className="check">
              <input type="checkbox" checked={v.buttons !== null} onChange={(e) => patch(i, { buttons: e.target.checked ? step.content.tg.buttons : null })} />
              <span>свои кнопки</span>
            </label>
            {v.buttons !== null && <ButtonsEditor buttons={v.buttons} survey={!!step.survey.qid} onChange={(b) => patch(i, { buttons: b }, `btn${i}`)} />}
          </div>
        </div>
      ))}
      <button type="button" className="add" data-testid="add-variant" onClick={() => save([...list, newStepVariant()])}><Plus size={15} strokeWidth={2} />Другой текст для источника</button>
      {list.length > 1 && <div className="field-hint">Срабатывает первый подошедший вариант – частные условия ставьте выше.</div>}
    </Section>
  );
}

export function EntryVariantsEditor({ funnel }: { funnel: Funnel }) {
  const apply = useStudio((s) => s.apply);
  const simple = useSimple();
  const list = funnel.entry_variants;
  const save = (v: EntryVariant[], co?: string) => apply((g) => ops.setEntryVariants(g, v), co ? { coalesce: `ev:${co}` } : undefined);
  const first = funnel.branch_router?.branches[0]?.entry_step ?? funnel.steps[0]?.id;
  return (
    <Section title={`Свой первый шаг для источника${list.length ? ` (${list.length})` : ""}`} open={list.length > 0} testId="sec-entry-variants"
      hint={simple ? "например, с поста канала – сразу приглашение" : "entry_variants"}>
      {!list.length && <div className="muted">Все начинают с одного шага. Можно начать с другого тех, кто пришёл по определённой ссылке.</div>}
      {list.map((v, i) => (
        <div className="variant" key={i} data-testid="entry-variant">
          <div className="row">
            <b>Если {whenPhrase(v.when)}</b>
            <span className="grow" />
            <button type="button" className="icon" aria-label="Выше" onClick={() => save(move(list, i, -1))}><ArrowUp size={14} strokeWidth={2} /></button>
            <button type="button" className="icon" aria-label="Ниже" onClick={() => save(move(list, i, 1))}><ArrowDown size={14} strokeWidth={2} /></button>
            <button type="button" className="icon x" title="удалить" onClick={() => save(list.filter((_, j) => j !== i))}><X size={15} strokeWidth={2} /></button>
          </div>
          <CohortEditor when={v.when} onChange={(w) => save(list.map((x, j) => (j === i ? { ...x, when: w } : x)), `w${i}`)} />
          <div className="row">
            <span>→ начать с</span>
            <select className="inp grow" value={v.entry_step} onChange={(e) => save(list.map((x, j) => (j === i ? { ...x, entry_step: e.target.value } : x)))}>
              {funnel.steps.map((s) => <option key={s.id} value={s.id}>{stepRef(s, simple)}</option>)}
            </select>
          </div>
        </div>
      ))}
      {first && <button type="button" className="add" data-testid="add-entry-variant" onClick={() => save([...list, newEntryVariant(first)])}><Plus size={15} strokeWidth={2} />Свой первый шаг для источника</button>}
    </Section>
  );
}
