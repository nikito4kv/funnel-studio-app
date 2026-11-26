// Карточки холста. Порядок сверху вниз – как читает человек: тип шага → название → картинка →
// текст (мятная подложка, как на досках) → кнопки под сообщением → когда и при каком условии
// уйдёт (чипы) → точки выхода. Кружок – круглая карточка.
//
// Стрелки цепляются к той стороне, которая смотрит на соседа (canvas/flow.ts): у карточки
// вход слева И сверху, основной выход снизу И справа. Остальные выходы – у своих подписей:
// кнопка-действие справа от кнопки, «дал / не дал / уже известен» у запроса контакта и почты.
import { Handle, Position, useReactFlow, type Node, type NodeProps } from "@xyflow/react";
import { MessageCircleWarning, NotebookPen, Paperclip, PencilLine, Play, Plus, ScanSearch, ShieldCheck, StickyNote } from "lucide-react";
import { addNext, freeExit } from "./addNext";
import { memo, useState } from "react";
import { mediaUrl } from "../api";
import { CircleCheckBig, Clock, CondIcon, plainLabel, StepIcon } from "../design/icons";
import { EDGE_KIND_LABEL, REVISION_ACTION_LABEL, REVISION_STATUS_LABEL, STEP_TYPE_LABEL } from "../model/ops";
import type { Button, EdgeKind, Note, Platform, Step } from "../model/types";
import { conditionBadges, duration } from "../phrases";
import { safeHtml } from "../render/safeHtml";
import type { SimMark } from "../sim/types";
import { CardLod } from "../nav/lod";
import { buttonTarget, exitLabel, stepTypeLabel, WORDS } from "../simple/labels";

export interface StepNodeData extends Record<string, unknown> {
  step: Step;
  chip: string | null;
  launch: string;
  platform: Platform;
  level: "error" | "warn" | null;
  notes: number;
  revisions: number;
  entry: boolean;
  used: EdgeKind[];
  /** Простой режим (постановщик): без key, плейсхолдеры словами. */
  simple?: boolean;
  /** Надгробие: имя шага, куда пойдут люди. */
  jumpTo?: string | null;
  sim: SimMark | null;
}

function SimBadge({ m }: { m: SimMark | null }) {
  if (!m) return null;
  const I = m.state === "sent" ? CircleCheckBig : m.state === "dropped" ? MessageCircleWarning : ShieldCheck;
  return <div className={`sim-badge s-${m.state}`} title={m.title} data-testid="sim-badge"><I size={13} strokeWidth={2} />{m.label}</div>;
}

export interface LaneNodeData extends Record<string, unknown> {
  title: string;
  count: number;
  width: number;
  height: number;
}

export interface NoteNodeData extends Record<string, unknown> {
  note: Note;
  pinnedTo: string | null;
}

export interface StartNodeData extends Record<string, unknown> {
  lines: string[];
}

export interface LabelNodeData extends Record<string, unknown> {
  label: string;
}

function fileName(ref: string): string {
  return ref.split("/").pop() ?? ref;
}

function MediaSlot({ refPath, launch, kind }: { refPath: string; launch: string; kind: "image" | "video" | "animation" | "rich" }) {
  const full = kind === "image" ? mediaUrl(refPath, launch) : null;
  // на карточке шириной 300 px хватает копии в 640 px (сервер отдаёт её по ?w=)
  const url = full && full.startsWith("files/") ? `${full}?w=640` : full;
  const [failed, setFailed] = useState<string | null>(null);
  return (
    <div className="media">
      {url && failed !== url ? (
        <img src={url} alt={fileName(refPath)} onError={() => setFailed(url)} draggable={false}
          loading="lazy" decoding="async" />
      ) : (
        <div className="media-ph">
          <StepIcon type={kind === "image" ? "photo" : kind} size={22} strokeWidth={1.5} />
          <span>{fileName(refPath)}</span>
        </div>
      )}
    </div>
  );
}

/** Что увидит человек на выбранной платформе: MAX null = как в TG, "" / [] = скрыто. */
export function effective(step: Step, platform: Platform) {
  const tg = step.content.tg;
  const mx = step.content.max;
  if (platform === "tg") return { text: tg.text, buttons: tg.buttons, claim: tg.claim_button_text, own: false };
  return {
    text: mx.text ?? tg.text,
    buttons: mx.buttons ?? tg.buttons,
    claim: mx.claim_button_text === null ? tg.claim_button_text : (mx.claim_button_text || null),
    own: mx.text !== null || mx.buttons !== null || mx.claim_button_text !== null,
  };
}

function Pills({ buttons, claim, claimHandle, simple }: { buttons: Button[]; claim: string | null; claimHandle: boolean; simple: boolean }) {
  if (!buttons.length && !claim && !claimHandle) return null;
  return (
    <div className="pills">
      {buttons.map((b, i) => {
        const target = buttonTarget(b.url, b.callback_data, b.web_app, simple);
        return (
          <div className="pill" key={i} title={b.url ?? b.callback_data ?? ""}>
            <span className="pill-text">{b.text || "…"}</span>
            {target && <span className="pill-url">{target}</span>}
          </div>
        );
      })}
      {(claim || claimHandle) && (
        <div className={claim ? "pill pill-claim" : "pill pill-claim pill-muted"} title="кнопка-действие: нажатие ведёт по стрелке «нажал»">
          <span className="pill-text">{claim ?? "кнопка-действие скрыта в MAX"}</span>
          {claimHandle && <Handle type="source" id="callback" position={Position.Right} className="h-claim" />}
        </div>
      )}
    </div>
  );
}

/** Когда уйдёт и при каком условии – чипы внизу карточки. */
function Meta({ step, chip }: { step: Step; chip: string | null }) {
  const badges = conditionBadges(step.conditions);
  const extra: string[] = [];
  if (step.pin.pin) extra.push("закрепить");
  if (step.schedule.dnd_exempt) extra.push("можно ночью");
  if (step.schedule.min_delay_seconds > 0 && (step.schedule.absolute_time_msk || step.schedule.webinar_offset_minutes !== null)) {
    extra.push(`не раньше чем через ${duration(step.schedule.min_delay_seconds)}`);
  }
  if (!step.schedule.skip_if_past && step.schedule.absolute_time_msk) extra.push("не пропускать прошедшее");
  if (!chip && !badges.length && !extra.length) return null;
  return (
    <div className="card-meta">
      {chip && <span className="chip chip-time" data-testid="time-chip"><Clock size={12} strokeWidth={2} />{chip}</span>}
      {badges.map((b, i) => (
        <span className="chip chip-cond" key={i} title="шаг уходит, только если…"><CondIcon emoji={b.icon} />{b.text}</span>
      ))}
      {extra.map((t) => <span className="chip" key={t}>{t}</span>)}
    </div>
  );
}

function PlatformTabs({ step, platform }: { step: Step; platform: Platform }) {
  const m = step.content.max;
  const differs = m.text !== null || m.buttons !== null || m.claim_button_text !== null;
  // TG и MAX одинаковы – метка не нужна (переключатель платформы – на панели холста)
  if (!differs) return null;
  return (
    <span className="tabs">
      <span className={platform === "tg" ? "tab tab-on" : "tab"}>TG</span>
      <span className={(differs ? "tab tab-diff" : "tab") + (platform === "max" ? " tab-on" : "")}
        title={differs ? "в MAX свой текст или кнопки" : "в MAX – как в TG"}>
        MAX{differs ? "≠" : ""}
      </span>
    </span>
  );
}

interface Exit {
  kind: EdgeKind;
  label: string;
}

export function exitsOf(step: Step): Exit[] {
  switch (step.type) {
    case "jump":
      return [{ kind: "jump", label: "переход" }];
    case "phone_request":
      return [
        { kind: "contact", label: "дал" },
        { kind: "skip", label: "не дал" },
        { kind: "skip_if_phone_known", label: "уже известен" },
      ];
    case "email_request":
      return [
        { kind: "email", label: "прислал" },
        { kind: "skip", label: "не прислал" },
        { kind: "skip_if_email_known", label: "уже известна" },
      ];
    default:
      if (step.survey.qid) return [{ kind: "survey_next", label: "следующий вопрос" }];
      return [{ kind: "done", label: "по времени" }];
  }
}

/** Точка выхода, из которой рисуется стрелка данного вида. */
export function handleOf(kind: EdgeKind): string {
  return kind === "also_fire" || kind === "chain_to_funnel" ? "done" : kind;
}

/** Суффикс второй (правой) точки основного выхода: «done-r», «survey_next-r», «jump-r». */
export const RIGHT = "-r";
/** Вторая точка входа – сверху. */
export const IN_TOP = "in-top";

/** Вид стрелки по точке, из которой её протянули мышью. */
export function kindOfHandle(handle: string | null | undefined): EdgeKind {
  const h = handle ?? "done";
  return (h.endsWith(RIGHT) ? h.slice(0, -RIGHT.length) : h) as EdgeKind;
}

function Exits({ step, used, simple }: { step: Step; used: EdgeKind[]; simple: boolean }) {
  const base = exitsOf(step);
  // стрелка вида, для которого у такого шага точки нет (редкие старые воронки), – всё равно видна
  for (const k of used) {
    const h = handleOf(k) as EdgeKind;
    if (h !== "callback" && !base.some((b) => b.kind === h)) base.push({ kind: h, label: EDGE_KIND_LABEL[h] });
  }
  const single = base.length === 1;
  return (
    <div className={single ? "exits single" : "exits"}>
      {base.map((x) => (
        <span className={used.some((k) => handleOf(k) === x.kind) ? "exit exit-used" : "exit"} key={x.kind}
          title={x.kind === "done" ? "протяните стрелку; с Alt – одновременную (пунктир)" : "протяните стрелку"}>
          <span className="exit-label">{exitLabel(x.kind, step.type, simple, x.label)}</span>
          <Handle type="source" id={x.kind} position={Position.Bottom} className="h-exit" />
        </span>
      ))}
    </div>
  );
}

/** Точки на сторонах карточки: вход слева и сверху, выходы ещё и справа. У запроса телефона и
 *  почты справа три точки одна под другой – «дал / не дал / уже есть» в том же порядке, что снизу. */
function SideHandles({ step }: { step: Step }) {
  const main = exitsOf(step);
  return (
    <>
      <Handle type="target" id="in" position={Position.Left} className="h-in" />
      <Handle type="target" id={IN_TOP} position={Position.Top} className="h-in-top" />
      {main.map((x, i) => (
        <Handle key={x.kind} type="source" id={x.kind + RIGHT} position={Position.Right} className="h-out-r"
          style={main.length > 1 ? { top: `${62 + i * 10}%` } : undefined} />
      ))}
    </>
  );
}

function Special({ step, platform, simple, jumpTo }: { step: Step; platform: Platform; simple: boolean; jumpTo: string | null }) {
  switch (step.type) {
    case "phone_request":
      return (
        <div className="pills">
          {platform === "max" && step.contact.manual_phone_max
            ? <div className="note-line">MAX: номер пишут сообщением</div>
            : <div className="pill pill-reply"><span className="pill-text">{step.contact.contact_button}</span></div>}
        </div>
      );
    case "email_request":
      return <div className="note-line">Ждём почту обычным сообщением</div>;
    case "call":
      return (
        <div className="mint">{step.call.audio_id ? `Запись №${step.call.audio_id}` : step.call.audio_url ?? step.call.text ?? "Звонок"}</div>
      );
    case "sms":
      return step.sms.text ? <div className="mint sms">{step.sms.text}</div> : <div className="note-line">SMS без текста</div>;
    case "jump":
      return <div className="note-line">{WORDS.tombstoneCard(simple, jumpTo).replace(/^⤳\s*/, "")}</div>;
    default:
      return null;
  }
}

function Marks({ d }: { d: StepNodeData }) {
  // импорт из Miro не уверен в этой карточке – «проверить N»
  const miro = d.step.miro?.doubts.filter((x) => x.level === "check").length ?? 0;
  if (!d.notes && !d.revisions && !d.entry && !miro) return null;
  return (
    <span className="marks">
      {miro > 0 && <span className="mark mark-miro" data-testid="miro-mark" title={`из Miro: проверить (${miro}) – подробности в панели шага`}><ScanSearch size={12} strokeWidth={2} />{miro}</span>}
      {d.entry && <span className="mark mark-entry" title="с этого шага начинается воронка"><Play size={10} strokeWidth={2.5} fill="currentColor" />вход</span>}
      {d.notes > 0 && <span className="mark" title="заметки к карточке"><StickyNote size={12} strokeWidth={2} />{d.notes}</span>}
      {d.revisions > 0 && <span className="mark mark-rev" title="правки к карточке"><PencilLine size={12} strokeWidth={2} />{d.revisions}</span>}
    </span>
  );
}

/** «＋ Следующее сообщение» под выбранной карточкой: новая карточка справа, сразу со стрелкой. */
function NextButton({ id, used }: { id: string; used: EdgeKind[] }) {
  const rf = useReactFlow();
  // used – чтобы кнопка пропадала, как только у карточки появилась стрелка «дальше»
  if (!used || !freeExit(id)) return null;
  return (
    <button type="button" className="next-btn nodrag nopan" data-testid="add-next"
      onClick={(e) => { e.stopPropagation(); addNext(id, { size: rf.getNode(id)?.measured as { width: number; height: number } | undefined }); }}>
      <Plus size={14} strokeWidth={2.25} />Следующее сообщение
    </button>
  );
}

function cls(base: string, d: StepNodeData, selected: boolean): string {
  return [base, selected ? "is-selected" : "", d.level ? `has-${d.level}` : "", d.sim ? `sim-${d.sim.state}` : ""].join(" ");
}

function typeName(s: Step, simple: boolean): string {
  return plainLabel(simple ? stepTypeLabel(s.type, true) : STEP_TYPE_LABEL[s.type]);
}

export const StepCard = memo(function StepCard({ data, selected }: NodeProps<Node<StepNodeData>>) {
  const s = data.step;
  const eff = effective(s, data.platform);
  const hiddenText = data.platform === "max" && s.content.max.text === "" && !!s.content.tg.text;
  const media = s.media.image ?? s.media.images?.[0] ?? null;
  const simple = !!data.simple;
  const survey = !!s.survey.qid;
  return (
    <div className={cls(`card card-${s.type}`, data, !!selected)}>
      <SideHandles step={s} />
      <div className="card-head">
        <SimBadge m={data.sim} />
        <span className="card-type"><StepIcon type={s.type} survey={survey} size={14} />{survey ? "Вопрос опроса" : typeName(s, simple)}</span>
        <Marks d={data} />
        <span className="grow" />
        <PlatformTabs step={s} platform={data.platform} />
      </div>
      {simple
        ? (s.title ? <div className="card-title">{s.title}</div> : null)
        : <div className="card-key">{s.title ? <span className="card-title">{s.title}</span> : null}<span className="mono">{s.key}</span></div>}
      {media && <MediaSlot refPath={media} launch={data.launch} kind="image" />}
      {s.media.video && <MediaSlot refPath={s.media.video} launch={data.launch} kind="video" />}
      {s.media.animation && <MediaSlot refPath={s.media.animation} launch={data.launch} kind="animation" />}
      {s.media.rich && <MediaSlot refPath={s.media.rich} launch={data.launch} kind="rich" />}
      {survey && (
        <div className="note-line">
          Вопрос {s.survey.qid}{s.survey.multi ? " · несколько ответов" : ""}{s.survey.free ? " · свободный ответ" : ""}{s.survey.final ? " · последний" : ""}
        </div>
      )}
      {eff.text && !hiddenText && <div className="mint text" dangerouslySetInnerHTML={{ __html: safeHtml(eff.text, simple) }} />}
      {hiddenText && <div className="note-line hidden-max">В MAX текст скрыт</div>}
      <Special step={s} platform={data.platform} simple={simple} jumpTo={data.jumpTo ?? null} />
      <Pills buttons={eff.buttons} claim={eff.claim} claimHandle={s.content.tg.claim_button_text !== null} simple={simple} />
      {survey && s.survey.multi && <div className="pills"><div className="pill pill-reply"><span className="pill-text">{s.survey.done_button}</span></div></div>}
      <Meta step={s} chip={data.chip} />
      <Exits step={s} used={data.used} simple={simple} />
      {selected && <NextButton id={s.id} used={data.used} />}
      <CardLod data={data} buttons={eff.buttons} />
    </div>
  );
});

export const CircleCard = memo(function CircleCard({ data, selected }: NodeProps<Node<StepNodeData>>) {
  const s = data.step;
  return (
    <div className={cls("circle-wrap", data, !!selected)}>
      <SideHandles step={s} />
      <div className="circle">
        <SimBadge m={data.sim} />
        <div className="circle-type"><StepIcon type="video_note" size={14} />Кружок</div>
        {data.simple ? (s.title ? <div className="circle-key">{s.title}</div> : null) : <div className="circle-key mono">{s.key}</div>}
        <div className="circle-file">{s.media.video_note ? <><Paperclip size={11} strokeWidth={2} />{fileName(s.media.video_note)}</> : "нет файла"}</div>
        {s.content.tg.text && <div className="circle-text" dangerouslySetInnerHTML={{ __html: safeHtml(s.content.tg.text, !!data.simple) }} />}
      </div>
      <Marks d={data} />
      <Meta step={s} chip={data.chip} />
      <Exits step={s} used={data.used} simple={!!data.simple} />
      {selected && <NextButton id={s.id} used={data.used} />}
      <CardLod data={data} buttons={[]} circle />
    </div>
  );
});

export function LaneNode({ data }: NodeProps<Node<LaneNodeData>>) {
  return (
    <div className="lane" style={{ width: data.width, height: data.height }}>
      <div className="lane-title">{data.title}<span className="lane-count">{data.count}</span></div>
    </div>
  );
}

export function NoteNode({ data, selected }: NodeProps<Node<NoteNodeData>>) {
  const n = data.note;
  if (n.kind === "revision") {
    return (
      <div className={`sticker revision st-${n.status}${selected ? " is-selected" : ""}`}>
        <div className="rev-head"><NotebookPen size={12} strokeWidth={2} />Правка {n.number}{n.action ? ` · ${REVISION_ACTION_LABEL[n.action]}` : ""}</div>
        <div className="sticker-text">{n.text}</div>
        <div className="rev-foot">
          <span className={`rev-status rs-${n.status}`}>{n.status ? REVISION_STATUS_LABEL[n.status] : ""}</span>
          {n.author && <span>· {n.author}</span>}
          {data.pinnedTo && <span>· на «{data.pinnedTo}»</span>}
        </div>
      </div>
    );
  }
  return (
    <div className={`sticker${selected ? " is-selected" : ""}`} style={{ background: n.color }}>
      <div className="sticker-text">{n.text}</div>
      {(n.author || data.pinnedTo) && <div className="rev-foot">{n.author}{data.pinnedTo ? ` · на «${data.pinnedTo}»` : ""}</div>}
    </div>
  );
}

export function StartNode({ data }: NodeProps<Node<StartNodeData>>) {
  return (
    <div className="start">
      <div className="start-title"><Play size={12} strokeWidth={2.5} fill="currentColor" />Вход в воронку</div>
      {data.lines.map((l, i) => <div className="start-line" key={i}>{l}</div>)}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}

export function LabelNode({ data }: NodeProps<Node<LabelNodeData>>) {
  return (
    <div className="label-node">
      <Handle type="target" id="in" position={Position.Left} isConnectable={false} />
      {data.label}
    </div>
  );
}

export const nodeTypes = {
  step: StepCard, circle: CircleCard, lane: LaneNode, note: NoteNode, start: StartNode, label: LabelNode,
};
