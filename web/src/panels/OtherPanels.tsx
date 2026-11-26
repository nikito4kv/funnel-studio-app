// Панели стрелки, заметки/правки и воронки (когда ничего не выделено) + диалог «удалить шаг».
import { ArrowRight, CircleAlert, CircleCheck, LayoutGrid, NotebookPen, Plus, StickyNote, TriangleAlert, X } from "lucide-react";
import { useMemo, useState } from "react";
import { chipFor } from "../canvas/flow";
import { StepIcon } from "../design/icons";
import { findEntries, incomingMap } from "../model/graphinfo";
import { issueCounts, validate } from "../model/validate";
import * as ops from "../model/ops";
import { DELAY_KINDS, EDGE_KIND_LABEL, kindAllowed, REVISION_ACTION_LABEL, REVISION_STATUS_LABEL } from "../model/ops";
import type { Edge, EdgeKind, Funnel, Note, RevisionAction, RevisionStatus, Step } from "../model/types";
import { edgePhrase } from "../phrases";
import { newId, useStudio } from "../store";
import { DurationInput } from "./DurationInput";
import { BranchEditor } from "./BranchEditor";
import { EntryVariantsEditor } from "./VariantsEditor";
import { edgeKindLabel } from "../simple/labels";
import { useSimple } from "../simple/mode";
import { stepRef } from "../simple/names";
import { Check, Field, Section, TextArea, TextInput } from "./ui";

// ── стрелка ──

const DELAY_MEANING: Partial<Record<EdgeKind, string>> = {
  done: "через сколько после этого сообщения придёт следующее",
  also_fire: "параллельный таймер: через сколько после отправки этого шага",
  contact: "через сколько после того, как человек поделился телефоном",
  skip: "сколько ждать телефон/почту, прежде чем идти дальше",
  skip_if_phone_known: "через сколько, если телефон уже известен",
  email: "через сколько после того, как прислал почту",
  skip_if_email_known: "через сколько, если почта уже известна",
  chain_to_funnel: "общая с «по времени» задержка перехода",
};

export function EdgePanel({ edge, funnel }: { edge: Edge; funnel: Funnel }) {
  const simple = useSimple();
  const apply = useStudio((s) => s.apply);
  const select = useStudio((s) => s.select);
  const byId = new Map(funnel.steps.map((s) => [s.id, s]));
  const from = byId.get(edge.from_step)!;
  const to = edge.to_step ? byId.get(edge.to_step) : null;
  const kinds = (Object.keys(EDGE_KIND_LABEL) as EdgeKind[]).filter((k) => k !== "chain_to_funnel" && k !== "jump" && (k === edge.kind || kindAllowed(from, k)));
  return (
    <div className="panel-body" data-testid="edge-panel">
      <div className="panel-title"><span>Стрелка</span><span className="muted">{edgePhrase(edge, from, simple)}</span></div>
      <div className="edge-ends">
        <button type="button" className="linkish" onClick={() => select({ steps: [from.id], edge: null })}>«{stepRef(from, simple)}»</button>
        <ArrowRight size={15} strokeWidth={2} className={edge.kind === "also_fire" ? "dashed-arrow" : undefined} />
        {to ? <button type="button" className="linkish" onClick={() => select({ steps: [to.id], edge: null })}>«{stepRef(to, simple)}»</button>
          : <span>воронка «{edge.to_funnel}»</span>}
      </div>
      {edge.kind !== "chain_to_funnel" && edge.kind !== "jump" && (
        <Section title="Вид">
          <select className="inp" value={edge.kind} data-testid="edge-kind" onChange={(e) => apply((g) => ops.setEdgeKind(g, { edgeId: edge.id, kind: e.target.value as EdgeKind }))}>
            {kinds.map((k) => <option key={k} value={k}>{simple ? edgeKindLabel(k, true) : EDGE_KIND_LABEL[k]}</option>)}
          </select>
          {edge.kind === "also_fire" && <div className="field-hint">{simple
            ? "Пунктир: придёт одновременно с основной цепочкой, по своему таймеру. Если у шага есть «только если…» и он пропустится – эта стрелка не сработает."
            : "Пунктир: сработает вместе с основной цепочкой, по своему таймеру. У шага с условием теряется при пропуске."}</div>}
        </Section>
      )}
      {DELAY_KINDS.has(edge.kind) && (
        <Section title="Задержка" testId="sec-delay">
          <div className="field-hint">{DELAY_MEANING[edge.kind]}</div>
          <DurationInput testId="edge-delay" value={edge.delay_seconds} allowNull={edge.kind === "skip"} nullLabel="без таймаута"
            onChange={(v) => apply((g) => ops.setEdgeDelay(g, { edgeId: edge.id, delay: v }), { coalesce: `delay:${edge.id}` })} />
          {to && (to.schedule.absolute_time_msk || to.schedule.webinar_offset_minutes !== null) && (
            <div className="warn-box">У «{stepRef(to, simple)}» своё время отправки – оно перебивает задержку стрелки.</div>
          )}
        </Section>
      )}
      {edge.kind === "chain_to_funnel" && (
        <Section title="Переход в воронку">
          <Field label="воронка"><TextInput value={edge.to_funnel ?? ""} mono onChange={(v) => v && apply((g) => ops.setEdgeFunnel(g, { edgeId: edge.id, toFunnel: v }), { coalesce: `chainf:${edge.id}` })} /></Field>
          <Check checked={edge.typing_before} onChange={(v) => apply((g) => ops.setEdgeFunnel(g, { edgeId: edge.id, toFunnel: edge.to_funnel!, typing: v }))} label="«печатает…» в ожидании" />
        </Section>
      )}
      <div className="panel-actions">
        <button type="button" className="danger" onClick={() => {
          if (apply((g) => ops.removeEdge(g, { edgeId: edge.id }))) select({ edge: null });
        }}>Удалить стрелку</button>
      </div>
    </div>
  );
}

// ── заметка / правка ──

const NEXT_STATUS: Record<RevisionStatus, RevisionStatus[]> = {
  open: ["done", "rejected"], done: ["accepted", "rejected", "open"], accepted: ["open"], rejected: ["open"],
};
const COLORS = ["#fff6b6", "#d9f2ff", "#e3f7d4", "#ffe0f0", "#ececec"];

export function NotePanel({ note, funnel }: { note: Note; funnel: Funnel }) {
  const simple = useSimple();
  const apply = useStudio((s) => s.apply);
  const select = useStudio((s) => s.select);
  const step = note.step_id ? funnel.steps.find((s) => s.id === note.step_id) : null;
  const patch = (p: Parameters<typeof ops.updateNote>[1]["patch"], co?: string) =>
    apply((g) => ops.updateNote(g, { noteId: note.id, patch: p }), co ? { coalesce: `${co}:${note.id}` } : undefined);
  return (
    <div className="panel-body" data-testid="note-panel">
      <div className="panel-title">
        <span>{note.kind === "revision" ? <><NotebookPen size={18} strokeWidth={1.75} />Правка {note.number}</> : <><StickyNote size={18} strokeWidth={1.75} />Заметка</>}</span>
        <span className="muted">в бот не уходит</span>
      </div>
      <TextArea rows={4} value={note.text} onChange={(v) => patch({ text: v }, "text")} />
      {note.kind === "revision" ? (
        <>
          <Field label="действие">
            <select className="inp" value={note.action ?? "other"} onChange={(e) => patch({ action: e.target.value as RevisionAction })}>
              {(Object.keys(REVISION_ACTION_LABEL) as RevisionAction[]).map((a) => <option key={a} value={a}>{REVISION_ACTION_LABEL[a]}</option>)}
            </select>
          </Field>
          <Field label={`статус: ${REVISION_STATUS_LABEL[note.status ?? "open"]}`}>
            <div className="row">
              {NEXT_STATUS[note.status ?? "open"].map((s) => (
                <button type="button" key={s} onClick={() => apply((g) => ops.setRevisionStatus(g, { noteId: note.id, status: s }))}>
                  → {REVISION_STATUS_LABEL[s]}
                </button>
              ))}
            </div>
            <span className="field-hint">«сделана» ставится сама, когда карточку правки изменили</span>
          </Field>
        </>
      ) : (
        <div className="row">
          {COLORS.map((c) => <button type="button" key={c} className={note.color === c ? "swatch on" : "swatch"} style={{ background: c }} onClick={() => patch({ color: c })} />)}
        </div>
      )}
      <Field label="автор"><TextInput value={note.author} onChange={(v) => patch({ author: v }, "author")} /></Field>
      {step && (
        <div className="row">
          <span className="muted">Приколота к «{stepRef(step, simple)}»</span>
          <button type="button" onClick={() => {
            const p = funnel.layout.positions[step.id] ?? { x: 0, y: 0 };
            patch({ step_id: null, position: { x: p.x + 360, y: p.y - 40 } });
          }}>Открепить</button>
        </div>
      )}
      {note.created_at && <div className="muted small">создана {new Date(note.created_at).toLocaleString("ru-RU")}</div>}
      <div className="panel-actions">
        <button type="button" className="danger" onClick={() => { if (apply((g) => ops.removeNote(g, { noteId: note.id }))) select({ note: null }); }}>Удалить</button>
      </div>
    </div>
  );
}

// ── воронка (ничего не выделено): оглавление, а не настройки ──

/** Шаги по порядку чтения: по полосам сверху вниз, внутри полосы – слева направо. */
function readingOrder(f: Funnel): { lane: string | null; steps: Step[] }[] {
  const pos = f.layout.positions;
  const lanes = [...f.lanes].sort((a, b) => (pos[a.id]?.y ?? a.order * 1e5) - (pos[b.id]?.y ?? b.order * 1e5));
  const byX = (a: Step, b: Step) => (pos[a.id]?.x ?? 0) - (pos[b.id]?.x ?? 0) || (pos[a.id]?.y ?? 0) - (pos[b.id]?.y ?? 0);
  const inLane = new Set(lanes.flatMap((l) => l.step_ids));
  const groups = lanes.map((l) => ({ lane: l.title as string | null, steps: f.steps.filter((s) => l.step_ids.includes(s.id)).sort(byX) }));
  const rest = f.steps.filter((s) => !inLane.has(s.id)).sort(byX);
  if (rest.length) groups.push({ lane: lanes.length ? "Без полосы" : null, steps: rest });
  return groups.filter((g) => g.steps.length);
}

/** Короткая целая фраза для оглавления: «сразу», «через 5 с», «в 09:50», «после ответа».
 *  Полная – в подсказке. */
export function shortChip(p: string): string {
  let s = p.split(" / ")[0].trim();
  s = s.replace(/^через сразу/, "сразу").replace(/^сразу при входе$/, "сразу");
  if (s.startsWith("нажал")) return "по кнопке";
  if (/^(ответил|дал телефон|дал номер|телефон известен|номер уже есть|прислал почту|почта известна|почта уже есть)/.test(s)) return "после ответа";
  if (/^не дал/.test(s)) return "если не ответил";
  // стрелка «вместе» (also_fire): коротко – только задержка, кто с кем – в подсказке
  if (s.startsWith("одновременно") || s.startsWith("параллельно")) {
    const m = s.match(/через (.+)$/);
    s = m ? `через ${m[1]}` : "сразу";
  }
  s = s.split(" · ")[0];
  s = s.replace(/^в день регистрации в /, "в ").replace(/^на следующий день после регистрации в (.+)$/, "на след. день в $1")
    .replace(/^через (\d+) дн\. после регистрации в (.+)$/, "день $1, $2");
  return s.replace(/ сек$/, " с").replace(/, день эфира$/, "").replace(/ после входа$/, "");
}

function Contents({ funnel }: { funnel: Funnel }) {
  const simple = useSimple();
  const branch = useStudio((s) => s.branch);
  const focusStep = useStudio((s) => s.focusStep);
  const groups = useMemo(() => readingOrder(funnel), [funnel]);
  const chips = useMemo(() => {
    const incoming = incomingMap(funnel);
    const entries = findEntries(funnel);
    const byId = new Map(funnel.steps.map((s) => [s.id, s]));
    return new Map(funnel.steps.map((s) => [s.id, chipFor(s, funnel, branch, incoming, entries, byId, simple)]));
  }, [funnel, branch, simple]);
  if (!funnel.steps.length) return null;
  let n = 0;
  return (
    <div className="contents" data-testid="funnel-contents">
      {groups.map((g, gi) => (
        <div className="contents-group" key={gi}>
          {g.lane && <div className="contents-lane">{g.lane}</div>}
          {g.steps.map((s) => (
            <button type="button" key={s.id} className="contents-row" onClick={() => focusStep(s.id)}>
              <span className="contents-n">{++n}</span>
              <StepIcon type={s.type} survey={!!s.survey.qid} size={14} />
              <span className="contents-name">{stepRef(s, simple)}</span>
              {chips.get(s.id) && <span className="contents-time" title={chips.get(s.id)!}>{shortChip(chips.get(s.id)!)}</span>}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

export function FunnelPanel({ funnel }: { funnel: Funnel }) {
  const simple = useSimple();
  const apply = useStudio((s) => s.apply);
  const set = useStudio((s) => s.set);
  const relayout = useStudio((s) => s.requestRelayout);
  const [newLane, setNewLane] = useState("");
  const issues = useMemo(() => validate(funnel), [funnel]);
  const { errors, warns } = issueCounts(issues);
  const lanes = [...funnel.lanes].sort((a, b) => (funnel.layout.positions[a.id]?.y ?? a.order * 1000) - (funnel.layout.positions[b.id]?.y ?? b.order * 1000));
  const addLane = () => {
    if (!newLane.trim()) return;
    const bottoms = funnel.steps.map((s) => (funnel.layout.positions[s.id]?.y ?? 0) + 400);
    const tops = funnel.lanes.map((l) => (funnel.layout.positions[l.id]?.y ?? 0) + 300);
    const y = Math.max(0, ...bottoms, ...tops) + 80;
    if (apply((g) => ops.addLane(g, { id: newId(), title: newLane.trim(), y }))) setNewLane("");
  };
  return (
    <div className="panel-body" data-testid="funnel-panel">
      <div className="panel-title"><span>{funnel.title || "Воронка"}</span>{!simple && <span className="mono muted">{funnel.id}</span>}</div>
      <div className="panel-sub">{simple ? "" : `${funnel.source?.path || "создана в Студии"} · `}{funnel.steps.length} шагов · клик по карточке – её настройки</div>
      {funnel.steps.length > 0 && (errors + warns ? (
        <button type="button" className={"health " + (errors ? "health-err" : "health-warn")} onClick={() => set({ rightTab: "check" })} data-testid="funnel-health">
          {errors ? <CircleAlert size={16} strokeWidth={2} /> : <TriangleAlert size={16} strokeWidth={2} />}
          <span className="grow">{errors + warns} {plural(errors + warns, "вещь", "вещи", "вещей")} стоит поправить</span>
          <span className="faint">Проверка</span>
        </button>
      ) : (
        <div className="health health-ok" data-testid="funnel-health"><CircleCheck size={16} strokeWidth={2} /><span>Замечаний нет</span></div>
      ))}
      {funnel.steps.length > 0 && <div className="sec-group-title">Сообщения по порядку</div>}
      <Contents funnel={funnel} />

      <div className="sec-group-title">Настройки воронки</div>
      <BranchEditor funnel={funnel} />
      <EntryVariantsEditor funnel={funnel} />
      <Section title={`Полосы (${funnel.lanes.length})`} testId="sec-lanes" open={false}>
        {lanes.map((l) => (
          <div className="row lane-row" key={l.id}>
            <input className="inp grow" value={l.title} aria-label="Название полосы" onChange={(e) => e.target.value.trim() && apply((g) => ops.updateLane(g, { laneId: l.id, patch: { title: e.target.value } }), { coalesce: `lane:${l.id}` })} />
            <span className="faint small">{l.step_ids.length}</span>
            <button type="button" className="x" aria-label="Удалить полосу" data-tip="Удалить полосу (карточки останутся)" onClick={() => apply((g) => ops.removeLane(g, { laneId: l.id }))}><X size={15} strokeWidth={2} /></button>
          </div>
        ))}
        <div className="row">
          <input className="inp grow" placeholder="Новая полоса, например «День 2»" value={newLane} onChange={(e) => setNewLane(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addLane()} />
          <button type="button" onClick={addLane} aria-label="Добавить полосу"><Plus size={15} strokeWidth={2} /></button>
        </div>
        <div className="field-hint">Карточка принадлежит полосе, в которую её положили.</div>
        <button type="button" className="ghost" onClick={() => {
          if (window.confirm("Разложить все карточки заново? Ваша раскладка пропадёт (вернуть – Ctrl+Z).")) relayout();
        }}><LayoutGrid size={14} strokeWidth={1.75} />Разложить заново…</button>
      </Section>
      <Section title="Прочее" open={false}>
        {!simple && <Field label="id воронки"><TextInput value={funnel.id} mono onChange={(v) => v && apply((g) => ops.setFunnelMeta(g, { id: v }), { coalesce: "fid" })} /></Field>}
        <Field label="Название (для себя)"><TextInput value={funnel.title} onChange={(v) => apply((g) => ops.setFunnelMeta(g, { title: v }), { coalesce: "ftitle" })} /></Field>
        <Check checked={funnel.max_video_note_caption} onChange={(v) => apply((g) => ops.setFunnelMeta(g, { max_video_note_caption: v }))}
          label="в MAX кружок уходит с подписью" hint="выключите, если в кружке говорят то же, что в тексте (§25)" />
        <Check checked={funnel.resend_entry_on_repeat} onChange={(v) => apply((g) => ops.setFunnelMeta(g, { resend_entry_on_repeat: v }))}
          label="повторный /start – прислать первое сообщение ещё раз"
          hint="если человек жмёт /start сразу после входа, бот по умолчанию молчит, чтобы не сбить цепочку. Включите для ботов-выдач: первое сообщение придёт ещё раз, остальное не тронется" />
        {funnel.air_email_profile && <div className="muted">профиль писем: {funnel.air_email_profile}</div>}
      </Section>
    </div>
  );
}

function plural(n: number, one: string, few: string, many: string): string {
  const d = n % 10;
  const h = n % 100;
  if (d === 1 && h !== 11) return one;
  if (d >= 2 && d <= 4 && (h < 12 || h > 14)) return few;
  return many;
}

// ── удалить шаг, у которого есть люди / входящие стрелки ──

export function RemoveDialog({ stepId, funnel }: { stepId: string; funnel: Funnel }) {
  const simple = useSimple();
  const apply = useStudio((s) => s.apply);
  const set = useStudio((s) => s.set);
  const select = useStudio((s) => s.select);
  const step = funnel.steps.find((s) => s.id === stepId);
  const done = funnel.edges.find((e) => e.from_step === stepId && e.to_step && e.kind !== "also_fire");
  const [target, setTarget] = useState(done?.to_step ?? "");
  if (!step) return null;
  const blockers = ops.removeBlockers(funnel, stepId);
  const close = () => set({ removeDialog: null });
  return (
    <div className="modal-back" onClick={close}>
      <div className="modal small-modal" onClick={(e) => e.stopPropagation()}>
        <h3>{simple ? "Убрать" : "Снять"} шаг «{stepRef(step, simple)}»</h3>
        <p>Насовсем удалить нельзя: {blockers.join("; ")}.</p>
        {simple
          ? <p>Шаг уберём, а люди, которые его уже ждут, пойдут дальше – в шаг, который вы выберете.</p>
          : <p>Шаг станет <b>переходом</b>: key останется, люди с таймером на него пойдут дальше по стрелке «переход».</p>}
        <Field label="куда перенаправить людей">
          <select className="inp" value={target} onChange={(e) => setTarget(e.target.value)} data-testid="redirect-target">
            <option value="">выберите шаг…</option>
            {funnel.steps.filter((s) => s.id !== stepId).map((s) => <option key={s.id} value={s.id}>{simple ? stepRef(s, true) : `${s.key}${s.title ? ` – ${s.title}` : ""}`}</option>)}
          </select>
        </Field>
        <div className="row end">
          <button type="button" onClick={close}>Отмена</button>
          <button type="button" className="danger" disabled={!target} onClick={() => {
            if (apply((g) => ops.removeStep(g, { stepId, redirectTo: target, edgeId: newId() }))) {
              close();
              select({ steps: [stepId], edge: null, note: null });
            }
          }}>{simple ? "Убрать и перенаправить" : "Снять и перенаправить"}</button>
        </div>
      </div>
    </div>
  );
}
