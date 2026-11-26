// Мелкие кирпичи панелей: секция, поле, переключатель-сегменты, число.
import { useEffect, useState, type ReactNode } from "react";

export function Section({ title, children, hint, open = true, testId }: { title: string; children: ReactNode; hint?: string; open?: boolean; testId?: string }) {
  return (
    <details className="sec" open={open} data-testid={testId}>
      <summary>{title}{hint && <span className="sec-hint">{hint}</span>}</summary>
      <div className="sec-body">{children}</div>
    </details>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function Seg<T extends string>({ value, options, onChange, testId }: {
  value: T; options: { value: T; label: string; title?: string }[]; onChange: (v: T) => void; testId?: string;
}) {
  return (
    <div className="seg" data-testid={testId}>
      {options.map((o) => (
        <button key={o.value} type="button" title={o.title} className={o.value === value ? "seg-on" : ""} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Текстовое поле, которое отдаёт значение на каждый ввод, но не теряет курсор при внешних правках. */
export function TextInput({ value, onChange, placeholder, mono, testId }: {
  value: string; onChange: (v: string) => void; placeholder?: string; mono?: boolean; testId?: string;
}) {
  return (
    <input className={mono ? "inp mono" : "inp"} value={value} placeholder={placeholder} data-testid={testId}
      onChange={(e) => onChange(e.target.value)} />
  );
}

export function TextArea({ value, onChange, rows = 3, placeholder }: { value: string; onChange: (v: string) => void; rows?: number; placeholder?: string }) {
  return <textarea className="inp" rows={rows} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />;
}

/** Целое число; пустое поле – null (если allowNull). Значение применяется при вводе корректного числа. */
export function IntInput({ value, onChange, allowNull, min, width = 72, testId }: {
  value: number | null; onChange: (v: number | null) => void; allowNull?: boolean; min?: number; width?: number; testId?: string;
}) {
  const [text, setText] = useState(value === null ? "" : String(value));
  useEffect(() => setText(value === null ? "" : String(value)), [value]);
  return (
    <input className="inp num" style={{ width }} value={text} inputMode="numeric" data-testid={testId}
      onChange={(e) => {
        const t = e.target.value.trim();
        setText(e.target.value);
        if (t === "" && allowNull) onChange(null);
        else if (/^-?\d+$/.test(t)) {
          const n = Number(t);
          if (min === undefined || n >= min) onChange(n);
        }
      }} />
  );
}

export function Check({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="check" title={hint}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}
