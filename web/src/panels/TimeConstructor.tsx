// «Когда отправить» – одна фраза-конструктор, время на часах, без минут с минусом.
//   после предыдущего – «через [1] [ч] после «…»» (задержка на входящей стрелке);
//   в определённое время – «[на следующий день ▾] в [11:00]»: дни считаются от эфира, а в
//                         воронке без эфира – от дня регистрации (так считает движок);
//   от эфира           – «[за ▾] [3 ч] [10 мин] до [эфира ▾]», для каждой ветки – время на часах,
//                         минуты до эфира Студия считает сама.
// Внизу – та же фраза, что на карточке и на стрелке: одна правда о времени.
import { Clock } from "lucide-react";
import { DELAY_KINDS, EDGE_KIND_LABEL } from "../model/ops";
import * as ops from "../model/ops";
import type { Branch, Funnel, Schedule, Step } from "../model/types";
import { edgeWords } from "../canvas/flow";
import { dayWord, entryDayWord, schedulePhrase } from "../phrases";
import { useStudio } from "../store";
import { branchLong, branchNames, edgeKindLabel } from "../simple/labels";
import { useSimple } from "../simple/mode";
import { stepRef } from "../simple/names";
import { DelayPhrase } from "./DurationInput";
import { duration } from "../phrases";
import { Check, Field, IntInput, Seg } from "./ui";

type Mode = "chain" | "clock" | "webinar";

function modeOf(s: Schedule): Mode {
  if (s.webinar_offset_minutes !== null) return "webinar";
  if (s.absolute_time_msk) return "clock";
  return "chain";
}

const RESET: Partial<Schedule> = {
  absolute_time_msk: null, webinar_offset_minutes: null, day_offset_by_branch: null,
  webinar_offset_by_branch: null, day_offset: 0, anchor: "webinar", skip_if_past: true,
  min_delay_seconds: 0,
};

function toMin(t: string): number {
  const [h, m] = t.split(":").map(Number);
  return h * 60 + m;
}

function clock(total: number): string {
  const m = ((total % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

/** День словами: у воронки с эфиром – от эфира, без эфира – от регистрации. */
function dayLabel(d: number, webinar: boolean): string {
  if (!webinar) return entryDayWord(d);
  if (d === -1) return "накануне эфира";
  if (d < -1) return `за ${-d} дн. до эфира`;
  if (d === 0) return "в день эфира";
  if (d === 1) return "на следующий день после эфира (день 2)";
  return `через ${d} дн. после эфира (день ${d + 1})`;
}

function DaySelect({ value, onChange, webinar }: { value: number; onChange: (v: number) => void; webinar: boolean }) {
  const opts = webinar ? [-2, -1, 0, 1, 2, 3, 4, 5, 6] : [0, 1, 2, 3, 4, 5, 6, 7];
  if (!opts.includes(value)) opts.push(value);
  return (
    <select className="inp" value={value} onChange={(e) => onChange(Number(e.target.value))} data-testid="day-select">
      {opts.sort((a, b) => a - b).map((d) => <option key={d} value={d}>{dayLabel(d, webinar)}</option>)}
    </select>
  );
}

/** «3 ч 10 мин» – два поля вместо минут. */
function HoursMinutes({ value, onChange, testId }: { value: number; onChange: (v: number) => void; testId?: string }) {
  const h = Math.floor(value / 60);
  const m = value % 60;
  return (
    <span className="hm" data-testid={testId}>
      <IntInput value={h} min={0} width={52} onChange={(v) => v !== null && onChange(v * 60 + m)} /> ч
      <IntInput value={m} min={0} width={52} testId={testId ? `${testId}-min` : undefined} onChange={(v) => v !== null && onChange(h * 60 + Math.min(59, v))} /> мин
    </span>
  );
}

export function TimeConstructor({ step, funnel }: { step: Step; funnel: Funnel }) {
  const apply = useStudio((s) => s.apply);
  const branchId = useStudio((s) => s.branch);
  const simple = useSimple();
  const sc = step.schedule;
  const mode = modeOf(sc);
  const branches = funnel.branch_router?.branches ?? [];
  const webinar = !!funnel.branch_router;
  const multi = branches.length > 1;
  const patch = (p: Partial<Schedule>, coalesce?: string) =>
    apply((g) => ops.setSchedule(g, { stepId: step.id, patch: p }), coalesce ? { coalesce: `${coalesce}:${step.id}` } : undefined);

  const setMode = (m: Mode) => {
    if (m === mode) return;
    if (m === "chain") patch(RESET);
    if (m === "clock") patch({ ...RESET, absolute_time_msk: "10:00", day_offset: sc.day_offset });
    if (m === "webinar") patch({ ...RESET, webinar_offset_minutes: -30, day_offset: sc.day_offset });
  };

  const incoming = funnel.edges.filter((e) => e.to_step === step.id && DELAY_KINDS.has(e.kind));
  const byId = new Map(funnel.steps.map((s) => [s.id, s]));
  const branch = branches.find((b) => b.id === branchId) ?? null;

  // от эфира: знак отдельно («за» / «в начале» / «через»), величина – часами и минутами
  const off = sc.webinar_offset_minutes ?? 0;
  const dir = off < 0 ? "before" : off === 0 ? "at" : "after";
  const setOffset = (d: string, abs: number) => patch({ webinar_offset_minutes: d === "before" ? -abs : d === "at" ? 0 : abs }, "woff");
  const what = sc.anchor === "repeat" ? "повтора" : "эфира";
  const baseOf = (b: Branch) => toMin(b.webinar_time) + (sc.anchor === "repeat" ? b.repeat_offset_minutes : 0);

  const perBranch = mode === "clock" ? sc.day_offset_by_branch : sc.webinar_offset_by_branch;
  const perBranchKey = mode === "clock" ? "day_offset_by_branch" : "webinar_offset_by_branch";
  const phrase = schedulePhrase(sc, branch, branchNames(branches, simple), !webinar)
    ?? (incoming.length ? incoming.map((e) => edgeWords(e, byId.get(e.from_step), simple)).join(" / ") : "после предыдущего сообщения");

  return (
    <div className="timec" data-testid="time-constructor">
      <Seg<Mode> value={mode} onChange={setMode} testId="time-mode" options={[
        { value: "chain", label: "после предыдущего", title: "через сколько после предыдущего сообщения" },
        { value: "clock", label: "в определённое время", title: webinar ? "в ЧЧ:ММ по Москве, в день эфира или рядом с ним" : "в ЧЧ:ММ по Москве, в день регистрации или позже" },
        ...(webinar || mode === "webinar" ? [{ value: "webinar" as Mode, label: "от эфира", title: "за или через столько-то от начала эфира" }] : []),
      ]} />

      {mode === "chain" && (
        <div className="timec-body">
          {incoming.length === 0 && <div className="info-box">В эту карточку пока не ведёт ни одна стрелка. Протяните её от предыдущей карточки – или нажмите «Следующее сообщение» под ней.</div>}
          {incoming.map((e) => (
            <div className="in-edge" key={e.id} data-testid="in-edge">
              <DelayPhrase value={e.delay_seconds} allowNull={e.kind === "skip"}
                after={`после «${stepRef(byId.get(e.from_step), simple)}»${e.kind === "done" || e.kind === "also_fire" ? ""
                  : ` (${simple ? edgeKindLabel(e.kind, true) : EDGE_KIND_LABEL[e.kind]})`}`}
                onChange={(v) => apply((g) => ops.setEdgeDelay(g, { edgeId: e.id, delay: v }), { coalesce: `delay:${e.id}` })} />
              {e.kind === "also_fire" && <div className="field-hint">Отдельно от основной цепочки: придёт по своему таймеру, даже если следующие сообщения уже идут.</div>}
            </div>
          ))}
        </div>
      )}

      {mode === "clock" && (
        <div className="timec-body">
          <div className="phrase-row">
            <DaySelect value={sc.day_offset} webinar={webinar} onChange={(v) => patch({ day_offset: v })} />
            <span>в</span>
            <input className="inp time-inp" type="time" value={sc.absolute_time_msk ?? ""} data-testid="clock-time" aria-label="Время по Москве"
              onChange={(e) => e.target.value && patch({ absolute_time_msk: e.target.value.slice(0, 5) }, "abs")} />
            <span className="faint">МСК</span>
          </div>
          <Field label="Если человек пришёл позже этого времени">
            <Seg<"skip" | "send"> value={sc.skip_if_past ? "skip" : "send"} onChange={(v) => patch({ skip_if_past: v === "skip" })} options={[
              { value: "skip", label: "не отправлять" }, { value: "send", label: "отправить сразу" },
            ]} />
            <span className={"field-hint" + (sc.skip_if_past ? " warn-text" : "")} data-testid="skip-consequence">
              {sc.skip_if_past
                ? `Кто дойдёт до этого шага позже ${sc.absolute_time_msk ?? "этого времени"}, сообщение не получит – цепочка пойдёт дальше.`
                : sc.min_delay_seconds > 120
                ? `Кто пришёл позже, получит его не в указанное время, а через ${duration(sc.min_delay_seconds)} после того, как шаг запланирован.`
                : "Кто пришёл позже, получит его сразу (через 2 минуты), а не в указанное время."}
            </span>
          </Field>
        </div>
      )}

      {mode === "webinar" && (
        <div className="timec-body">
          <div className="phrase-row">
            <select className="inp" value={dir} onChange={(e) => setOffset(e.target.value, Math.abs(off) || 30)} data-testid="webinar-dir">
              <option value="before">за</option>
              <option value="at">в начале</option>
              <option value="after">через</option>
            </select>
            {dir !== "at" && <HoursMinutes value={Math.abs(off)} onChange={(v) => setOffset(dir, Math.max(1, v))} testId="webinar-min" />}
            <select className="inp" value={sc.anchor} onChange={(e) => patch({ anchor: e.target.value as Schedule["anchor"] })} data-testid="webinar-anchor">
              <option value="webinar">{dir === "before" ? "до эфира" : dir === "after" ? "после начала эфира" : "эфира"}</option>
              <option value="repeat">{dir === "before" ? "до повтора" : dir === "after" ? "после начала повтора" : "повтора"}</option>
            </select>
          </div>
          <Field label="День">
            <DaySelect value={sc.day_offset} webinar onChange={(v) => patch({ day_offset: v })} />
          </Field>
          {branches.length > 0 && !perBranch && (
            <div className="field-hint">
              {branches.map((b) => `${simple ? branchLong(b, branches) : `ветка ${b.id}`} – в ${clock(baseOf(b) + off)}`).join(" · ")}
            </div>
          )}
        </div>
      )}

      {mode !== "chain" && multi && (
        <div className="per-branch">
          <Check checked={!!perBranch} label="своё время для каждой ветки"
            hint="по умолчанию – как у первой ветки"
            onChange={(on) => patch({ [perBranchKey]: on ? {} : null } as Partial<Schedule>)} />
          {perBranch && branches.map((b) => (
            <div className="row" key={b.id}>
              <span className="branch-tag">{simple ? branchLong(b, branches) : `ветка ${b.id} · эфир ${b.webinar_time}`}</span>
              {mode === "clock" ? (
                <select className="inp" value={perBranch[b.id] ?? ""} onChange={(e) => {
                  const m = { ...perBranch };
                  if (e.target.value === "") delete m[b.id];
                  else m[b.id] = Number(e.target.value);
                  patch({ day_offset_by_branch: m });
                }}>
                  <option value="">как у первой ветки ({dayWord(sc.day_offset)})</option>
                  {[-1, 0, 1, 2, 3, 4, 5].map((d) => <option key={d} value={d}>{dayLabel(d, true)}</option>)}
                </select>
              ) : (
                <>
                  <span className="muted">в</span>
                  <input className="inp time-inp" type="time" data-testid={`branch-clock-${b.id}`}
                    value={perBranch[b.id] !== undefined ? clock(baseOf(b) + perBranch[b.id]) : ""}
                    onChange={(e) => {
                      const m = { ...perBranch };
                      if (!e.target.value) delete m[b.id];
                      else {
                        // время на часах → минуты от эфира (в пределах суток вокруг эфира)
                        let d = toMin(e.target.value) - baseOf(b);
                        if (d > 720) d -= 1440;
                        if (d < -720) d += 1440;
                        m[b.id] = d;
                      }
                      patch({ webinar_offset_by_branch: m }, `wob:${b.id}`);
                    }} />
                  <span className="faint small">
                    {perBranch[b.id] !== undefined
                      ? (perBranch[b.id] < 0 ? `за ${fmt(-perBranch[b.id])} до ${what}` : perBranch[b.id] === 0 ? `в начале ${what}` : `через ${fmt(perBranch[b.id])} после ${what}`)
                      : `как у первой: ${clock(baseOf(b) + off)}`}
                  </span>
                </>
              )}
            </div>
          ))}
        </div>
      )}

      {mode !== "chain" && (
        <div className="min-delay" data-testid="min-delay">
          <Check checked={sc.min_delay_seconds > 0} onChange={(v) => patch({ min_delay_seconds: v ? 300 : 0 })}
            label="не раньше чем через…"
            hint="считая от момента, когда шаг запланирован; у шагов, которые ставятся при регистрации, – от регистрации. Нужно, чтобы утреннее сообщение не влезло посреди регистрации" />
          {sc.min_delay_seconds > 0 && (
            <div className="phrase-row">
              <span>не раньше чем через</span>
              <HoursMinutes value={Math.round(sc.min_delay_seconds / 60)} testId="min-delay-min"
                onChange={(v) => patch({ min_delay_seconds: Math.max(1, v) * 60 }, "mindelay")} />
              <span className="muted">после того, как шаг запланирован</span>
            </div>
          )}
        </div>
      )}

      <Check checked={sc.dnd_exempt} onChange={(v) => patch({ dnd_exempt: v })} label="можно ночью (без тихих часов)" />

      <div className="phrase" data-testid="time-phrase">
        <Clock size={14} strokeWidth={2} aria-hidden />
        <span>{phrase}{branch && mode !== "chain" && <span className="muted"> ({simple ? branchLong(branch, branches) : `ветка ${branch.id}`})</span>}</span>
      </div>
    </div>
  );
}

function fmt(min: number): string {
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h && m ? `${h} ч ${m} мин` : h ? `${h} ч` : `${m} мин`;
}
