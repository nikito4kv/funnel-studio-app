// Панель свойств шага: тип, когда, условие, текст и кнопки по платформам, медиа,
// настройки типа (контакт/почта/опрос/звонок/SMS), переходы, заметки, «расширенные».
import { Flag, NotebookPen, Plus, Smartphone, StickyNote, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { plainLabel, StepIcon } from "../design/icons";
import { findEntries } from "../model/graphinfo";
import * as ops from "../model/ops";
import { EDGE_KIND_LABEL, STEP_TYPE_LABEL } from "../model/ops";
import type { Button, Funnel, Platform, Step, StepType } from "../model/types";
import { textLimit } from "../model/validate";
import { duration } from "../phrases";
import { safeHtml } from "../render/safeHtml";
import { newId, useStudio } from "../store";
import { ButtonsEditor } from "./ButtonsEditor";
import { ConditionsEditor } from "./ConditionsEditor";
import { DurationInput } from "./DurationInput";
import { MediaEditor } from "./MediaEditor";
import { TextEditor } from "./TextEditor";
import { TimeConstructor } from "./TimeConstructor";
import { VariantsEditor } from "./VariantsEditor";
import { PhonePreview } from "../preview/PhonePreview";
import { edgeKindLabel, stepTypeLabel, WORDS } from "../simple/labels";
import { useSimple } from "../simple/mode";
import { stepName, stepRef } from "../simple/names";
import { MiroStepInfo } from "../miro/MiroStepInfo";
import { Check, Field, IntInput, Section, Seg, TextArea, TextInput } from "./ui";

const TYPES: StepType[] = ["photo", "text", "video_note", "phone_request", "email_request", "video", "animation", "call", "sms", "rich", "jump", "air_pin"];

type MaxMode = "inherit" | "set" | "hide";
const MAX_MODES: { value: MaxMode; label: string; title: string }[] = [
  { value: "inherit", label: "как в TG", title: "в MAX уйдёт то же, что в Telegram" },
  { value: "set", label: "свой", title: "в MAX – своя версия" },
  { value: "hide", label: "скрыть", title: "в MAX этого нет" },
];

function maxModeOf<T>(v: T | null, empty: (v: T) => boolean): MaxMode {
  if (v === null) return "inherit";
  return empty(v) ? "hide" : "set";
}

function PlatformTabs() {
  const platform = useStudio((s) => s.platform);
  const set = useStudio((s) => s.set);
  return (
    <Seg<Platform> value={platform} onChange={(p) => set({ platform: p })} testId="panel-platform" options={[
      { value: "tg", label: "Telegram" }, { value: "max", label: "MAX" },
    ]} />
  );
}

function ContentSection({ step, funnel, part }: { step: Step; funnel: Funnel; part: "text" | "buttons" }) {
  const simple = useSimple();
  const apply = useStudio((s) => s.apply);
  const platform = useStudio((s) => s.platform);
  const { limit, caption } = textLimit(step);
  const circle = step.type === "video_note";
  const tg = step.content.tg;
  const mx = step.content.max;
  const setText = (p: Platform, mode: MaxMode | "set", value?: string) =>
    apply((g) => ops.setText(g, { stepId: step.id, platform: p, mode, value }), { coalesce: `text:${step.id}:${p}` });
  const setButtons = (p: Platform, mode: MaxMode | "set", buttons?: Button[]) =>
    apply((g) => ops.setButtons(g, { stepId: step.id, platform: p, mode, buttons }), { coalesce: `btn:${step.id}:${p}` });
  const setClaim = (p: Platform, mode: MaxMode | "set", value?: string) =>
    apply((g) => ops.setClaim(g, { stepId: step.id, platform: p, mode, value }), { coalesce: `claim:${step.id}:${p}` });
  const docKey = `${step.id}:${platform}`;
  const survey = !!step.survey.qid;
  const hasCallback = funnel.edges.some((e) => e.from_step === step.id && e.kind === "callback");

  const textMode = maxModeOf(mx.text, (v) => v === "");
  const btnMode = maxModeOf(mx.buttons, (v) => v.length === 0);
  const claimMode = maxModeOf(mx.claim_button_text, (v) => v === "");

  if (part === "text") return (
      <Section title={circle ? "Что говорит спикер" : "Текст"} testId="sec-text"
        hint={circle ? "что говорит спикер; в TG текст не отправляется" : caption ? "подпись к медиа" : undefined}>
        <PlatformTabs />
        {platform === "tg" ? (
          <TextEditor value={tg.text} docKey={docKey} onChange={(v) => setText("tg", "set", v)} testId="text-tg"
            limit={circle ? undefined : caption ? 1024 : limit} limitNote={caption ? "медиа и текст уйдут двумя сообщениями" : "Telegram не отправит"} />
        ) : (
          <div data-testid="max-text">
            <Seg<MaxMode> value={textMode} options={MAX_MODES} testId="max-text-mode" onChange={(m) => {
              if (m === "set") setText("max", "set", mx.text || tg.text);
              else setText("max", m);
            }} />
            {textMode === "inherit" && (
              <div className="inherit-box">
                <div className="muted">В MAX уйдёт тот же текст, что в Telegram:</div>
                <div className="mint preview" dangerouslySetInnerHTML={{ __html: safeHtml(tg.text) || "<i>пусто</i>" }} />
              </div>
            )}
            {textMode === "set" && (
              <TextEditor value={mx.text ?? ""} docKey={docKey} onChange={(v) => setText("max", "set", v)} limit={4000} limitNote="больше 4000 – MAX не отправит" />
            )}
            {textMode === "hide" && <div className="inherit-box muted">В MAX текст не отправляется{circle ? " – кружок уйдёт видео без подписи" : ""}.</div>}
            {circle && funnel.max_video_note_caption && textMode === "inherit" && tg.text && (
              <div className="warn-box">В MAX кружок уходит видео с подписью: человек услышит сценарий и прочитает его же. Обычно выбирают «скрыть».</div>
            )}
          </div>
        )}
      </Section>
  );
  const hasClaim = tg.claim_button_text !== null;
  return (
    <>
      {!circle && step.type !== "jump" && (
        <Section title={survey ? "Варианты ответа" : "Кнопки под сообщением"} testId="sec-buttons"
          hint={survey ? "человек выбирает ответ кнопкой" : undefined}>
          {platform === "tg" ? (
            <ButtonsEditor buttons={tg.buttons} survey={survey} onChange={(b) => setButtons("tg", "set", b)} />
          ) : (
            <>
              <Seg<MaxMode> value={btnMode} options={MAX_MODES} onChange={(m) => {
                if (m === "set") setButtons("max", "set", mx.buttons?.length ? mx.buttons : tg.buttons);
                else setButtons("max", m);
              }} />
              {btnMode === "inherit" && <div className="muted">{tg.buttons.length ? `как в TG: ${tg.buttons.map((b) => `«${b.text}»`).join(", ")}` : "в TG кнопок нет"}</div>}
              {btnMode === "set" && <ButtonsEditor buttons={mx.buttons ?? []} survey={survey} onChange={(b) => setButtons("max", "set", b)} />}
              {btnMode === "hide" && <div className="muted">В MAX кнопок нет.</div>}
            </>
          )}

          {(!simple || hasClaim) && <div className="claim">
            <div className="claim-head">{simple ? "Кнопка с продолжением" : "Кнопка-действие"} <span className="field-hint">{simple ? "нажал – бот идёт по синей стрелке от этой кнопки" : "нажатие ведёт по стрелке «нажал» (синяя точка у кнопки)"}</span></div>
            {platform === "tg" ? (
              tg.claim_button_text === null ? (
                <button type="button" className="add" onClick={() => setClaim("tg", "set", "✅ ЗАБРАТЬ")}><Plus size={15} strokeWidth={2} />Кнопка-действие</button>
              ) : (
                <div className="row">
                  <input className="inp grow" value={tg.claim_button_text} onChange={(e) => setClaim("tg", "set", e.target.value)} />
                  <button type="button" className="x" aria-label="Убрать" data-tip={hasCallback ? "Уберёт и стрелку «нажал»" : "Убрать"} onClick={() => setClaim("tg", "hide")}><X size={15} strokeWidth={2} /></button>
                </div>
              )
            ) : tg.claim_button_text !== null ? (
              <>
                <Seg<MaxMode> value={claimMode} options={MAX_MODES} onChange={(m) => setClaim("max", m, m === "set" ? (mx.claim_button_text || tg.claim_button_text!) : undefined)} />
                {claimMode === "set" && <input className="inp" value={mx.claim_button_text ?? ""} onChange={(e) => setClaim("max", "set", e.target.value)} />}
              </>
            ) : <div className="muted">Нет (добавляется на вкладке Telegram).</div>}
            {tg.claim_button_text !== null && (
              <div className="row">
                <Check checked={step.claim.keeps_chain} onChange={(v) => apply((g) => ops.setClaimSettings(g, { stepId: step.id, patch: { keeps_chain: v } }))}
                  label="нажатие не отменяет цепочку" hint="claim_keeps_chain: таймер «по времени» остаётся" />
              </div>
            )}
          </div>}
          {survey && step.survey.multi && (
            <Field label="кнопка «готово»">
              <TextInput value={step.survey.done_button} onChange={(v) => apply((g) => ops.setSurvey(g, { stepId: step.id, patch: { done_button: v } }), { coalesce: `done:${step.id}` })} />
            </Field>
          )}
        </Section>
      )}
    </>
  );
}

function TypeFields({ step }: { step: Step }) {
  const apply = useStudio((s) => s.apply);
  const id = step.id;
  const co = (k: string) => ({ coalesce: `${k}:${id}` });
  if (step.type === "phone_request") {
    const c = step.contact;
    const p = (patch: Partial<typeof c>, k = "contact") => apply((g) => ops.setContact(g, { stepId: id, patch }), co(k));
    return (
      <Section title="Запрос телефона" hint="выходы: дал / не дал / уже известен">
        <Field label="кнопка «поделиться контактом»"><TextInput value={c.contact_button} onChange={(v) => p({ contact_button: v }, "cb")} /></Field>
        <Check checked={c.skip_if_known} onChange={(v) => p({ skip_if_known: v })} label="если телефон уже известен – пропустить шаг" />
        <Check checked={c.always_send_text} onChange={(v) => p({ always_send_text: v })} label="всегда слать текст (даже если телефон известен)" />
        <Field label="текст, если телефон известен" hint="работает только вместе с «всегда слать текст» (правило 13)">
          <TextArea value={c.text_if_known} onChange={(v) => p({ text_if_known: v }, "tik")} />
        </Field>
        <Check checked={c.manual_phone_max} onChange={(v) => p({ manual_phone_max: v })} label="в MAX просить номер сообщением" hint="в MAX нет кнопки «поделиться контактом»" />
        {c.manual_phone_max && (
          <>
            <Field label="текст запроса в MAX"><TextArea value={c.manual_phone_max_text} onChange={(v) => p({ manual_phone_max_text: v }, "mpt")} /></Field>
            <Field label="если прислали не номер"><TextArea value={c.manual_phone_retry_text} onChange={(v) => p({ manual_phone_retry_text: v }, "mpr")} /></Field>
          </>
        )}
      </Section>
    );
  }
  if (step.type === "email_request") {
    const c = step.email;
    const p = (patch: Partial<typeof c>, k = "email") => apply((g) => ops.setEmail(g, { stepId: id, patch }), co(k));
    return (
      <Section title="Запрос почты" hint="выходы: прислал / не прислал / уже известна">
        <Check checked={c.skip_if_known} onChange={(v) => p({ skip_if_known: v })} label="если почта уже известна – пропустить шаг" />
        <Field label="текст, если почта известна"><TextArea value={c.text_if_known} onChange={(v) => p({ text_if_known: v }, "etik")} /></Field>
        <Field label="если прислали не почту"><TextArea value={c.retry_text} onChange={(v) => p({ retry_text: v }, "ert")} /></Field>
      </Section>
    );
  }
  if (step.type === "call") {
    const c = step.call;
    const p = (patch: Partial<typeof c>, k = "call") => apply((g) => ops.setCall(g, { stepId: id, patch }), co(k));
    return (
      <Section title="Автозвонок">
        <Field label="id записи в кабинете звонобота" hint="только id – ссылкой на mp3 звонобот отказывает (116 из 116)">
          <IntInput value={c.audio_id} allowNull min={1} width={140} onChange={(v) => p({ audio_id: v }, "aid")} />
        </Field>
        <Field label="текст для синтеза (если нет записи)"><TextArea value={c.text ?? ""} onChange={(v) => p({ text: v || null }, "ct")} /></Field>
      </Section>
    );
  }
  if (step.type === "sms") {
    const t = step.sms.text ?? "";
    return (
      <Section title="SMS">
        <TextArea rows={4} value={t} onChange={(v) => apply((g) => ops.setSms(g, { stepId: id, patch: { text: v || null } }), co("sms"))} />
        <div className="field-hint">{t.length} знаков · {"{stream_url}"} подставляется короткой ссылкой при отправке</div>
      </Section>
    );
  }
  if (step.survey.qid) {
    const s = step.survey;
    const p = (patch: Partial<typeof s>, k = "survey") => apply((g) => ops.setSurvey(g, { stepId: id, patch }), co(k));
    return (
      <Section title="Вопрос опроса" hint="выход: следующий вопрос">
        <Field label="id вопроса"><TextInput value={s.qid ?? ""} mono onChange={(v) => v && p({ qid: v }, "qid")} /></Field>
        <Check checked={s.multi} onChange={(v) => p({ multi: v })} label="можно выбрать несколько" />
        <Check checked={s.free} onChange={(v) => p({ free: v })} label="свободный ответ текстом" />
        <Check checked={s.final} onChange={(v) => p({ final: v })} label="последний вопрос" />
        <Check checked={s.edit} onChange={(v) => p({ edit: v })} label="следующий вопрос – правкой этого сообщения" />
      </Section>
    );
  }
  return null;
}

function Transitions({ step, funnel }: { step: Step; funnel: Funnel }) {
  const simple = useSimple();
  const apply = useStudio((s) => s.apply);
  const select = useStudio((s) => s.select);
  const out = funnel.edges.filter((e) => e.from_step === step.id);
  const byId = new Map(funnel.steps.map((s) => [s.id, s]));
  const chain = out.find((e) => e.kind === "chain_to_funnel");
  const [target, setTarget] = useState("");
  return (
    <Section title="Переходы" hint="стрелки из этой карточки" testId="sec-transitions">
      {!out.length && <div className="muted">Нет. Протяните стрелку от точки выхода внизу карточки.</div>}
      {out.map((e) => (
        <div className="out-edge" key={e.id}>
          <button type="button" className="linkish" onClick={() => select({ edge: e.id, steps: [], note: null })}>
            {simple ? edgeKindLabel(e.kind, true) : EDGE_KIND_LABEL[e.kind]} → {e.to_step ? `«${stepRef(byId.get(e.to_step), simple)}»` : `воронка «${e.to_funnel}»`}
          </button>
          <span className="muted">{e.delay_seconds !== null ? duration(e.delay_seconds) : ""}</span>
        </div>
      ))}
      {step.type !== "jump" && (
        <div className="row">
          <select className="inp grow" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">{WORDS.alsoFireAdd(simple)}</option>
            {funnel.steps.filter((s) => s.id !== step.id).map((s) => <option key={s.id} value={s.id}>{stepRef(s, simple)}</option>)}
          </select>
          <button type="button" disabled={!target} onClick={() => {
            const id = newId();
            if (apply((g) => ops.addEdge(g, { id, from: step.id, kind: "also_fire", to: target }))) {
              setTarget("");
              select({ edge: id, steps: [], note: null });
            }
          }} aria-label="Добавить"><Plus size={15} strokeWidth={2} /></button>
        </div>
      )}
      {step.type !== "jump" && (
        <Check checked={!!chain} label="потом перейти в другую воронку (только TG)" onChange={(on) => {
          if (on) apply((g) => ops.addEdge(g, { id: newId(), from: step.id, kind: "chain_to_funnel", toFunnel: "alt" }));
          else if (chain) apply((g) => ops.removeEdge(g, { edgeId: chain.id }));
        }} />
      )}
      {chain && (
        <div className="row">
          <Field label="воронка"><TextInput value={chain.to_funnel ?? ""} mono onChange={(v) => v && apply((g) => ops.setEdgeFunnel(g, { edgeId: chain.id, toFunnel: v }), { coalesce: `chainf:${chain.id}` })} /></Field>
          <Check checked={chain.typing_before} label="«печатает…» в ожидании" onChange={(v) => apply((g) => ops.setEdgeFunnel(g, { edgeId: chain.id, toFunnel: chain.to_funnel!, typing: v }))} />
        </div>
      )}
      {chain && (
        <Field label="через (общая задержка с «по времени»)">
          <DurationInput value={chain.delay_seconds} onChange={(v) => v !== null && apply((g) => ops.setEdgeDelay(g, { edgeId: chain.id, delay: v }), { coalesce: `delay:${chain.id}` })} />
        </Field>
      )}
    </Section>
  );
}

function NotesOfStep({ step, funnel }: { step: Step; funnel: Funnel }) {
  const apply = useStudio((s) => s.apply);
  const select = useStudio((s) => s.select);
  const set = useStudio((s) => s.set);
  const notes = funnel.notes.filter((n) => n.step_id === step.id);
  const add = (kind: "note" | "revision") => {
    const id = newId();
    const createdAt = new Date().toISOString();
    const ok = apply((g) => (kind === "revision"
      ? ops.addRevision(g, { id, text: "Что поправить", action: "replace_text", stepId: step.id, author: "я", createdAt })
      : ops.addNote(g, { id, text: "Заметка", stepId: step.id, author: "я", createdAt })));
    if (ok) {
      set({ showNotes: true });
      select({ note: id, steps: [], edge: null });
    }
  };
  return (
    <Section title={`Заметки и правки${notes.length ? ` (${notes.length})` : ""}`} open={notes.length > 0} hint="в бот не уходят">
      {notes.map((n) => (
        <button type="button" key={n.id} className="linkish note-link" onClick={() => select({ note: n.id, steps: [], edge: null })}>
          {n.kind === "revision" ? <><NotebookPen size={13} strokeWidth={1.75} />Правка {n.number}:</> : <StickyNote size={13} strokeWidth={1.75} />} {n.text.slice(0, 60)}
        </button>
      ))}
      <div className="row">
        <button type="button" onClick={() => add("note")}><StickyNote size={14} strokeWidth={1.75} />Заметка</button>
        <button type="button" onClick={() => add("revision")}><NotebookPen size={14} strokeWidth={1.75} />Правка</button>
      </div>
    </Section>
  );
}

function Advanced({ step }: { step: Step; funnel?: Funnel }) {
  const apply = useStudio((s) => s.apply);
  const [key, setKey] = useState(step.key);
  return (
    <Section title="Расширенные" open={false} testId="sec-advanced">
      <Field label="техническое имя (key)" hint="на нём держатся таймеры людей и метки кликов. Переименование безопасно для Студии (id не меняется), но в уже опубликованной воронке – только через переход (удалённый шаг)">
        <div className="row">
          <input className="inp mono grow" value={key} onChange={(e) => setKey(e.target.value)} />
          <button type="button" disabled={key === step.key} onClick={() => apply((g) => ops.setKey(g, { stepId: step.id, key }))}>Переименовать</button>
        </div>
      </Field>
      <Check checked={step.pin.pin} onChange={(v) => apply((g) => ops.setPin(g, { stepId: step.id, patch: { pin: v } }))} label="закрепить сообщение (только TG)" />
      <Check checked={step.pin.air_pin} onChange={(v) => apply((g) => ops.setPin(g, { stepId: step.id, patch: { air_pin: v } }))} label="закреп отсчёта до эфира" />
      <Field label="через N секунд после входа (after_start_seconds)">
        <IntInput value={step.schedule.after_start_seconds} allowNull min={0} onChange={(v) => apply((g) => ops.setSchedule(g, { stepId: step.id, patch: { after_start_seconds: v } }), { coalesce: `as:${step.id}` })} />
      </Field>
      <Field label="шаблон диплома">
        <TextInput value={step.diploma_template} mono onChange={(v) => apply((g) => ops.setMisc(g, { stepId: step.id, patch: { diploma_template: v } }), { coalesce: `dip:${step.id}` })} />
      </Field>
      {Object.keys(step.advanced).length > 0 && (
        <Field label="поля движка без места в модели"><pre className="json">{JSON.stringify(step.advanced, null, 1)}</pre></Field>
      )}
      <div className="muted mono small">id {step.id}</div>
    </Section>
  );
}

export function StepPanel({ step, funnel }: { step: Step; funnel: Funnel }) {
  const apply = useStudio((s) => s.apply);
  const set = useStudio((s) => s.set);
  const simple = useSimple();
  const [phone, setPhone] = useState(false);
  const jumpEdge = step.type === "jump" ? funnel.edges.find((e) => e.from_step === step.id && e.kind === "jump") : undefined;
  const jumpTo = jumpEdge?.to_step ? stepRef(funnel.steps.find((s) => s.id === jumpEdge.to_step), simple) : null;
  const launch = funnel.source?.launch ?? "";
  const losses = useMemo(() => new Map(TYPES.map((t) => [t, t === step.type ? [] : ops.typeChangeLosses(funnel, step.id, t)])), [funnel, step]);
  const changeType = (t: StepType) => {
    const lost = losses.get(t) ?? [];
    if (lost.length && !window.confirm(`Сменить тип на «${plainLabel(STEP_TYPE_LABEL[t])}»? Пропадёт: ${lost.join(", ")}.`)) return;
    apply((g) => ops.setType(g, { stepId: step.id, type: t }));
  };
  const t = step.type;
  const hasMedia = !["jump", "call", "sms", "air_pin"].includes(t);
  const hasContent = t !== "jump" && t !== "call" && t !== "sms";
  const mediaTitle = t === "video_note" ? "Кружок" : t === "video" ? "Видео" : t === "animation" ? "GIF" : t === "rich" ? "Статья" : "Картинка";
  const mediaOpen = t !== "text" || !!step.media.image;
  // типы, которые постановщику почти не нужны, – только если шаг уже такой
  const types = TYPES.filter((x) => !simple || x === t || !["rich", "jump", "air_pin"].includes(x));
  return (
    <div className="panel-body" data-testid="step-panel">
      <div className="step-head">
        <span className="step-head-icon"><StepIcon type={t} survey={!!step.survey.qid} size={18} /></span>
        <div className="step-head-text">
          <input className="inp title-inp" aria-label="Название карточки" data-tip="Название видно только вам и команде – в бот оно не уходит"
            placeholder={stepName(step, 40)} value={step.title}
            onChange={(e) => apply((g) => ops.setMisc(g, { stepId: step.id, patch: { title: e.target.value } }), { coalesce: `title:${step.id}` })} />
          <span className="step-head-type">
            {step.survey.qid ? "Вопрос опроса" : plainLabel(simple ? stepTypeLabel(t, true) : STEP_TYPE_LABEL[t])}
            {!simple && <span className="mono" data-testid="panel-key">{step.key}</span>}
          </span>
        </div>
      </div>
      {t !== "jump" && (
        <div className="step-actions">
          <button type="button" className="phone-btn" data-testid="phone-button" onClick={() => setPhone(true)}>
            <Smartphone size={15} strokeWidth={1.75} />Как в телефоне
          </button>
        </div>
      )}
      {phone && <PhonePreview step={step} funnel={funnel} onClose={() => setPhone(false)} />}
      <MiroStepInfo step={step} funnel={funnel} />
      {t === "jump" && <div className="info-box">{WORDS.tombstoneHint(simple, jumpTo)}</div>}

      {/* сверху вниз – как само сообщение: медиа → текст → кнопки → когда → при каком условии */}
      {hasMedia && (
        <Section title={mediaTitle} open={mediaOpen} testId="sec-media" hint={t === "text" && !step.media.image ? "не обязательно" : undefined}>
          <MediaEditor step={step} launch={launch} />
        </Section>
      )}
      {hasContent && <ContentSection step={step} funnel={funnel} part="text" />}
      {hasContent && <ContentSection step={step} funnel={funnel} part="buttons" />}
      <TypeFields step={step} />
      {t !== "jump" && (
        <Section title="Когда отправить" testId="sec-time">
          <TimeConstructor step={step} funnel={funnel} />
        </Section>
      )}
      {t !== "jump" && (
        <Section title={WORDS.conditionTitle(simple)} hint={WORDS.conditionHint(simple)} open={Object.values(step.conditions).some(Boolean)} testId="sec-conditions">
          <ConditionsEditor step={step} funnel={funnel} />
        </Section>
      )}

      <div className="sec-group-title">Ещё</div>
      <Section title="Тип шага" open={false} testId="sec-type">
        <select className="inp" value={t} onChange={(e) => changeType(e.target.value as StepType)} data-testid="type-select">
          {types.map((x) => <option key={x} value={x}>{plainLabel(stepTypeLabel(x, simple))}{losses.get(x)?.length ? " – часть настроек пропадёт" : ""}</option>)}
        </select>
        <span className="field-hint">Текст и картинку Студия переключает сама: положили картинку – шаг стал «с картинкой».</span>
      </Section>
      {!["jump", "call", "sms", "air_pin", "video_note"].includes(t) && <VariantsEditor step={step} />}
      {!simple && <Transitions step={step} funnel={funnel} />}
      <NotesOfStep step={step} funnel={funnel} />
      {!simple && <Advanced key={step.id + step.key} step={step} funnel={funnel} />}
      <div className="panel-actions">
        {!funnel.branch_router && !findEntries(funnel).has(step.id) && (
          <button type="button" className="ghost" onClick={() => apply((g) => ops.setStartStep(g, { stepId: step.id }))}>
            <Flag size={14} strokeWidth={1.75} />Сделать первым шагом
          </button>
        )}
        <span className="grow" />
        <button type="button" className="ghost danger-text" onClick={() => {
          const blockers = ops.removeBlockers(funnel, step.id);
          if (blockers.length) set({ removeDialog: step.id });
          else apply((g) => ops.removeStep(g, { stepId: step.id }));
        }}><Trash2 size={14} strokeWidth={1.75} />Удалить шаг</button>
      </div>
    </div>
  );
}
