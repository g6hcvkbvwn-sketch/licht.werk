import { useEffect, useRef, useState } from "react";
import type { FixtureInfo } from "./api";

const COLOR_ATTRIBUTES = ["red", "green", "blue"] as const;

function toHex(state: Record<string, number>, prefix = ""): string {
  return (
    "#" +
    COLOR_ATTRIBUTES.map((name) =>
      Math.round((state[prefix + name] ?? 0) * 255)
        .toString(16)
        .padStart(2, "0"),
    ).join("")
  );
}

function fromHex(hex: string, prefix = ""): Record<string, number> {
  const n = parseInt(hex.slice(1), 16);
  return {
    [`${prefix}red`]: ((n >> 16) & 255) / 255,
    [`${prefix}green`]: ((n >> 8) & 255) / 255,
    [`${prefix}blue`]: (n & 255) / 255,
  };
}

const MOVEMENT = ["pan", "tilt", "speed"];
const RING_SECTIONS = [1, 2, 3, 4, 5, 6];

function label(name: string): string {
  const text = name.replace(/_/g, " ");
  return text[0].toUpperCase() + text.slice(1);
}

interface Props {
  fixture: FixtureInfo;
  lockedAttributes?: string[];
  onChange: (fixtureId: string, values: Record<string, number>) => void;
  onRename: (fixtureId: string, name: string) => void;
}

export function DisplayNameEditor({ name, onSave }: {
  name: string;
  onSave: (name: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name);
  const save = () => {
    if (draft.trim()) {
      onSave(draft.trim());
      setEditing(false);
    }
  };
  if (!editing) {
    return <>{name} <button type="button" aria-label={`Rename ${name}`} onClick={() => {
      setDraft(name);
      setEditing(true);
    }}>Rename</button></>;
  }
  return (
    <span>
      <input aria-label={`Display name for ${name}`} autoFocus maxLength={80}
        value={draft} onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") save();
          else if (event.key === "Escape") setEditing(false);
        }} />
      <button type="button" disabled={!draft.trim()} onClick={save}>Save</button>
      <button type="button" onClick={() => setEditing(false)}>Cancel</button>
    </span>
  );
}

/** Controls generated from the fixture's logical attributes; no DMX channel numbers here. */
export default function FixturePanel({ fixture, lockedAttributes = [], onChange, onRename }: Props) {
  const [values, setValues] = useState(fixture.state);
  const pending = useRef<Record<string, number>>({});
  const timer = useRef<number | undefined>(undefined);
  const [ringIndividual, setRingIndividual] = useState(false);
  const has = (name: string) => fixture.attributes.includes(name);
  const hasRgb = COLOR_ATTRIBUTES.every(has);
  const hasRing = fixture.attributes.some((name) => name.startsWith("ring_"));
  const hasRingRgb = COLOR_ATTRIBUTES.every((c) => has(`ring_${c}`));
  const hasSections = RING_SECTIONS.every((n) => COLOR_ATTRIBUTES.every((c) => has(`ring_${n}_${c}`)));
  const beam = fixture.attributes.filter((name) => !name.startsWith("ring_") && !MOVEMENT.includes(name)
    && !(hasRgb && (COLOR_ATTRIBUTES as readonly string[]).includes(name)));
  const movement = MOVEMENT.filter(has);
  const ringSliders = fixture.attributes.filter((name) => name === "ring_intensity" || name === "ring_strobe");
  const slider = (name: string) => (
    <label key={name} className={lockedAttributes.includes(name) ? "locked-field" : ""}>
      {label(name)}: {Math.round((values[name] ?? 0) * 100)}%{lockedAttributes.includes(name) ? " · LOCKED" : ""}
      <input type="range" min={0} max={100}
        disabled={!fixture.enabled || lockedAttributes.includes(name)}
        value={Math.round((values[name] ?? 0) * 100)}
        onChange={(e) => update({ [name]: Number(e.target.value) / 100 })} />
    </label>
  );
  const picker = (title: string, prefix: string) => {
    const locked = COLOR_ATTRIBUTES.some((a) => lockedAttributes.includes(prefix + a));
    return <label key={prefix} className={locked ? "locked-field" : ""}>
      {title}{locked ? " · LOCKED (scene / sequence)" : ""}
      <input type="color" disabled={!fixture.enabled || locked} value={toHex(values, prefix)}
        onChange={(e) => update(fromHex(e.target.value, prefix))} />
    </label>;
  };

  useEffect(() => () => window.clearTimeout(timer.current), []);

  // Follow server-side changes (scene recalls, fades) unless the user is mid-edit.
  useEffect(() => {
    if (Object.keys(pending.current).length === 0) setValues(fixture.state);
  }, [fixture.state]);

  // Batch changes and send them shortly after the user stops dragging.
  const update = (changed: Record<string, number>) => {
    setValues((current) => ({ ...current, ...changed }));
    pending.current = { ...pending.current, ...changed };
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const batch = pending.current;
      pending.current = {};
      onChange(fixture.id, batch);
    }, 50);
  };

  return (
    <section className={fixture.enabled ? "fixture" : "fixture disabled"}>
      <h2><DisplayNameEditor name={fixture.name} onSave={(name) => onRename(fixture.id, name)} /></h2>
      <p className="muted">
        {fixture.manufacturer} {fixture.model} · mode {fixture.mode} · universe {fixture.universe},
        address {fixture.start_address}
        {!fixture.enabled && " · DISABLED in patch"}
      </p>
      {fixture.fixture_type === "hazer" && (
        <p className="muted">
          Pump/fan command levels, not calibrated output percentages. Master and blackout do not
          stop haze. Keep power on for the machine's cooldown; pump off is not shutdown.
        </p>
      )}
      <div className="fixture-groups">
        <fieldset><legend>{hasRing ? "Beam" : "Controls"}</legend>
          {hasRgb && picker("Color", "")}
          {beam.map(slider)}
        </fieldset>
        {movement.length > 0 && <fieldset><legend>Movement</legend>{movement.map(slider)}</fieldset>}
        {hasRing && <fieldset><legend>Ring</legend>
          {ringSliders.map(slider)}
          {hasSections && hasRingRgb && <div role="group" aria-label="Ring colour mode" className="segmented">
            <button type="button" className={!ringIndividual ? "active" : ""} onClick={() => setRingIndividual(false)}>Uniform</button>
            <button type="button" className={ringIndividual ? "active" : ""} onClick={() => setRingIndividual(true)}>Individual</button>
          </div>}
          {(!ringIndividual || !hasSections) && hasRingRgb && picker("Ring colour (all sections)", "ring_")}
          {ringIndividual && hasSections && <div className="ring-sections">
            {RING_SECTIONS.map((n) => picker(`Section ${n}`, `ring_${n}_`))}
          </div>}
        </fieldset>}
      </div>
      <details className="fixture-advanced">
        <summary>Advanced: raw attributes</summary>
        {fixture.attributes.map(slider)}
      </details>
    </section>
  );
}
