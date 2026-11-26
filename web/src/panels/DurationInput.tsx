// Конструктор задержки: число + единица, быстрые варианты. Без свободного текста.
import { useEffect, useState } from "react";
import { duration } from "../phrases";

const UNITS: { sec: number; label: string }[] = [
  { sec: 1, label: "сек" },
  { sec: 60, label: "мин" },
  { sec: 3600, label: "ч" },
  { sec: 86400, label: "дн" },
];

const QUICK = [0, 5, 10, 60, 180, 1800, 3600, 7200, 14400, 86400];

function bestUnit(sec: number): number {
  for (const u of [...UNITS].reverse()) if (sec > 0 && sec % u.sec === 0) return u.sec;
  return 1;
}

export function DurationInput({ value, onChange, allowNull, nullLabel = "не задано", testId }: {
  value: number | null;
  onChange: (v: number | null) => void;
  allowNull?: boolean;
  nullLabel?: string;
  testId?: string;
}) {
  const [unit, setUnit] = useState(() => bestUnit(value ?? 0));
  const [text, setText] = useState(value === null ? "" : String((value ?? 0) / bestUnit(value ?? 0)));
  useEffect(() => {
    if (value === null) { setText(""); return; }
    const u = value % unit === 0 ? unit : bestUnit(value);
    setUnit(u);
    setText(String(value / u));
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  const commit = (t: string, u: number) => {
    const s = t.trim().replace(",", ".");
    if (s === "") {
      if (allowNull) onChange(null);
      return;
    }
    const n = Number(s);
    if (Number.isFinite(n) && n >= 0) onChange(Math.round(n * u));
  };

  return (
    <div className="dur" data-testid={testId}>
      <div className="dur-row">
        <input className="inp num" value={text} inputMode="decimal" placeholder={allowNull ? nullLabel : "0"}
          onChange={(e) => { setText(e.target.value); commit(e.target.value, unit); }} />
        <select className="inp" value={unit} onChange={(e) => { const u = Number(e.target.value); setUnit(u); commit(text, u); }}>
          {UNITS.map((u) => <option key={u.sec} value={u.sec}>{u.label}</option>)}
        </select>
        <span className="dur-phrase">{value === null ? nullLabel : duration(value)}</span>
      </div>
      <div className="quick">
        {allowNull && <button type="button" className={value === null ? "q-on" : ""} onClick={() => onChange(null)}>{nullLabel}</button>}
        {QUICK.map((q) => (
          <button type="button" key={q} className={value === q ? "q-on" : ""} onClick={() => onChange(q)}>{duration(q)}</button>
        ))}
      </div>
    </div>
  );
}

/** Одно поле-число без стрелок: пусто – 0. */
function Num({ value, onChange, label, testId }: { value: number; onChange: (v: number) => void; label: string; testId?: string }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  return (
    <input className="inp num dp-num" inputMode="numeric" aria-label={label} value={text} data-testid={testId}
      onChange={(e) => {
        setText(e.target.value);
        const t = e.target.value.trim();
        if (t === "") onChange(0);
        else if (/^\d+$/.test(t)) onChange(Number(t));
      }} />
  );
}

/** «Через [2] ч [3] мин после «…»» – одна строка: часы и минуты отдельными полями (секунды –
 *  только если они есть), под ней готовые варианты. Сырые минуты («123 мин») не показываем. */
export function DelayPhrase({ value, onChange, after, allowNull, nullLabel = "без таймаута", testId }: {
  value: number | null;
  onChange: (v: number | null) => void;
  /** «после «Привет!…»» */
  after: string;
  allowNull?: boolean;
  nullLabel?: string;
  testId?: string;
}) {
  const v = value ?? 0;
  const h = Math.floor(v / 3600);
  const m = Math.floor((v % 3600) / 60);
  const s = v % 60;
  const set = (hh: number, mm: number, ss: number) => onChange(hh * 3600 + Math.min(mm, 59) * 60 + Math.min(ss, 59));
  return (
    <div className="dur" data-testid={testId}>
      <div className="dp-row">
        {value === null ? <span>{nullLabel}</span> : (
          <>
            <span>Через</span>
            <Num value={h} label="часов" onChange={(x) => set(x, m, s)} /><span>ч</span>
            <Num value={m} label="минут" onChange={(x) => set(h, x, s)} testId="delay-min" /><span>мин</span>
            {s > 0 && <><Num value={s} label="секунд" onChange={(x) => set(h, m, x)} /><span>сек</span></>}
          </>
        )}
        <span className="dp-after">{after}</span>
      </div>
      <div className="quick">
        {allowNull && <button type="button" className={value === null ? "q-on" : ""} onClick={() => onChange(null)}>{nullLabel}</button>}
        {QUICK.map((q) => (
          <button type="button" key={q} className={value === q ? "q-on" : ""} onClick={() => onChange(q)}>{duration(q)}</button>
        ))}
      </div>
    </div>
  );
}
