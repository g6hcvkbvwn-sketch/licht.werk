import { useEffect, useId, useState } from "react";
import { BEATS } from "./showFields";

export function NumberField({ label, value, onChange, min = 0, max = 100, unit = "%" }: {
  label: string; value: number; onChange: (value: number) => void; min?: number; max?: number; unit?: string;
}) {
  const id = useId();
  const [text, setText] = useState(Number.isFinite(value) ? String(value) : "");
  useEffect(() => {
    if (Number.isFinite(value)) setText((current) => current.trim() && Number(current) === value ? current : String(value));
  }, [value]);
  const numeric = text.trim() === "" ? NaN : Number(text);
  const invalid = !Number.isFinite(numeric) || numeric < min || numeric > max;
  const message = `Enter ${min}–${max}${unit ? ` ${unit}` : ""}.`;
  return <label className="editor-number" htmlFor={id}>
    <span>{label}{unit ? ` (${unit})` : ""}</span>
    <input id={id} type="number" step="any" min={min} max={max} required value={text}
      aria-invalid={invalid} aria-describedby={invalid ? `${id}-error` : undefined}
      onChange={(e) => {
        const raw = e.target.value; setText(raw);
        onChange(raw.trim() === "" ? NaN : Number(raw));
      }} />
    {invalid && <small id={`${id}-error`} role="alert" className="editor-error">{message}</small>}
  </label>;
}

export function BeatField({ value, onChange, label, cut = false }: {
  value: number; onChange: (value: number) => void; label: string; cut?: boolean;
}) {
  const allowed = cut ? [0, ...BEATS] : BEATS;
  return <label>{label}<select value={value} aria-label={label} onChange={(e) => onChange(Number(e.target.value))}>
    {!allowed.includes(value) && <option value={value}>Choose a supported beat length</option>}
    {allowed.map((beat) => <option key={beat} value={beat}>{beat === 0 ? "Cut" : `${beat} beats`}</option>)}
  </select>{!allowed.includes(value) && <small role="alert" className="editor-error">Choose a listed beat length.</small>}</label>;
}

export const validNumber = (value: number, min: number, max: number) => Number.isFinite(value) && value >= min && value <= max;
