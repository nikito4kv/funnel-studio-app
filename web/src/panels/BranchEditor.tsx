// Редактор веток по времени регистрации (branch_router): название, первый шаг, время эфира,
// сдвиг повтора и окна «регистрация с–по → сегодня / завтра / дата». Сверху – шкала суток:
// видно, кому достаётся каждая минута, где дыры и пересечения.
import { ArrowDown, ArrowUp, CircleAlert, Plus, X } from "lucide-react";
import { useState } from "react";
import { branchProblems, fmtMin, newBranch, repeatPhrase, segments } from "../model/branches";
import * as ops from "../model/ops";
import type { Branch, BranchWindow, Funnel } from "../model/types";
import { branchLong } from "../simple/labels";
import { useSimple } from "../simple/mode";
import { stepRef } from "../simple/names";
import { useStudio } from "../store";
import { IntInput, Section } from "./ui";

const COLORS = ["#4f8cf7", "#f59f45", "#3fb27f", "#b26ee8", "#e0567a", "#6b7078"];
const DAY: { value: BranchWindow["day"]; label: string }[] = [
  { value: "today", label: "эфир сегодня" },
  { value: "tomorrow", label: "эфир завтра" },
  { value: "fixed", label: "эфир в дату" },
];

function Timeline({ funnel, name }: { funnel: Funnel; name: (id: string) => string }) {
  const r = funnel.branch_router!;
  const color = (id: string) => COLORS[Math.max(0, r.branches.findIndex((b) => b.id === id)) % COLORS.length];
  return (
    <div className="br-timeline" data-testid="branch-timeline">
      <div className="br-bar">
        {segments(r).map((s) => {
          const w = ((s.to - s.from + 1) / 1440) * 100;
          const title = `${fmtMin(s.from)}–${fmtMin(s.to)}: ${s.owners.length ? s.owners.map(name).join(" + ") : "ни одной ветки"}`;
          const cls = !s.owners.length ? "br-seg br-gap" : s.owners.length > 1 ? "br-seg br-over" : "br-seg";
          return <div key={s.from} className={cls} style={{ width: `${w}%`, background: s.owners.length === 1 ? color(s.owners[0]) : undefined }} title={title} />;
        })}
      </div>
      <div className="br-ticks">{[0, 6, 12, 18, 24].map((h) => <span key={h} style={{ left: `${(h / 24) * 100}%` }}>{String(h).padStart(2, "0")}:00</span>)}</div>
      <div className="br-legend">
        {r.branches.map((b) => <span key={b.id}><i style={{ background: color(b.id) }} />{name(b.id)}</span>)}
        <span><i className="br-gap" />никуда</span>
        <span><i className="br-over" />две ветки</span>
      </div>
    </div>
  );
}

function BranchCard({ b, i, funnel, name }: { b: Branch; i: number; funnel: Funnel; name: (id: string) => string }) {
  const apply = useStudio((s) => s.apply);
  const simple = useSimple();
  const r = funnel.branch_router!;
  const [id, setId] = useState(b.id);
  const patch = (p: Partial<Branch>, co?: string) =>
    apply((g) => ops.updateBranch(g, { branchId: b.id, patch: p }), co ? { coalesce: `br:${b.id}:${co}` } : undefined);
  const setWin = (j: number, w: Partial<BranchWindow>) => patch({ windows: b.windows.map((x, k) => (k === j ? { ...x, ...w } : x)) });
  const probs = branchProblems(funnel, name).filter((p) => p.branchId === b.id);
  const hours = Math.floor(b.repeat_offset_minutes / 60);
  const mins = b.repeat_offset_minutes % 60;
  return (
    <div className="br-card" data-testid="branch-card" style={{ borderLeftColor: COLORS[i % COLORS.length] }}>
      <div className="row">
        <b className="grow">{branchLong(b, r.branches)}</b>
        <button type="button" className="icon" title="выше – при пересечении окон побеждает верхняя" onClick={() => apply((g) => ops.moveBranch(g, { branchId: b.id, delta: -1 }))} aria-label="Выше"><ArrowUp size={14} strokeWidth={2} /></button>
        <button type="button" className="icon" title="ниже" onClick={() => apply((g) => ops.moveBranch(g, { branchId: b.id, delta: 1 }))} aria-label="Ниже"><ArrowDown size={14} strokeWidth={2} /></button>
        <button type="button" className="icon x" title="удалить ветку" onClick={() => {
          if (window.confirm(r.branches.length === 1 ? "Это последняя ветка – ветки выключатся совсем. Продолжить?" : `Удалить ветку «${name(b.id)}»?`)) {
            if (apply((g) => ops.removeBranch(g, { branchId: b.id })) && useStudio.getState().branch === b.id) {
              useStudio.getState().set({ branch: useStudio.getState().funnel?.branch_router?.branches[0]?.id ?? null });
            }
          }
        }} aria-label="Удалить"><X size={15} strokeWidth={2} /></button>
      </div>
      <div className="br-grid">
        <span className="field-label">название</span>
        <div className="row">
          <input className="inp mono" style={{ width: 90 }} value={id} data-testid="branch-id" onChange={(e) => setId(e.target.value)}
            onBlur={() => {
              const next = id.trim();
              if (!next || next === b.id) { setId(b.id); return; }
              if (!apply((g) => ops.updateBranch(g, { branchId: b.id, patch: { id: next } }))) setId(b.id);
              else if (useStudio.getState().branch === b.id) useStudio.getState().set({ branch: next });
            }} />
          {simple && <span className="muted small">служебное имя – для людей ветка называется по времени эфира</span>}
        </div>
        <span className="field-label">первый шаг</span>
        <select className="inp" value={b.entry_step} data-testid="branch-entry" onChange={(e) => patch({ entry_step: e.target.value })}>
          {!funnel.steps.some((s) => s.id === b.entry_step) && <option value={b.entry_step}>(шаг удалён – выберите другой)</option>}
          {funnel.steps.map((s) => <option key={s.id} value={s.id}>{stepRef(s, simple)}</option>)}
        </select>
        <span className="field-label">эфир в</span>
        <input className="inp" type="time" value={b.webinar_time} data-testid="branch-time" onChange={(e) => e.target.value && patch({ webinar_time: e.target.value.slice(0, 5) }, "time")} />
        <span className="field-label">повтор</span>
        <div className="row">
          <span>через</span>
          <IntInput value={hours} min={0} width={52} onChange={(v) => v !== null && patch({ repeat_offset_minutes: v * 60 + mins }, "rep")} testId="branch-repeat-h" />
          <span>ч</span>
          <IntInput value={mins} min={0} width={52} onChange={(v) => v !== null && v < 60 && patch({ repeat_offset_minutes: hours * 60 + v }, "rep")} />
          <span>мин после эфира</span>
          <span className="muted small">– {repeatPhrase(b)}</span>
        </div>
      </div>
      <div className="br-wins">
        <div className="field-label">кто регистрируется</div>
        {b.windows.map((w, j) => (
          <div className="br-win" key={j} data-testid="branch-window">
            <div className="row">
              <span>с</span>
              <input className="inp" type="time" value={w.from} onChange={(e) => e.target.value && setWin(j, { from: e.target.value.slice(0, 5) })} data-testid="win-from" />
              <span>по</span>
              <input className="inp" type="time" value={w.to} onChange={(e) => e.target.value && setWin(j, { to: e.target.value.slice(0, 5) })} data-testid="win-to" />
              <span className="grow" />
              <button type="button" className="x" aria-label="Убрать окно" title="убрать окно" onClick={() => patch({ windows: b.windows.filter((_, k) => k !== j) })}><X size={15} strokeWidth={2} /></button>
            </div>
            <div className="row">
              <span>→</span>
              <select className="inp" value={w.day} onChange={(e) => {
                const day = e.target.value as BranchWindow["day"];
                setWin(j, { day, date: day === "fixed" ? (w.date ?? new Date().toISOString().slice(0, 10)) : null });
              }} data-testid="win-day">
                {DAY.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
              {w.day === "fixed" && <input className="inp" type="date" value={w.date ?? ""} onChange={(e) => e.target.value && setWin(j, { date: e.target.value })} />}
            </div>
          </div>
        ))}
        <button type="button" className="add" data-testid="add-window" onClick={() => {
          // новое окно – первая «дыра» в сутках, если она есть
          const gap = segments(r).find((x) => !x.owners.length);
          const w = gap ? { from: fmtMin(gap.from), to: fmtMin(gap.to) } : { from: "12:00", to: "23:59" };
          patch({ windows: [...b.windows, { ...w, day: "today", date: null }] });
        }}><Plus size={15} strokeWidth={2} />Окно регистрации</button>
      </div>
      {probs.map((p) => <div key={p.text} className={p.level === "error" ? "err-box" : "warn-box"}><CircleAlert size={14} strokeWidth={2} />{p.text}</div>)}
    </div>
  );
}

export function BranchEditor({ funnel }: { funnel: Funnel }) {
  const apply = useStudio((s) => s.apply);
  const simple = useSimple();
  const r = funnel.branch_router;
  const first = funnel.steps[0]?.id;
  const name = (id: string) => {
    const b = r?.branches.find((x) => x.id === id);
    return simple && b ? `«${branchLong(b, r!.branches).replace(" ветка · эфир", ",")}»` : `«${id}»`;
  };
  const common = r ? branchProblems(funnel, name).filter((p) => !p.branchId) : [];
  return (
    <Section title={`Ветки по времени регистрации${r ? ` (${r.branches.length})` : ""}`} open={false} testId="sec-branches"
      hint="в какое время человек зарегистрировался → когда у него эфир">
      {!r && (
        <>
          <div className="muted">Веток нет: у всех один и тот же эфир из настроек запуска.</div>
          {first && <button type="button" data-testid="enable-branches" onClick={() => {
            const b = newBranch(null, first);
            if (apply((g) => ops.addBranch(g, { branch: b }))) useStudio.getState().set({ branch: b.id });
          }}>Включить ветки по времени регистрации</button>}
        </>
      )}
      {r && <Timeline funnel={funnel} name={name} />}
      {common.map((p) => <div key={p.text} className="err-box" data-testid="branch-problem"><CircleAlert size={14} strokeWidth={2} />{p.text}</div>)}
      {r?.branches.map((b, i) => <BranchCard key={b.id + i} b={b} i={i} funnel={funnel} name={name} />)}
      {r && first && (
        <button type="button" className="add" data-testid="add-branch" onClick={() => apply((g) => ops.addBranch(g, { branch: newBranch(g.branch_router, g.branch_router?.branches[0]?.entry_step ?? first) }))}>＋ ветка</button>
      )}
      {r && <div className="field-hint">Окна проверяются по порядку: при пересечении побеждает верхняя ветка. Время – по Москве, «по» включительно.</div>}
    </Section>
  );
}
