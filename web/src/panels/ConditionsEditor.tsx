// Условия (гейты) – выпадающий список из §4.5 исследования. Шаг УХОДИТ, только если условие
// выполнено; иначе пропускается, и цепочка идёт дальше по стрелке «по времени».
import { Plus, TriangleAlert, X } from "lucide-react";
import { useEffect, useState } from "react";
import * as ops from "../model/ops";
import type { Conditions, Funnel, Step } from "../model/types";
import { useStudio } from "../store";
import { Field, IntInput, TextInput } from "./ui";

interface Gate {
  id: string;
  label: string;
  hint: string;
  active: (c: Conditions) => boolean;
  on: Partial<Conditions>;
  off: Partial<Conditions>;
  /** доп. поле: зал эфира, префикс метки ссылки (какого из двух полей) или окна часов регистрации */
  extra?: "room" | "no_link_click" | "link_click" | "windows";
}

const ROOM_OFF: Partial<Conditions> = { attendance: null, bizon_room: null, bizon_room_max: "", gate_window_hours: 4 };

export const GATES: Gate[] = [
  { id: "was", label: "был на эфире", hint: "по отчёту Bizon зала этого дня; данные – только после эфира", active: (c) => c.attendance === "was", on: { attendance: "was" }, off: ROOM_OFF, extra: "room" },
  { id: "was_not", label: "не был на эфире", hint: "по отчёту Bizon зала этого дня; ставить после конца эфира", active: (c) => c.attendance === "was_not", on: { attendance: "was_not" }, off: ROOM_OFF, extra: "room" },
  { id: "not_paid", label: "не оплатил ВИП", hint: "нужен настроенный vip_sync этого запуска", active: (c) => c.not_paid, on: { not_paid: true }, off: { not_paid: false } },
  { id: "paid", label: "оплатил ВИП", hint: "только купившим ВИП", active: (c) => c.paid, on: { paid: true }, off: { paid: false } },
  { id: "not_course_paid", label: "не купил курс", hint: "", active: (c) => c.not_course_paid, on: { not_course_paid: true }, off: { not_course_paid: false } },
  { id: "course_paid", label: "купил курс", hint: "", active: (c) => c.course_paid, on: { course_paid: true }, off: { course_paid: false } },
  { id: "not_joined", label: "не вступил в канал", hint: "бот должен быть админом канала; в MAX/VK проверка не работает", active: (c) => c.channel === "not_joined", on: { channel: "not_joined" }, off: { channel: null } },
  { id: "joined", label: "вступил в канал", hint: "бот должен быть админом канала", active: (c) => c.channel === "joined", on: { channel: "joined" }, off: { channel: null } },
  { id: "survey_done", label: "не прошёл опрос", hint: "прошедшим опрос шаг не уходит", active: (c) => c.survey_done, on: { survey_done: true }, off: { survey_done: false } },
  { id: "has_lm", label: "есть лидмагнит", hint: "только пришедшим по посту с лидмагнитом", active: (c) => c.has_lm, on: { has_lm: true }, off: { has_lm: false } },
  { id: "no_link_click", label: "не кликнул ссылку", hint: "в MAX честный клик – только по кнопке; t.me не считается", active: (c) => !!c.no_link_click, on: { no_link_click: "link" }, off: { no_link_click: "" }, extra: "no_link_click" },
  { id: "link_click", label: "кликнул ссылку", hint: "только в движке Студии: в yaml bot-engine этого условия нет. В MAX клик – только по кнопке; без включённых ссылок движка шаг не уйдёт никому", active: (c) => !!c.link_click, on: { link_click: "link" }, off: { link_click: "" }, extra: "link_click" },
  { id: "no_manager_dm", label: "не написал менеджеру", hint: "при сбое проверки шаг отправится (fail-open)", active: (c) => c.no_manager_dm, on: { no_manager_dm: true }, off: { no_manager_dm: false } },
  { id: "max_subscription_gate", label: "подписан на канал в MAX", hint: "MAX: подарок только подписчику", active: (c) => c.max_subscription_gate, on: { max_subscription_gate: true }, off: { max_subscription_gate: false } },
  { id: "registered_windows", label: "зарегистрировался не в эти часы", hint: "время регистрации – когда человеку ушло первое сообщение воронки (повторный /start считается заново). Не нашли его – шаг отправится", active: (c) => (c.registered_windows?.length ?? 0) > 0, on: { registered_windows: ["11:00-12:00"] }, off: { registered_windows: [] }, extra: "windows" },
];

/** «11:00-12:00» → ["11:00", "12:00"]; конец «24:00» в поле времени – «00:00». */
function splitWindow(w: string): [string, string] {
  const [a = "", b = ""] = w.split("-");
  return [a, b === "24:00" ? "00:00" : b];
}

function joinWindow(a: string, b: string): string {
  return `${a}-${b === "00:00" ? "24:00" : b}`;
}

/** Окна часов регистрации (МСК): «с [11:00] до [12:00]». В модель уходят только правильные окна –
 *  неправильное остаётся в полях с подсказкой, что не так. */
function WindowsEditor({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [rows, setRows] = useState<[string, string][]>(() => value.map(splitWindow));
  useEffect(() => {
    const cur = rows.map(([a, b]) => joinWindow(a, b));
    if (cur.join() !== value.join() && rows.every(([a, b]) => !ops.windowProblem(joinWindow(a, b)))) {
      setRows(value.map(splitWindow));
    }
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps
  const problems = rows.map(([a, b]) => ops.windowProblem(joinWindow(a, b)));
  const set = (next: [string, string][]) => {
    setRows(next);
    const ws = next.map(([a, b]) => joinWindow(a, b));
    if (ws.length && ws.every((w) => !ops.windowProblem(w))) onChange(ws);
  };
  const readable = value.map((w) => w.replace("-", "–")).join(", ");
  return (
    <div className="cond-extra" data-testid="reg-windows">
      {rows.map(([a, b], i) => (
        <div className="row" key={i}>
          <span className="muted">с</span>
          <input className="inp time-inp" type="time" value={a} aria-label="С какого времени" data-testid={`reg-win-from-${i}`}
            onChange={(e) => set(rows.map((r, j) => (j === i ? [e.target.value.slice(0, 5), r[1]] : r)))} />
          <span className="muted">до</span>
          <input className="inp time-inp" type="time" value={b} aria-label="До какого времени" data-testid={`reg-win-to-${i}`}
            onChange={(e) => set(rows.map((r, j) => (j === i ? [r[0], e.target.value.slice(0, 5)] : r)))} />
          <span className="faint">МСК</span>
          {rows.length > 1 && (
            <button type="button" className="x" aria-label="Убрать окно" data-tip="Убрать окно"
              onClick={() => set(rows.filter((_, j) => j !== i))}><X size={14} strokeWidth={2} /></button>
          )}
          {problems[i] && <div className="field-hint warn-text">{problems[i]}</div>}
        </div>
      ))}
      <button type="button" className="ghost" data-testid="reg-win-add" onClick={() => set([...rows, ["16:00", "17:00"]])}>
        <Plus size={14} strokeWidth={2} />ещё окно
      </button>
      <div className="field-hint" data-testid="reg-windows-phrase">
        Не отправлять тем, кто зарегистрировался {value.length > 1 ? "в эти часы" : "в это время"}: {readable} (по Москве). Начало входит, конец – нет;
        окно через полночь – двумя окнами, «до 00:00» – до конца суток.
      </div>
    </div>
  );
}

export function ConditionsEditor({ step, funnel }: { step: Step; funnel: Funnel }) {
  const apply = useStudio((s) => s.apply);
  const c = step.conditions;
  const active = GATES.filter((g) => g.active(c));
  const free = GATES.filter((g) => !g.active(c) && !(g.id === "was" && c.attendance) && !(g.id === "was_not" && c.attendance)
    && !(g.id === "joined" && c.channel) && !(g.id === "not_joined" && c.channel));
  const patch = (p: Partial<Conditions>, co?: string) =>
    apply((g) => ops.setConditions(g, { stepId: step.id, patch: p }), co ? { coalesce: `${co}:${step.id}` } : undefined);
  const alsoFire = funnel.edges.some((e) => e.from_step === step.id && e.kind === "also_fire");

  return (
    <div className="conds" data-testid="conditions">
      {!active.length && <div className="faint small">Без условий – шаг уходит всем.</div>}
      {active.map((g) => (
        <div className="cond" key={g.id}>
          <div className="cond-head">
            <span>шаг уходит, только если <b>{g.label}</b></span>
            <button type="button" className="x" aria-label="Убрать условие" data-tip="Убрать условие" onClick={() => patch(g.off)}><X size={15} strokeWidth={2} /></button>
          </div>
          {g.hint && <div className="field-hint">{g.hint}</div>}
          {g.extra === "room" && (
            <div className="cond-extra">
              <Field label="зал Bizon этого дня" hint="пусто – зал запуска">
                <TextInput value={c.bizon_room ?? ""} onChange={(v) => patch({ bizon_room: v || null }, "room")} mono />
              </Field>
              <Field label="MAX-зал" hint="если у MAX отдельная комната">
                <TextInput value={c.bizon_room_max} onChange={(v) => patch({ bizon_room_max: v }, "roommax")} mono />
              </Field>
              <Field label="окно проверки, ч">
                <IntInput value={c.gate_window_hours} min={0} onChange={(v) => v !== null && patch({ gate_window_hours: v }, "win")} />
              </Field>
            </div>
          )}
          {g.extra === "no_link_click" && (
            <Field label="префикс метки ссылки" hint="шаг не уйдёт тем, кто кликнул ссылку с такой меткой">
              <TextInput value={c.no_link_click} onChange={(v) => patch({ no_link_click: v || "link" }, "link")} mono />
            </Field>
          )}
          {g.extra === "windows" && (
            <WindowsEditor value={c.registered_windows ?? []} onChange={(v) => patch({ registered_windows: v }, "regwin")} />
          )}
          {g.extra === "link_click" && (
            <Field label="префикс метки ссылки" hint="шаг уйдёт только тем, кто кликнул ссылку с такой меткой (кнопка шага – funnel_<шаг>)">
              <TextInput value={c.link_click} onChange={(v) => patch({ link_click: v || "link" }, "linkpos")} mono />
            </Field>
          )}
        </div>
      ))}
      {alsoFire && active.length > 0 && (
        <div className="warn-box"><TriangleAlert size={14} strokeWidth={2} />У шага есть параллельные стрелки: если шаг пропустится по условию, они не сработают (правило 41).</div>
      )}
      <select className="inp" value="" data-testid="add-condition" onChange={(e) => {
        const g = GATES.find((x) => x.id === e.target.value);
        if (g) patch(g.on);
      }}>
        <option value="">Добавить условие…</option>
        {free.map((g) => <option key={g.id} value={g.id}>{g.label}</option>)}
      </select>
    </div>
  );
}
