import { useEffect, useRef, useState } from "react";
import {
  FX_FAMILIES, getFxSlots, includeMoveZone, resetFxSlot, saveFxSlot, startFxSlot, stopFxFamily, storeCurrentFx,
  type EffectParams, type FxDefinition, type FxPage, type FxSlots, type FxComponent,
} from "./fxApi";
import DialogFrame, { type DialogFrameProps } from "./DialogFrame";
import {
  mapPoints, mapZones, moveMode, placeLabels, pointWarnings, toggleTarget, tourSummary, tourWarnings, zoneWarnings,
  type MoveMode, type TourStatus, type ZoneStatus,
} from "./tourBuilder";
import "./fx.css";

const BEATS = [0.25, 0.5, 1, 2, 4, 8, 16, 32];
const GENERATOR_NAMES = {
  chase: "Dimmer", rainbow: "Rainbow", two_colour: "Two colours", circle: "Circle", figure8: "Figure 8",
};
const valid = (value: number, min: number, max: number) =>
  Number.isFinite(value) && value >= min && value <= max;
const percent = (value: number) => Number.isFinite(value) ? `${Math.round(value * 1000) / 10}%` : "—";
const message = (e: unknown) => e instanceof Error ? e.message : String(e);

export function validateFx(definition: FxDefinition, page: FxPage, data: FxSlots): string[] {
  const p = definition.params;
  const errors: string[] = [];
  if (!definition.name.trim()) errors.push("Enter a preset name.");
  if (!FX_FAMILIES[page].kinds.includes(definition.kind)) errors.push("Choose a generator in this family.");
  for (const key of ["size", "spread", "softness", "maximum"] as const) {
    if (!valid(p[key], 0, 1)) errors.push(`${key === "size" ? "Depth / Size" : key}: enter 0–100%.`);
  }
  if (!Number.isFinite(p.beats) || p.beats <= 0) errors.push("Beats must be a finite positive number.");
  if (!valid(p.seconds, 0.25, 120)) errors.push("Seconds per cycle must be 0.25–120.");
  for (const key of ["travel", "hold"] as const) {
    if (!Number.isFinite(p[key]) || p[key] <= 0 || p[key] > 120)
      errors.push(`${key === "travel" ? "Travel" : "Hold"}: enter more than 0 and at most 120 ${p.timing}.`);
  }
  if (new Set(p.order).size !== p.order.length || p.order.some((id) => !data.fixtures.some((f) => f.id === id)))
    errors.push("Fixture order contains missing or duplicate IDs. Restore patch order or repair the list.");
  if (p.palette.length < 2 || p.palette.length > 8 || p.palette.some((c) => !/^#[0-9a-f]{6}$/i.test(c)))
    errors.push("Palette needs 2–8 valid #RRGGBB colours.");
  const zoneIds = p.zone_ids ?? [];
  if (new Set(p.target_ids).size !== p.target_ids.length
    || p.target_ids.some((id) => !data.targets.some((t) => t.id === id)))
    errors.push("Target Tour contains missing or duplicate targets. Remove them or choose existing targets.");
  if (new Set(zoneIds).size !== zoneIds.length
    || (data.tour_zones && zoneIds.some((id) => !data.tour_zones?.some((z) => z.id === id))))
    errors.push("Zone route contains missing or duplicate zones. Remove them or choose existing zones.");
  if (p.target_ids.length && zoneIds.length) errors.push("A Move route uses either targets or zones, not both.");
  const stopCount = p.target_ids.length + zoneIds.length;
  if (page === "circle" && stopCount > 1 && p.pattern === "current")
    errors.push("Choose Still, Circle or Figure eight for During hold.");
  if (page === "circle" && !stopCount && p.pattern !== "current")
    errors.push("Add a target or zone to the route, or switch to Current target / zone.");
  return errors;
}

function NumberField({ label, value, onChange, min = 0, max = 100, unit = "%", disabled = false, positive = false }: {
  label: string; value: number; onChange: (value: number) => void;
  min?: number; max?: number; unit?: string; disabled?: boolean; positive?: boolean;
}) {
  const error = !valid(value, min, max) || (positive && value <= 0);
  return <label className="fx-number">
    <span>{label}</span>
    <input type="number" min={min} max={max} step="any" value={Number.isFinite(value) ? value : ""}
      disabled={disabled} aria-label={`${label} ${unit}`} aria-invalid={error}
      onChange={(e) => onChange(e.target.value.trim() ? Number(e.target.value) : NaN)} />
    <span>{unit}</span>
    {error && <small className="fx-invalid" role="alert">
      {positive ? `Enter more than 0 and at most ${max}` : `Enter ${min}–${max}`} {unit}.
    </small>}
  </label>;
}

function OrderedList({ ids, items, onChange, removable = false }: {
  ids: string[]; items: { id: string; name: string }[]; onChange: (ids: string[]) => void; removable?: boolean;
}) {
  const move = (index: number, direction: number) => {
    const next = [...ids];
    [next[index], next[index + direction]] = [next[index + direction], next[index]];
    onChange(next);
  };
  return <ol className="fx-order">{ids.map((id, index) => <li key={`${id}:${index}`}>
    <span>{items.find((item) => item.id === id)?.name ?? `Missing: ${id}`}</span>
    <button type="button" disabled={index === 0} aria-label={`Move ${id} up`}
      onClick={() => move(index, -1)}>↑</button>
    <button type="button" disabled={index === ids.length - 1} aria-label={`Move ${id} down`}
      onClick={() => move(index, 1)}>↓</button>
    {removable && <button type="button" aria-label={`Remove ${id}`}
      onClick={() => onChange(ids.filter((_, i) => i !== index))}>Remove</button>}
  </li>)}</ol>;
}

function TourMap({ room, statuses, zones, ids, mode, current }: {
  room: [number, number, number]; statuses: TourStatus[]; zones: ZoneStatus[]; ids: string[];
  mode: MoveMode; current: Set<string>;
}) {
  const routeZones = mode === "zones";
  const areas = mapZones(zones, routeZones ? ids : mode === "current" ? ids : [], room);
  const points = placeLabels(mapPoints(statuses, routeZones ? [] : ids, room), areas, room);
  if (!points.length && !areas.length) return null;
  const centres = new Map([
    ...points.map((p) => [p.id, { left: p.left, top: p.top }] as const),
    ...areas.map((z) => [z.id, { left: z.left + z.width / 2, top: z.top + z.height / 2 }] as const),
  ]);
  const route = mode === "current" ? [] : ids.map((id) => centres.get(id)).filter((point) => point !== undefined);
  const names = new Map([...statuses, ...zones].map((item) => [item.id, item.name]));
  return <div className="fx-tour-map">
    <h3>Schematic top view</h3>
    <small className="fx-map-side">far side</small>
    <div className="fx-tour-map-area"
      style={{ aspectRatio: `${room[0]} / ${room[1]}`, maxWidth: `${Math.round(380 * room[0] / room[1])}px` }}>
      {areas.map((zone) => <div key={zone.id} title={zone.name} aria-label={`Zone ${zone.name}`}
        className={`fx-map-zone${zone.order.length ? " picked" : ""}${current.has(zone.id) ? " current" : ""}`}
        style={{ left: `${zone.left}%`, top: `${zone.top}%`, width: `${zone.width}%`, height: `${zone.height}%` }}>
        <small className={`fx-map-label side-${zone.labelSide}`}>
          {zone.order.length && mode !== "current" ? `${zone.order.join(",")} ` : ""}{zone.name}</small>
      </div>)}
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        {route.length > 1 && <polyline fill="none" points={[...route, route[0]].map((p) => `${p.left},${p.top}`).join(" ")} />}
      </svg>
      {points.map((point) => <span key={point.id}
        className={`fx-map-point${point.order.length || (mode === "current" && ids.includes(point.id)) ? " picked" : ""}${current.has(point.id) ? " current" : ""}`}
        style={{ left: `${point.left}%`, top: `${point.top}%` }} title={point.label} aria-label={point.label}>
        {point.order.length && mode !== "current" ? point.order.join(",") : ""}
        <small className={`fx-map-label side-${point.labelSide}`} aria-hidden="true">{point.name}</small>
      </span>)}
    </div>
    <small className="fx-map-side">DJ booth</small>
    {mode !== "current" && ids.length > 0 && <p className="fx-tour-legend">
      {ids.map((id, index) => `${index + 1} ${names.get(id) ?? id}`).join(" → ")} → 1</p>}
    <small className="muted">Seen from above, oriented like the Live/Preview monitors (DJ booth at the bottom).
      Dots = targets (hover for height), boxes = zones; wall zones are drawn as thin strips at their wall.
      Dashed outline = current aim. Schematic only: real beams travel dark along each head's pan/tilt path.</small>
  </div>;
}

interface Props {
  track: number;
  page: FxPage;
  stripName: string;
  stripActive: boolean;
  preview: boolean;
  component?: FxComponent;
  initialSlot?: number;
  onClose: () => void;
  embedded?: { definition: FxDefinition; onChange: (definition: FxDefinition) => void };
}

function InlineFrame({ children, status }: DialogFrameProps) {
  return <div className="fx-embedded">{status}{children}</div>;
}

export default function FxDialog({ track, page, stripName, stripActive, preview, component = "beam", initialSlot = 0, onClose, embedded }: Props) {
  const [data, setData] = useState<FxSlots | null>(null);
  const [draft, updateDraft] = useState<FxDefinition | null>(embedded?.definition ?? null);
  const setDraft = (definition: FxDefinition | null) => {
    updateDraft(definition);
    if (definition) embedded?.onChange(definition);
  };
  const [row, setRow] = useState(initialSlot);
  const [factory, setFactory] = useState("");
  const [mode, setMode] = useState<MoveMode>("current");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const inFlight = useRef(false);
  const generation = useRef(0);
  const alive = useRef(true);
  const family = FX_FAMILIES[page];

  const loadDraft = (definition: FxDefinition) => {
    updateDraft(structuredClone(definition));
    setMode(page === "circle" ? moveMode(definition.params) : "current");
    setFactory("");
  };
  useEffect(() => {
    alive.current = true;
    let loading = false;
    let initialized = false;
    const refresh = async () => {
      if (loading || inFlight.current) return;
      loading = true;
      const requestGeneration = generation.current;
      try {
        const next = await getFxSlots(track, page, component);
        if (!alive.current || inFlight.current || generation.current !== requestGeneration) return;
        setData(next); setConnected(true);
        if (!initialized) {
          loadDraft(embedded?.definition ?? next.slots[initialSlot] ?? next.slots[0]);
          initialized = true;
        }
      } catch (e) {
        if (alive.current && generation.current === requestGeneration) { setError(message(e)); setConnected(false); }
      } finally { loading = false; }
    };
    void refresh();
    const timer = window.setInterval(refresh, 1000);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
    };
  }, [track, page, component]);

  const perform = async (action: () => Promise<FxSlots>, text: string, replaceDraft = false): Promise<boolean> => {
    if (inFlight.current) return false;
    generation.current++;
    inFlight.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const next = await action();
      if (alive.current) {
        setData(next); setConnected(true); setNotice(text);
        if (replaceDraft) loadDraft(next.slots[row]);
      }
      return true;
    } catch (e) { if (alive.current) setError(message(e)); return false; }
    finally {
      inFlight.current = false;
      if (alive.current) setBusy(false);
    }
  };
  const p = draft?.params;
  const change = (params: Partial<EffectParams>) => {
    if (draft) { setDraft({ ...draft, params: { ...draft.params, ...params } }); setNotice(""); }
  };
  const errors = draft && data ? validateFx(draft, page, data) : [];
  const tourMode = page === "circle" && mode !== "current";
  const tourStatuses: TourStatus[] = data?.tour_targets
    ?? data?.targets.map((t) => ({ ...t, x: null, y: null, z: null, reachable: true, pattern_ok: true, note: "" })) ?? [];
  const zoneStatuses: ZoneStatus[] = data?.tour_zones ?? [];
  const aims = data?.current_aims ?? [];
  const currentAims = new Set(aims.flatMap((a) => [a.target_id, a.zone_id].filter((id) => id !== null)));
  const stops = p ? (mode === "zones" ? p.zone_ids ?? [] : mode === "targets" ? p.target_ids
    : [...p.target_ids, ...p.zone_ids ?? []]) : [];
  const routeProblems = page !== "circle" || !p ? []
    : mode === "targets" ? tourWarnings(p, tourStatuses)
    : mode === "zones" ? zoneWarnings(p, zoneStatuses)
    : pointWarnings(p, tourStatuses, zoneStatuses);
  if (tourMode && stops.length === 1)
    routeProblems.push(`A tour needs at least two stops. For one ${mode === "zones" ? "zone" : "target"}, use Current target / zone instead.`);
  const pickedName = mode === "current" && stops.length
    ? [...tourStatuses, ...zoneStatuses].find((item) => item.id === stops[0])?.name ?? stops[0] : null;
  const pointSummary = !pickedName ? "Uses each head's current aim (see At launch below)."
    : p?.zone_ids?.length ? `Pattern runs inside zone “${pickedName}”. Size 0 = still at the zone centre.`
    : `Pattern runs around target “${pickedName}”. Size 0 = still on the target.`;
  const switchMode = (next: MoveMode) => {
    if (next === mode) return;
    setMode(next);
    change({ target_ids: [], zone_ids: [], pattern: next === "current" ? "current" : "none" });
  };
  const pickZone = (zone: ZoneStatus, params: Partial<EffectParams>) => {
    if (zone.state !== "excluded") { change(params); return; }
    if (!window.confirm(`Include zone “${zone.name}” for ${stripName}?\n\nThis only changes the area permission `
      + "(the same as Aim / Permissions on the APC). Nothing moves now.")) return;
    void perform(() => includeMoveZone(track, zone.id), `${zone.name} is now included for ${stripName}. Nothing moved.`)
      .then((ok) => { if (ok) change(params); });
  };
  const dirty = !!draft && !!data && JSON.stringify(draft) !== JSON.stringify(data.slots[row]);
  const contextReady = connected && data?.preview === preview;
  const savedErrors = data?.slots[row] ? validateFx(data.slots[row], page, data) : [];
  const savedParams = data?.slots[row]?.params;
  const savedCount = data && savedParams ? (savedParams.order.length || data.fixtures.length) : 0;
  const savedParticipantCount = savedParams?.selection === "even" ? Math.floor(savedCount / 2)
    : savedParams?.selection === "odd" ? Math.ceil(savedCount / 2) : savedCount;
  const participants = data && p
    ? (p.order.length ? p.order : data.fixtures.map((f) => f.id))
      .filter((_, index) => p.selection === "odd" ? index % 2 === 0 : p.selection === "even" ? index % 2 === 1 : true)
    : [];
  const launchProblem = !contextReady ? "Waiting for current Live / Preview status."
    : !stripActive ? "Strip is in standby. Activate its group / single mode before starting."
    : !data?.fixtures.length ? "No compatible fixtures in this strip."
    : "";
  const canSave = !!draft && !!data && !errors.length && connected && !busy;
  const canRun = !launchProblem && !busy;
  const matchingRun = !!data?.running && data.running.slot === row;

  const presetTools = <>
    <button type="button" disabled={busy || !contextReady || !matchingRun} onClick={() => {
      if (dirty && !window.confirm("Replace unsaved draft and saved slot with current running FX?")) return;
      void perform(() => storeCurrentFx(track, page, row, component), "Current FX stored. Base / look values were not captured.", true);
    }}>Store current FX in this slot</button>
    <button type="button" disabled={busy || !connected} onClick={() => {
      if (window.confirm(`Reset slot ${row + 1} to factory? Saved edits and this draft will be replaced; running FX stays unchanged.`))
        void perform(() => resetFxSlot(track, page, row, component), "Factory definition restored; running snapshot unchanged.", true);
    }}>Reset to factory</button>
  </>;
  const target = preview ? "Preview" : "Live";
  const Frame = embedded ? InlineFrame : DialogFrame;
  return <Frame id="fx" size={page === "circle" ? "wide" : "medium"} className="fx-dialog" dirty={dirty} busy={busy} onClose={onClose} saveNote preview={preview}
    title={`${family.name} FX · ${component === "ring" ? "Ring · " : ""}${stripName} · strip ${track}`}
    context={`Slot ${row + 1} · ${preview ? "Start / Apply / Stop affect staged FX, not live output." : "Start / Apply / Stop may change output now."}`}
    status={<>
      {error && <div className="banner error" role="alert">{error}</div>}
      {notice && <p className="fx-notice" role="status">{notice}</p>}
    </>}
    footer={data && draft && p ? <>
      <button type="button" disabled={!canSave} onClick={() => void perform(
        () => saveFxSlot(track, page, row, draft, false, component), "Saved for next launch. Running snapshot unchanged.", true,
      )}>Save for next launch</button>
      <button type="button" disabled={!canSave || !canRun || !participants.length || routeProblems.length > 0}
        title={routeProblems.length ? "Fix the route warnings first." : undefined} onClick={() => void perform(
        () => saveFxSlot(track, page, row, draft, true, component),
        `Saved and applied in ${preview ? "PREVIEW (staged)" : "LIVE"}.`, true,
      )}>Save &amp; apply to {target}</button>
      <button type="button" disabled={busy || !contextReady || !data.running} onClick={() => void perform(
        () => stopFxFamily(track, page, component), `Stopped ${family.name} in ${preview ? "PREVIEW" : "LIVE"}; saved slots unchanged.`,
      )}>Stop {family.name} family</button>
      <button type="button" disabled={!canRun || savedErrors.length > 0 || !savedParticipantCount} onClick={() => void perform(
        () => startFxSlot(track, page, row, component), `Started saved slot ${row + 1}. Unsaved draft was not applied.`,
      )}>Start saved slot {row + 1}{dirty ? " (ignores draft)" : ""}</button>
      <details className="dialog-more"><summary>More</summary><div className="dialog-more-menu">{presetTools}</div></details>
    </> : null}>
      {!data || !draft || !p ? <p>Loading saved FX presets… Retrying automatically.</p> : <>
        {!embedded && <nav className="fx-slots" aria-label="FX preset slots">
          {Array.from({ length: 5 }, (_, index) => <button key={index} type="button"
            disabled={busy || !data.slots[index]} aria-pressed={row === index}
            className={row === index ? "active" : ""} onClick={() => {
              if (index === row || (dirty && !window.confirm("Discard unsaved edits and select another slot?"))) return;
              setRow(index); loadDraft(data.slots[index]); setNotice(""); setError("");
            }}>Slot {index + 1}<small>{data.slots[index]?.name ?? "Unavailable"}</small>
            <small>{row === index ? "Selected" : ""}{row === index && data.running?.slot === index ? " · " : ""}{data.running?.slot === index ? "Running" : ""}</small></button>)}
        </nav>}
        <p className="fx-running" role="status">{data.running
          ? `${data.running.active ? "Running" : "Suspended / inactive"}: slot ${data.running.slot + 1} · ${data.running.definition.name}${data.running.combo_name ? ` · Combo: ${data.running.combo_name}` : ""}${matchingRun || embedded ? "" : " (another slot)"}.`
          : "No running FX in this family (stopped or replaced)."}
          {" "}{embedded ? "Editing this Combo component's draft." : dirty ? "Unsaved draft." : "Showing saved definition."}</p>
        {data.warnings.length > 0 && <ul className="fx-warnings">{data.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
        <fieldset disabled={busy} className="fx-fields">
          <label>Preset name <input value={draft.name} aria-invalid={!draft.name.trim()}
            onChange={(e) => { setDraft({ ...draft, name: e.target.value }); setNotice(""); }} /></label>
          <label>Load factory preset <select value={factory} onChange={(e) => {
            const value = e.target.value;
            if (!value) return;
            const definition = data.factory[Number(value)];
            loadDraft(definition); embedded?.onChange(definition);
            setFactory(value); setNotice("Factory preset loaded into draft only. Save to keep it.");
          }}><option value="">Choose a factory look…</option>
            {data.factory.map((definition, index) => family.kinds.includes(definition.kind)
              ? <option value={index} key={index}>
                {GENERATOR_NAMES[definition.kind]} · {definition.name.replace(/^(Dimmer|Colour|Move)\s*·\s*/, "")}
              </option> : null)}
          </select></label>
          {page !== "chase" && <label>Generator <select value={draft.kind}
            onChange={(e) => setDraft({ ...draft, kind: e.target.value as FxDefinition["kind"] })}>
            {family.kinds.map((kind) => <option value={kind} key={kind}>
              {GENERATOR_NAMES[kind]}
            </option>)}
          </select></label>}
          <div className="fx-grid">
            <label>Timing <select value={p.timing} onChange={(e) => change({ timing: e.target.value as EffectParams["timing"] })}>
              <option value="beats">Beats</option><option value="seconds">Seconds</option>
            </select></label>
            {tourMode ? null : p.timing === "beats" ? <label>Rate <select value={p.beats} onChange={(e) => change({ beats: Number(e.target.value) })}>
              {!BEATS.includes(p.beats) && <option value={p.beats}>{p.beats} (saved value)</option>}
              {BEATS.map((beat) => <option key={beat} value={beat}>{beat} beats / cycle</option>)}
            </select></label> : <NumberField label="Rate" value={p.seconds} min={0.25} max={120}
              unit="seconds / cycle" onChange={(seconds) => change({ seconds })} />}
            {!tourMode && <NumberField label={page === "chase" ? "Depth" : "Size"} value={p.size * 100}
              disabled={page === "chase" && p.maximum === 0} onChange={(size) => change({ size: size / 100 })} />}
            <NumberField label="Spread" value={p.spread * 100} disabled={participants.length < 2}
              onChange={(spread) => change({ spread: spread / 100 })} />
          </div>
          {participants.length < 2 && <small className="muted">Spread needs multiple participating fixtures.</small>}
          <small className="muted">One cycle = the complete pattern / palette. Beat FX join the shared grid; changing rate may jump phase.
            Seconds FX start from phase zero on launch.</small>
          {page === "chase" && <>
            <label>Shape <select value={p.shape} onChange={(e) => change({ shape: e.target.value as EffectParams["shape"] })}>
              {Object.entries({ legacy: "Legacy pulse", sine: "Sine", triangle: "Triangle", square: "Square",
                saw_up: "Saw up", saw_down: "Saw down", one_by_one: "One by one" })
                .map(([value, name]) => <option key={value} value={value}>{name}</option>)}
            </select></label>
            <NumberField label="Maximum · % of base" value={p.maximum * 100}
              onChange={(maximum) => change({ maximum: maximum / 100 })} />
            {valid(p.size, 0, 1) && valid(p.maximum, 0, 1) ? <p>Range: {percent(p.maximum * (1 - p.size))}–{percent(p.maximum)} of fader / base intensity.
              Minimum = maximum × (1 − depth).
              {" "}Example at 80% base: {percent(0.8 * p.maximum * (1 - p.size))}–{percent(0.8 * p.maximum)} output, before master.
              {p.maximum === 0 && " Range is zero; Depth is disabled. A zero base always stays dark."}</p>
              : <p className="muted">Enter valid Depth and Maximum values (0–100%) to see the range and output example.</p>}
            {p.shape === "one_by_one" && <small>One cycle visits every participant in order; Spread does not define the step time.</small>}
          </>}
          {page === "rainbow" && <>
            <label>Colour mode <select value={p.colour_mode}
              onChange={(e) => change({ colour_mode: e.target.value as EffectParams["colour_mode"] })}>
              <option value="base">Follow base colour</option><option value="palette">Fixed palette</option>
            </select></label>
            {p.colour_mode === "base" ? <p className="muted">Rainbow follows base hue; Two colours uses a hue offset.
              Size controls hue range / distance. Look and manual base colours remain in charge.</p> : <>
              <p>Fixed palette controls {component === "ring" ? "section" : "main"} colour, not intensity.
                {component === "beam" && " Main white is suppressed while active;"}
                live colour edits update the underlying base only. Stop restores it.</p>
              <div className="fx-palette">{p.palette.map((colour, index) => <div key={index}>
                <input type="color" value={/^#[0-9a-f]{6}$/i.test(colour) ? colour : "#000000"}
                  aria-label={`Palette colour ${index + 1}`}
                  onChange={(e) => change({ palette: p.palette.map((c, i) => i === index ? e.target.value : c) })} />
                <span>{index + 1}</span><button type="button" disabled={p.palette.length <= 2}
                  aria-label={`Remove palette colour ${index + 1}`}
                  onClick={() => change({ palette: p.palette.filter((_, i) => i !== index) })}>Remove</button>
              </div>)}
                <button type="button" disabled={p.palette.length >= 8}
                  onClick={() => change({ palette: [...p.palette, "#ffffff"] })}>Add colour</button>
              </div>
              <label>Transition <select value={p.transition}
                onChange={(e) => change({ transition: e.target.value as EffectParams["transition"] })}>
                <option value="step">Step</option><option value="smooth">Smooth · RGB interpolation</option>
              </select></label>
            </>}
          </>}
          {page === "circle" && <>
            <div className="fx-destination" role="group" aria-label="Movement mode">
              {([
                ["current", "Current target / zone", "One point: the strip's current aim, or one target or zone you pick"],
                ["targets", "Named target tour", "Visit chosen targets in your order, then repeat"],
                ["zones", "Named zone tour", "Visit chosen zones in your order, then repeat"],
              ] as const).map(([value, title, hint]) => <button type="button" key={value} aria-pressed={mode === value}
                className={mode === value ? "active" : ""} onClick={() => switchMode(value)}>{title}<small>{hint}</small></button>)}
            </div>
            <section className="fx-tour">
              <p className="fx-tour-summary" role="status"><strong>{mode === "current" ? pointSummary
                : tourSummary(p, mode === "zones" ? "zone" : "target")}</strong></p>
              {routeProblems.length > 0 && <ul className="fx-warnings">
                {routeProblems.map((w) => <li key={w}>{w}</li>)}</ul>}
              <div className="fx-route-picker">
                <div className="fx-route-pick">
                  {mode === "current" && <fieldset className="fx-target-tiles">
                    <legend>Default</legend>
                    <button type="button" aria-pressed={!stops.length} className={!stops.length ? "picked" : ""}
                      onClick={() => change({ target_ids: [], zone_ids: [] })}>
                      <span>Use current aim</span>
                      <small>{aims.length ? aims.map((a) => `${a.name}: ${
                        a.target_id ? tourStatuses.find((t) => t.id === a.target_id)?.name ?? a.target_id
                        : a.zone_id ? zoneStatuses.find((z) => z.id === a.zone_id)?.name ?? a.zone_id
                        : a.include_zones.length > 1 ? `tour ${a.include_zones.length} areas` : "included area"}`).join(" · ")
                        : "No moving heads"}</small>
                    </button>
                  </fieldset>}
                  {mode !== "zones" && <fieldset className="fx-target-tiles">
                    <legend>{mode === "current" ? "Or one target (tap again for current aim)" : "Targets · tap in visiting order (tap again to remove)"}</legend>
                    {tourStatuses.length === 0 && <p className="muted">No named targets yet. Add measured targets in Setup → Room.</p>}
                    {tourStatuses.map((t) => {
                      const position = stops.indexOf(t.id);
                      const picked = position >= 0;
                      return <button type="button" key={t.id} aria-pressed={picked}
                        className={`${picked ? "picked" : ""} ${!t.reachable ? "unavailable" : ""}`}
                        disabled={!t.reachable && !picked} title={t.note}
                        onClick={() => change(mode === "current"
                          ? { target_ids: picked ? [] : [t.id], zone_ids: [] }
                          : { target_ids: toggleTarget(p.target_ids, t.id) })}>
                        {picked && mode !== "current" && <b className="fx-tile-number">{position + 1}</b>}
                        <span>{t.name}</span>
                        <small>{!t.reachable ? (t.x === null ? "Not measured" : "Unreachable")
                          : t.pattern_ok ? "Pattern OK" : mode === "current" ? "Still only (Size 0)" : "Still hold only"}
                          {currentAims.has(t.id) ? " · current aim" : ""}</small>
                      </button>;
                    })}
                  </fieldset>}
                  {mode !== "targets" && <fieldset className="fx-target-tiles">
                    <legend>{mode === "current" ? "Or one zone (tap again for current aim)" : "Zones · tap in visiting order (tap again to remove)"}</legend>
                    {zoneStatuses.length === 0 && <p className="muted">No zones yet. Add zones in Setup → Room.</p>}
                    {zoneStatuses.map((z) => {
                      const position = stops.indexOf(z.id);
                      const picked = position >= 0;
                      const blocked = z.state === "unreachable";
                      return <button type="button" key={z.id} aria-pressed={picked}
                        className={`${picked ? "picked" : ""} ${blocked ? "unavailable" : ""}`}
                        disabled={blocked && !picked} title={z.note}
                        onClick={() => picked
                          ? change(mode === "current" ? { zone_ids: [], target_ids: [] }
                            : { zone_ids: (p.zone_ids ?? []).filter((id) => id !== z.id) })
                          : pickZone(z, mode === "current" ? { zone_ids: [z.id], target_ids: [] }
                            : { zone_ids: [...p.zone_ids ?? [], z.id] })}>
                        {picked && mode !== "current" && <b className="fx-tile-number">{position + 1}</b>}
                        <span>{z.name}</span>
                        <small>{z.state === "included" ? "Included" : z.state === "excluded"
                          ? "Excluded · tap to include" : "Unreachable"}{currentAims.has(z.id) ? " · current aim" : ""}</small>
                      </button>;
                    })}
                  </fieldset>}
                  {mode !== "current" && <div className="fx-route">
                    <h3>Route</h3>
                    {stops.length ? <OrderedList ids={stops} items={mode === "zones" ? zoneStatuses : tourStatuses} removable
                      onChange={(ids) => change(mode === "zones" ? { zone_ids: ids } : { target_ids: ids })} />
                      : <p className="muted">Empty.</p>}
                    <button type="button" disabled={!stops.length}
                      onClick={() => change({ target_ids: [], zone_ids: [] })}>Clear route</button>
                  </div>}
                </div>
                {data.room && <TourMap room={data.room} statuses={tourStatuses} zones={zoneStatuses} ids={stops}
                  mode={mode} current={currentAims} />}
              </div>
              {tourMode && <div className="fx-grid">
                <NumberField label="Travel" value={p.travel} positive max={120} unit={p.timing}
                  onChange={(travel) => change({ travel })} />
                <NumberField label="Hold" value={p.hold} positive max={120} unit={p.timing}
                  onChange={(hold) => change({ hold })} />
                <label>During hold <select value={p.pattern}
                  onChange={(e) => change({ pattern: e.target.value as EffectParams["pattern"] })}>
                  <option value="none">Still</option><option value="circle">Circle</option>
                  <option value="figure8">Figure eight</option>
                </select></label>
                {p.pattern !== "none" && <>
                  {p.timing === "beats" ? <label>Pattern rate <select value={p.beats}
                    onChange={(e) => change({ beats: Number(e.target.value) })}>
                    {!BEATS.includes(p.beats) && <option value={p.beats}>{p.beats} (saved value)</option>}
                    {BEATS.map((beat) => <option key={beat} value={beat}>{beat} beats / cycle</option>)}
                  </select></label> : <NumberField label="Pattern rate" value={p.seconds} min={0.25} max={120}
                    unit="seconds / cycle" onChange={(seconds) => change({ seconds })} />}
                  <NumberField label="Pattern size" value={p.size * 100} onChange={(size) => change({ size: size / 100 })} />
                </>}
              </div>}
              {tourMode ? <p className="muted">Travel and Hold use the Timing unit above. Patterns run only during holds, never while
                travelling{mode === "zones" ? "; in a zone, Still holds at its centre and a pattern stays inside the zone" : ""}.
                Device knob 1 edits Travel while a tour runs. Picking only edits this draft; nothing moves until
                Save &amp; apply or Start.</p>
                : !stops.length ? <>
                  <p><strong>At launch:</strong></p>
                  {data.anchors?.length ? <ul>{data.anchors.map((line) => <li key={line}>{line}</li>)}</ul>
                    : <p className="muted">No moving heads found on this strip.</p>}
                  <p className="muted">Change the current aim with Aim / Permissions (SHIFT + a head strip's TRACK SELECT).
                    With several included areas and no aim, movement tours the areas with dark transfers.</p>
                </> : <p className="muted">Rate and Size above control the pattern. Picking only edits this draft; nothing
                  moves until Save &amp; apply or Start. Including a zone changes permissions only.</p>}
            </section>
            <p className="fx-warnings">Movement never bypasses permissions, calibration or limits. Transfers remain dark;
              unreachable or excluded movement can be blanked. Size is angular amplitude / area coverage, not metres.
              Stop reveals the latest prepared or edited base aim.</p>
          </>}
          <details><summary>More · order, selection{page === "circle" ? " and reflection" : ""}</summary>
            <label>Participants <select value={p.selection}
              onChange={(e) => change({ selection: e.target.value as EffectParams["selection"] })}>
              <option value="all">All</option><option value="odd">Odd positions</option><option value="even">Even positions</option>
              {page === "chase" && <option value="alternating">Alternating · odd / even opposite phases</option>}
              {page !== "chase" && p.selection === "alternating" && <option value="alternating">Alternating (saved)</option>}
            </select></label>
            <small>Odd / even use 1-based positions below, not DMX addresses. Excluded fixtures retain their base state.</small>
            <OrderedList ids={p.order.length ? p.order : data.fixtures.map((f) => f.id)}
              items={data.fixtures} onChange={(order) => change({ order })} />
            <button type="button" onClick={() => change({ order: [] })}>Use patch order</button>
            <label><input type="checkbox" checked={p.reverse}
              onChange={(e) => change({ reverse: e.target.checked })} />Reverse traversal</label>
            {((page === "chase" && p.shape === "legacy")
              || (page === "rainbow" && draft.kind === "two_colour" && p.colour_mode === "base")) &&
              <NumberField label="Legacy softness" value={p.softness * 100}
                onChange={(softness) => change({ softness: softness / 100 })} />}
            {page === "circle" && <>
              <label>Local pattern reflection <select value={p.mirror}
                onChange={(e) => change({ mirror: e.target.value as EffectParams["mirror"] })}>
                <option value="none">None</option><option value="x">Horizontal · X</option>
                <option value="y">Vertical · Y</option><option value="xy">Both · X + Y</option>
              </select></label>
              <label><input type="checkbox" checked={p.mirror_alternate}
                onChange={(e) => change({ mirror_alternate: e.target.checked })} />Mirror alternate fixtures only</label>
              <small>Local pan / tilt or surface axes, not a room reflection. Legacy Mirror factory looks use
                phase offsets; Spread and geometric reflection are different.</small>
            </>}
          </details>
        </fieldset>
        {errors.length > 0 && <ul className="fx-invalid" role="alert">{errors.map((e) => <li key={e}>{e}</li>)}</ul>}
        {launchProblem && <p className="fx-warnings">{launchProblem} Saved presets can still be edited.</p>}
        {!participants.length && <p className="fx-warnings">Draft selection has no participants; fix selection before Apply.</p>}
        {!embedded && savedErrors.length > 0 && <p className="fx-warnings">Start saved slot unavailable: {savedErrors.join(" ")}</p>}
        {!embedded && !savedParticipantCount && <p className="fx-warnings">Start saved slot unavailable: saved selection has no participants.
          Choose All or Odd, or edit the fixture order and Save first.</p>}
        {!embedded && <p className="muted">Start uses the saved slot, never this draft, and does not toggle off. Store current FX requires
          this selected slot to be running in the current context. Inactive / incompatible strips and movement admission
          are validated by the backend; a rejected action is shown beside the buttons.</p>}
      </>}
  </Frame>;
}
