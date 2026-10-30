import { useEffect, useRef, useState } from "react";
import {
  FX_FAMILIES, getFxSlots, resetFxSlot, saveFxSlot, startFxSlot, stopFxFamily, storeCurrentFx,
  type EffectParams, type FxDefinition, type FxPage, type FxSlots, type FxComponent,
} from "./fxApi";
import DialogFrame from "./DialogFrame";
import "./fx.css";

const BEATS = [0.25, 0.5, 1, 2, 4, 8, 16, 32];
const GENERATOR_NAMES = {
  chase: "Dimmer", rainbow: "Rainbow", two_colour: "Two colours", circle: "Circle", figure8: "Figure 8",
};
const valid = (value: number, min: number, max: number) =>
  Number.isFinite(value) && value >= min && value <= max;
const percent = (value: number) => Number.isFinite(value) ? `${Math.round(value * 1000) / 10}%` : "—";
const message = (e: unknown) => e instanceof Error ? e.message : String(e);

function validate(definition: FxDefinition, page: FxPage, data: FxSlots): string[] {
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
  if (new Set(p.target_ids).size !== p.target_ids.length
    || p.target_ids.some((id) => !data.targets.some((t) => t.id === id)))
    errors.push("Target Tour contains missing or duplicate targets. Remove them or choose existing targets.");
  if (page === "circle" && p.target_ids.length && p.pattern === "current")
    errors.push("Choose None, Circle or Figure 8 for Target Tour holds.");
  if (page === "circle" && !p.target_ids.length && p.pattern !== "current")
    errors.push("Add a Target Tour destination, or switch to Current aim / included areas.");
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

interface Props {
  track: number;
  page: FxPage;
  stripName: string;
  stripActive: boolean;
  preview: boolean;
  component?: FxComponent;
  initialSlot?: number;
  onClose: () => void;
}

export default function FxDialog({ track, page, stripName, stripActive, preview, component = "beam", initialSlot = 0, onClose }: Props) {
  const [data, setData] = useState<FxSlots | null>(null);
  const [draft, setDraft] = useState<FxDefinition | null>(null);
  const [row, setRow] = useState(initialSlot);
  const [factory, setFactory] = useState("");
  const [tour, setTour] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const inFlight = useRef(false);
  const generation = useRef(0);
  const alive = useRef(true);
  const family = FX_FAMILIES[page];

  const loadDraft = (definition: FxDefinition) => {
    setDraft(structuredClone(definition));
    setTour(page === "circle" && definition.params.target_ids.length > 0);
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
          loadDraft(next.slots[initialSlot] ?? next.slots[0]);
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

  const perform = async (action: () => Promise<FxSlots>, text: string, replaceDraft = false) => {
    if (inFlight.current) return;
    generation.current++;
    inFlight.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const next = await action();
      if (alive.current) {
        setData(next); setConnected(true); setNotice(text);
        if (replaceDraft) loadDraft(next.slots[row]);
      }
    } catch (e) { if (alive.current) setError(message(e)); }
    finally {
      inFlight.current = false;
      if (alive.current) setBusy(false);
    }
  };
  const p = draft?.params;
  const change = (params: Partial<EffectParams>) => {
    if (draft) { setDraft({ ...draft, params: { ...draft.params, ...params } }); setNotice(""); }
  };
  const errors = draft && data ? validate(draft, page, data) : [];
  if (tour && p && !p.target_ids.length) errors.push("Add at least one Target Tour destination.");
  const dirty = !!draft && !!data && JSON.stringify(draft) !== JSON.stringify(data.slots[row]);
  const contextReady = connected && data?.preview === preview;
  const savedErrors = data?.slots[row] ? validate(data.slots[row], page, data) : [];
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
      void perform(() => storeCurrentFx(track, page, row, component), "Current FX stored. Base / scene values were not captured.", true);
    }}>Store current FX in this slot</button>
    <button type="button" disabled={busy || !connected} onClick={() => {
      if (window.confirm(`Reset slot ${row + 1} to factory? Saved edits and this draft will be replaced; running FX stays unchanged.`))
        void perform(() => resetFxSlot(track, page, row, component), "Factory definition restored; running snapshot unchanged.", true);
    }}>Reset to factory</button>
  </>;
  const target = preview ? "Preview" : "Live";
  return <DialogFrame id="fx" size="medium" className="fx-dialog" dirty={dirty} busy={busy} onClose={onClose} saveNote preview={preview}
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
      <button type="button" disabled={!canSave || !canRun || !participants.length} onClick={() => void perform(
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
        <nav className="fx-slots" aria-label="FX preset slots">
          {Array.from({ length: 5 }, (_, index) => <button key={index} type="button"
            disabled={busy || !data.slots[index]} aria-pressed={row === index}
            className={row === index ? "active" : ""} onClick={() => {
              if (index === row || (dirty && !window.confirm("Discard unsaved edits and select another slot?"))) return;
              setRow(index); loadDraft(data.slots[index]); setNotice(""); setError("");
            }}>Slot {index + 1}<small>{data.slots[index]?.name ?? "Unavailable"}</small>
            <small>{row === index ? "Selected" : ""}{row === index && data.running?.slot === index ? " · " : ""}{data.running?.slot === index ? "Running" : ""}</small></button>)}
        </nav>
        <p className="fx-running" role="status">{data.running
          ? `${data.running.active ? "Running" : "Suspended / inactive"}: slot ${data.running.slot + 1} · ${data.running.definition.name}${matchingRun ? "" : " (another slot)"}.`
          : "No running FX in this family (stopped or replaced)."}
          {" "}{dirty ? "Unsaved draft." : "Showing saved definition."}</p>
        {data.warnings.length > 0 && <ul className="fx-warnings">{data.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
        <fieldset disabled={busy} className="fx-fields">
          <label>Preset name <input value={draft.name} aria-invalid={!draft.name.trim()}
            onChange={(e) => { setDraft({ ...draft, name: e.target.value }); setNotice(""); }} /></label>
          <label>Load factory preset <select value={factory} onChange={(e) => {
            const value = e.target.value;
            if (!value) return;
            loadDraft(data.factory[Number(value)]); setFactory(value); setNotice("Factory preset loaded into draft only. Save to keep it.");
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
            {p.timing === "beats" ? <label>Rate <select value={p.beats} onChange={(e) => change({ beats: Number(e.target.value) })}>
              {!BEATS.includes(p.beats) && <option value={p.beats}>{p.beats} (saved value)</option>}
              {BEATS.map((beat) => <option key={beat} value={beat}>{beat} beats / cycle</option>)}
            </select></label> : <NumberField label="Rate" value={p.seconds} min={0.25} max={120}
              unit="seconds / cycle" onChange={(seconds) => change({ seconds })} />}
            <NumberField label={page === "chase" ? "Depth" : "Size"} value={p.size * 100}
              disabled={page === "chase" && p.maximum === 0} onChange={(size) => change({ size: size / 100 })} />
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
              Size controls hue range / distance. Scene and manual base colours remain in charge.</p> : <>
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
            <label>Destination <select value={tour ? "tour" : "current"} onChange={(e) => {
              const enabled = e.target.value === "tour";
              setTour(enabled); change({ target_ids: [], pattern: enabled ? "none" : "current" });
            }}><option value="current">Current aim / included areas</option><option value="tour">Target Tour</option></select></label>
            {tour ? <section className="fx-tour">
              <h3>Ordered Target Tour destinations</h3>
              <OrderedList ids={p.target_ids} items={data.targets} removable onChange={(target_ids) => change({ target_ids })} />
              <label>Add destination <select value="" onChange={(e) => {
                if (e.target.value) change({ target_ids: [...p.target_ids, e.target.value] });
              }}><option value="">Choose a named target…</option>
                {data.targets.filter((t) => !p.target_ids.includes(t.id)).map((t) =>
                  <option value={t.id} key={t.id}>{t.name}</option>)}
              </select></label>
              <NumberField label="Travel" value={p.travel} positive max={120} unit={p.timing}
                onChange={(travel) => change({ travel })} />
              <NumberField label="Hold" value={p.hold} positive max={120} unit={p.timing}
                onChange={(hold) => change({ hold })} />
              <label>Pattern during holds <select value={p.pattern}
                onChange={(e) => change({ pattern: e.target.value as EffectParams["pattern"] })}>
                <option value="none">None · stationary hold</option><option value="circle">Circle</option>
                <option value="figure8">Figure 8</option>
              </select></label>
              <p>Each destination uses Travel then Hold in {p.timing}. Optional patterns run only during holds,
                never during transfers. Device knob 1 is labelled Travel and edits Travel duration during a tour.
                The editor's cycle Rate controls the hold pattern and stays independent of Travel / Hold durations.</p>
            </section> : <p>Anchor: current target / included areas. Configure these in the existing Aim / Permissions controls
              (SHIFT + a head strip's TRACK SELECT). With multiple included areas and no selected target,
              movement tours the areas with dark transfers.</p>}
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
        {savedErrors.length > 0 && <p className="fx-warnings">Start saved slot unavailable: {savedErrors.join(" ")}</p>}
        {!savedParticipantCount && <p className="fx-warnings">Start saved slot unavailable: saved selection has no participants.
          Choose All or Odd, or edit the fixture order and Save first.</p>}
        <p className="muted">Start uses the saved slot, never this draft, and does not toggle off. Store current FX requires
          this selected slot to be running in the current context. Inactive / incompatible strips and movement admission
          are validated by the backend; a rejected action is shown beside the buttons.</p>
      </>}
  </DialogFrame>;
}
