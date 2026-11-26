// Вкладка «▶ Симулятор»: что и когда получит человек и что у него выпадет.
// Считает сервер (studio/sim – правила движка), здесь – параметры, лента по дням и «все ветки».
import { Ban, CircleDot, CircleX, Copy, CornerDownRight, CornerUpRight, Dot, Hourglass, type LucideIcon, Mail, MessageSquare, Moon, PhoneCall, Play, Redo2, RefreshCw, RotateCcw, Scissors, Send, Square, Timer, TriangleAlert, User } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { Funnel } from "../model/types";
import { groupByDay, iconOf, reasonRu, toneOf, uniqueDropped, webinarDays, type Filter } from "../sim/format";
import { useSim, type DndMode } from "../sim/simStore";
import type { Persona, SimEvent } from "../sim/types";
import "../sim/sim.css";
import { useStudio } from "../store";
import { useSimple } from "../simple/mode";
import { humanizeTokens } from "../simple/labels";
import { MatrixDialog } from "../sim/MatrixDialog";
import { Check, Section, Seg } from "./ui";

const QUICK = ["09:00", "12:30", "17:30", "23:30"];

function Mins({ value, onChange, unit = "мин", testId }: { value: number; onChange: (v: number) => void; unit?: string; testId?: string }) {
  return (
    <span className="sim-after">
      через
      <input className="inp num sim-num" inputMode="numeric" value={String(value)} data-testid={testId}
        onChange={(e) => { const n = Number(e.target.value.replace(/\D/g, "") || "0"); onChange(n); }} />
      {unit}
    </span>
  );
}

type FlagKey = "vip_paid" | "course_paid" | "channel_joined" | "link_clicked" | "manager_dm";
const FLAGS: { key: FlagKey; label: string; hint: string }[] = [
  { key: "vip_paid", label: "Оплатил ВИП", hint: "гасит дожимы ВИПа, открывает шаги «только оплатившим»" },
  { key: "course_paid", label: "Купил курс", hint: "условия «не купил / купил курс»" },
  { key: "channel_joined", label: "Вступил в канал", hint: "условия «в канале / не в канале»" },
  { key: "link_clicked", label: "Кликнул ссылку", hint: "условия «кликнул / не кликнул ссылку»" },
  { key: "manager_dm", label: "Написал менеджеру", hint: "условие «не написал менеджеру» (если настроено в запуске)" },
];

function PersonaEditor({ days }: { days: number }) {
  const p = useSim((s) => s.persona);
  const set = useSim((s) => s.setPersona);
  const reset = useSim((s) => s.resetPersona);
  const toggleDay = (n: number) => set({
    attended_days: p.attended_days.includes(n) ? p.attended_days.filter((d) => d !== n) : [...p.attended_days, n].sort(),
  });
  return (
    <div className="sim-persona" data-testid="sim-persona">
      <div className="sim-sub">Кто он</div>
      <div className="sim-days">
        <span className="muted">Был на эфире:</span>
        {Array.from({ length: days }, (_, i) => i + 1).map((n) => (
          <label key={n} className={"sim-day" + (p.attended_days.includes(n) ? " on" : "")}>
            <input type="checkbox" checked={p.attended_days.includes(n)} onChange={() => toggleDay(n)}
              data-testid={`sim-air-${n}`} />
            {days === 1 ? "да" : `день ${n}`}
          </label>
        ))}
      </div>
      {FLAGS.map((f) => {
        const afterKey = `${f.key}_after_min` as keyof Persona;
        return (
          <div className="sim-line" key={f.key}>
            <Check checked={p[f.key]} onChange={(v) => set({ [f.key]: v } as Partial<Persona>)} label={f.label} hint={f.hint} />
            {p[f.key] && (
              <Mins value={p[afterKey] as number} onChange={(v) => set({ [afterKey]: v } as Partial<Persona>)} />
            )}
          </div>
        );
      })}
      <div className="sim-grid">
        <Check checked={p.phone_known} onChange={(v) => set({ phone_known: v })} label="Телефон уже известен" />
        <Check checked={p.email_known} onChange={(v) => set({ email_known: v })} label="Почта уже известна" />
        <Check checked={p.survey_done} onChange={(v) => set({ survey_done: v })} label="Опрос пройден заранее" />
        <Check checked={p.has_lm} onChange={(v) => set({ has_lm: v })} label="Пришёл с лидмагнитом" />
      </div>
      <div className="sim-sub">Что делает</div>
      <div className="sim-line">
        <Check checked={p.contact === "given"} onChange={(v) => set({ contact: v ? "given" : "no" })} label="Даёт контакт" />
        {p.contact === "given" && <Mins value={p.contact_after_s} unit="с" onChange={(v) => set({ contact_after_s: v })} />}
      </div>
      <div className="sim-line">
        <Check checked={p.claim} onChange={(v) => set({ claim: v })} label="Жмёт кнопки-действия" hint="«Забрать», «Смотреть», «Пройти опрос»" />
        {p.claim && <Mins value={p.claim_after_s} unit="с" onChange={(v) => set({ claim_after_s: v })} />}
      </div>
      <div className="sim-line">
        <Check checked={p.survey_answer} onChange={(v) => set({ survey_answer: v })} label="Отвечает на опрос" />
        {p.survey_answer && <Mins value={p.survey_after_s} unit="с" onChange={(v) => set({ survey_after_s: v })} />}
      </div>
      <div className="sim-line">
        <Check checked={p.email} onChange={(v) => set({ email: v })} label="Присылает почту" />
        {p.email && <Mins value={p.email_after_s} unit="с" onChange={(v) => set({ email_after_s: v })} />}
      </div>
      <button type="button" className="linkish small" onClick={reset}>вернуть «типового человека»</button>
    </div>
  );
}

// значки ленты: в модели (sim/format.ts, тесты) – символы, на экране – иконки lucide
const EV_ICON: Record<string, LucideIcon> = {
  "▶": Play, "✉": Mail, "⛔": Ban, "✖": CircleX, "✂": Scissors, "↻": RefreshCw, "⧉": Copy, "⌛": Hourglass, "⏳": Hourglass,
  "👤": User, "⏱": Timer, "■": Square, "↪": CornerUpRight, "⚠": TriangleAlert, "⤳": CornerDownRight, "↷": Redo2,
  "↺": RotateCcw, "🌙": Moon, "◉": CircleDot, "☎": PhoneCall, "💬": MessageSquare,
};

function EventRow({ e }: { e: SimEvent }) {
  const focusStep = useStudio((s) => s.focusStep);
  const simple = useSimple();
  const Ic = EV_ICON[iconOf(e)] ?? Dot;
  const tone = toneOf(e);
  const target = e.step_id;
  return (
    <button type="button" className={`sim-ev t-${tone}`} disabled={!target} data-testid="sim-event"
      data-kind={e.kind} data-step={target ?? ""} onClick={() => target && focusStep(target)}
      title={e.by ? `пришло из «${e.by}»` : undefined}>
      <span className="sim-time">{e.time}</span>
      <span className="sim-ico"><Ic size={13} strokeWidth={2} /></span>
      <span className="sim-what">
        {e.key && !simple && <span className="sim-key">{e.key}</span>}
        {e.title && <span className="sim-title">{simple ? humanizeTokens(e.title) : e.title}</span>}
        <span className="sim-text">{simple ? humanizeTokens(e.text) : e.text}</span>
      </span>
    </button>
  );
}

export function SimulatorPanel({ funnel }: { funnel: Funnel }) {
  const st = useSim();
  const headerPlatform = useStudio((s) => s.platform);
  const toast = useStudio((s) => s.toast);
  const focusStep = useStudio((s) => s.focusStep);
  const [filter, setFilter] = useState<Filter>("all");
  const days = useMemo(() => webinarDays(funnel.steps.map((s) => ({
    day_offset: s.schedule.day_offset, attendance: !!s.conditions.attendance, webinar: s.schedule.webinar_offset_minutes !== null,
  }))), [funnel]);
  const groups = useMemo(() => (st.result ? groupByDay(st.result, filter) : []), [st.result, filter]);
  const dropped = st.result ? uniqueDropped(st.result.summary.dropped) : [];

  useEffect(() => { void st.loadTestrun(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { st.setParams({ platform: headerPlatform }); }, [headerPlatform]); // eslint-disable-line react-hooks/exhaustive-deps

  const r = st.result;
  const setTime = (hm: string) => st.setParams({ registeredAt: `${st.registeredAt.slice(0, 10)}T${hm}` });
  return (
    <div className="panel-body sim" data-testid="sim-panel">
      <div className="panel-title"><span><Play size={18} strokeWidth={1.75} />Симулятор</span><span className="muted">что и когда получит человек</span></div>

      <div className="sim-params">
        <label className="field">
          <span className="field-label">Регистрация (МСК)</span>
          <input className="inp" type="datetime-local" value={st.registeredAt} data-testid="sim-when"
            onChange={(e) => e.target.value && st.setParams({ registeredAt: e.target.value.slice(0, 16) })} />
        </label>
        <div className="quick">
          {QUICK.map((q) => (
            <button type="button" key={q} className={st.registeredAt.endsWith(q) ? "q-on" : ""} onClick={() => setTime(q)}>{q}</button>
          ))}
        </div>
        <div className="row">
          <Seg value={st.platform} testId="sim-platform" onChange={(v) => st.setParams({ platform: v })}
            options={[{ value: "tg", label: "TG" }, { value: "max", label: "MAX" }]} />
          <Seg<DndMode> value={st.dnd} onChange={(v) => st.setParams({ dnd: v })} options={[
            { value: "launch", label: "ночь: как в запуске", title: "ночной режим из launch.yaml запуска" },
            { value: "on", label: "вкл" }, { value: "off", label: "выкл" }]} />
        </div>
      </div>

      <Section title="Человек" hint="по умолчанию – типовой" open={false} testId="sim-persona-sec">
        <PersonaEditor days={days} />
      </Section>

      <div className="row">
        <button type="button" className="primary" onClick={() => void st.run()} disabled={st.loading} data-testid="sim-run">
          <Play size={14} strokeWidth={2} />{st.loading ? "Считаю…" : "Прогнать"}
        </button>
        <button type="button" onClick={() => void st.runMatrix()} disabled={st.matrixLoading} data-testid="sim-matrix"
          title="прогон по типовым моментам регистрации: кто что теряет">
          {st.matrixLoading ? "Считаю ветки…" : "Все ветки"}
        </button>
        <button type="button" onClick={st.clear} disabled={!r} data-testid="sim-clear" title="убрать отметки с холста">Очистить</button>
        <span className="grow" />
        <button type="button" disabled={!st.testrun?.ready || st.testrunBusy} data-testid="sim-testrun"
          title={st.testrun?.ready ? "отправить воронку себе через тестового бота, пауза 1,5 с между сообщениями" : st.testrun?.hint || "нужен тестовый бот"}
          onClick={() => { st.sendTestrun().then((m) => toast(m, "ok")).catch((e) => toast((e as Error).message, "error")); }}>
          <Send size={14} strokeWidth={1.75} />{st.testrunBusy ? "Шлю…" : "Тест в TG"}
        </button>
      </div>
      {st.testrun && !st.testrun.ready && <div className="field-hint">Тест в Telegram: {st.testrun.hint}</div>}
      {st.error && <div className="err-box">{st.error}</div>}

      {r && (
        <>
          <div className="sim-sum" data-testid="sim-summary">
            <div className="sim-branch">
              {r.branch?.id ? <>ветка <b>{r.branch.id}</b> · эфир {r.branch.webinar_label}</> : (r.branch?.note ?? "без веток")}
              <span className="muted"> · {r.platform === "tg" ? "Telegram" : "MAX"}</span>
            </div>
            <div className="sim-nums">
              <span className="n-ok"><Mail size={12} strokeWidth={2} />{r.summary.sent}</span>
              {r.summary.late > 0 && <span className="n-late"><Hourglass size={12} strokeWidth={2} />с опозданием {r.summary.late}</span>}
              {r.summary.dnd > 0 && <span className="n-late"><Moon size={12} strokeWidth={2} />ночью сдвинуто {r.summary.dnd}</span>}
              <span className={dropped.length ? "n-drop" : "n-zero"}><CircleX size={12} strokeWidth={2} />выпало {dropped.length}</span>
              <span className="n-gate"><Ban size={12} strokeWidth={2} />по условию {r.summary.gated.length}</span>
              {r.summary.replaced > 0 && <span className="n-late"><RefreshCw size={12} strokeWidth={2} />перезаписано {r.summary.replaced}</span>}
            </div>
            {dropped.length > 0 && (
              <div className="sim-drops">
                {dropped.map((d, i) => (
                  <button type="button" key={i} className="sim-drop" onClick={() => d.step_id && focusStep(d.step_id)} data-testid="sim-drop">
                    <b>{d.key}</b> – {reasonRu(d.reason)} <span className="muted">({d.at.slice(0, 11)})</span>
                  </button>
                ))}
              </div>
            )}
            {r.summary.stuck.map((s, i) => <div key={i} className="warn-box">{s}</div>)}
            {r.summary.truncated && <div className="warn-box">{r.summary.truncated}</div>}
            <details className="sim-launch">
              <summary>настройки запуска{r.launch.slug ? ` «${r.launch.slug}»` : ""}</summary>
              <ul>{r.launch.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
            </details>
          </div>

          <Seg<Filter> value={filter} onChange={setFilter} testId="sim-filter" options={[
            { value: "all", label: "всё" }, { value: "messages", label: "сообщения" }, { value: "problems", label: "потери и сдвиги" }]} />

          <div className="sim-feed" data-testid="sim-feed">
            {groups.map((g) => (
              <div className="sim-dayg" key={g.day}>
                <div className="sim-dayh">
                  <b>{g.title}</b> <span className="muted">{g.date}</span>
                  {g.airs.length > 0 && <span className="sim-airs">{g.airs.join(" · ")}</span>}
                </div>
                {g.events.map((e, i) => <EventRow e={e} key={i} />)}
              </div>
            ))}
            {!groups.length && <div className="muted">Под фильтр ничего не попало.</div>}
          </div>
        </>
      )}
      {!r && !st.error && (
        <div className="field-hint">
          Прогон считает по правилам движка: время от эфира и по часам, «если время прошло», ночной режим, условия,
          параллельные стрелки, кнопки и ожидание контакта. После прогона карточки на холсте получают отметку
          «уйдёт в …», выпавшие – красную пунктирную рамку.
        </div>
      )}
      <MatrixDialog />
    </div>
  );
}
