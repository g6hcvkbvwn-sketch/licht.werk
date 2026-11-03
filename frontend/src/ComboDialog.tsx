import { useEffect, useRef, useState } from "react";
import DialogFrame from "./DialogFrame";
import FxDialog, { validateFx } from "./FxDialog";
import {
  FX_FAMILIES, getComboSlots, resetCombo, saveComboSlot, startComboSlot, stopCombo, storeCombo,
  type ComboDefinition, type ComboSlots, type FxComponent, type FxDefinition, type FxPage,
} from "./fxApi";

const pages: FxPage[] = ["chase", "rainbow", "circle"];
const pageOf = (effect: FxDefinition): FxPage =>
  effect.kind === "two_colour" ? "rainbow" : effect.kind === "figure8" ? "circle" : effect.kind;
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

export default function ComboDialog({ track, stripName, stripActive, preview, component, initialSlot = 0, onClose }: {
  track: number; stripName: string; stripActive: boolean; preview: boolean; component: FxComponent;
  initialSlot?: number; onClose: () => void;
}) {
  const [data, setData] = useState<ComboSlots | null>(null);
  const [draft, setDraft] = useState<ComboDefinition | null>(null);
  const [row, setRow] = useState(initialSlot);
  const [page, setPage] = useState<FxPage>("chase");
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const inFlight = useRef(false);
  const generation = useRef(0);
  const alive = useRef(true);

  const load = (definition: ComboDefinition) => {
    setDraft(structuredClone(definition));
    setRevision((value) => value + 1);
  };
  useEffect(() => {
    alive.current = true;
    let initialized = false;
    let loading = false;
    const refresh = async () => {
      if (inFlight.current || loading) return;
      loading = true;
      const current = generation.current;
      try {
        const next = await getComboSlots(track, component);
        if (!alive.current || current !== generation.current || inFlight.current) return;
        setData(next); setConnected(true);
        if (!initialized) {
          load(next.slots[initialSlot]);
          setPage(pageOf(next.slots[initialSlot].effects[0]));
          initialized = true;
        }
      } catch (e) {
        if (alive.current && current === generation.current) { setError(message(e)); setConnected(false); }
      } finally { loading = false; }
    };
    void refresh();
    const timer = window.setInterval(refresh, 1000);
    return () => { alive.current = false; window.clearInterval(timer); };
  }, [track, component]);

  const perform = async (action: () => Promise<ComboSlots>, text: string, replace = false) => {
    if (inFlight.current) return;
    inFlight.current = true; generation.current++; setBusy(true); setError(""); setNotice("");
    try {
      const next = await action();
      if (!alive.current) return;
      setData(next); setConnected(true); setNotice(text);
      if (replace) load(next.slots[row]);
    } catch (e) { if (alive.current) setError(message(e)); }
    finally { inFlight.current = false; if (alive.current) setBusy(false); }
  };
  const dirty = !!draft && !!data && JSON.stringify(draft) !== JSON.stringify(data.slots[row]);
  const errors = draft && data ? [
    ...(!draft.name.trim() ? ["Enter a Combo name."] : []),
    ...(!draft.effects.length ? ["Enable at least one family."] : []),
    ...draft.effects.flatMap((effect) => {
      const family = pageOf(effect);
      const info = data.families[family];
      return info ? validateFx(effect, family, info).map((text) => `${FX_FAMILIES[family].name}: ${text}`)
        : [`${FX_FAMILIES[family].name} is incompatible with this strip.`];
    }),
  ] : [];
  const ready = connected && data?.preview === preview && !busy;
  const effect = draft?.effects.find((item) => pageOf(item) === page);
  const update = (next: FxDefinition) => {
    setDraft((current) => current && ({ ...current,
      effects: current.effects.map((item) => pageOf(item) === page ? next : item),
    }));
    setNotice("");
  };
  return <DialogFrame id="combo" size="wide" className="fx-dialog" title={`Combo FX · ${stripName}`}
    dirty={dirty} busy={busy} onClose={onClose} preview={preview} saveNote
    context="One bundle, full family controls. Launch replaces this strip's FX on the selected beam/ring component."
    status={<>{error && <div className="banner error" role="alert">{error}</div>}
      {notice && <p role="status">{notice}</p>}</>}
    footer={data && draft ? <>
      <button disabled={!ready || !!errors.length} onClick={() => void perform(
        () => saveComboSlot(track, row, draft, false, component), "Saved for next launch; running FX unchanged.", true,
      )}>Save for next launch</button>
      <button disabled={!ready || !stripActive || !!errors.length} onClick={() => void perform(
        () => saveComboSlot(track, row, draft, true, component), `Saved and applied in ${preview ? "PREVIEW" : "LIVE"}.`, true,
      )}>Save &amp; apply now</button>
      <button disabled={!ready || !stripActive} onClick={() => void perform(
        () => startComboSlot(track, row, component), "Started saved Combo; unsaved draft not applied.",
      )}>Start saved Combo</button>
      <button disabled={!ready || !data.running} onClick={() => void perform(
        () => stopCombo(track, component), "Stopped bundled components; standalone replacements continue.",
      )}>Stop running Combo</button>
      <button disabled={!ready || data.running?.slot !== row} onClick={() => {
        if (dirty && !window.confirm("Replace your draft and saved slot with the running Combo?")) return;
        void perform(() => storeCombo(track, row, component), "Stored current bundle, including live tuning. Detached effects excluded.", true);
      }}>Store current Combo</button>
      <button disabled={!ready} onClick={() => {
        if (window.confirm("Reset this Combo slot to factory? Running effects remain unchanged."))
          void perform(() => resetCombo(track, row, component), "Factory Combo restored.", true);
      }}>Reset to factory</button>
    </> : null}>
    {!data || !draft ? <p>Loading Combo presets…</p> : <>
      <nav className="fx-slots" aria-label="Combo slots">{data.slots.map((slot, index) =>
        <button key={index} disabled={busy} className={row === index ? "active" : ""} onClick={() => {
          if (index === row || (dirty && !window.confirm("Discard unsaved edits and select another Combo?"))) return;
          setRow(index); load(slot); setError(""); setNotice("");
        }}>Slot {index + 1}<small>{slot.name}</small></button>,
      )}</nav>
      <p role="status">{data.running
        ? `${data.running.active ? "Running" : "Suspended"}: ${data.running.definition.name}${data.running.modified ? " · modified" : ""}.`
        : "No running Combo."} Standalone replacement effects are not stopped by Stop Combo.</p>
      <label>Combo name <input disabled={busy} value={draft.name}
        onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
      <nav className="fx-slots" aria-label="Combo families">{pages.filter((family) => data.families[family]).map((family) => {
        const included = draft.effects.some((item) => pageOf(item) === family);
        return <div key={family}>
          <label><input type="checkbox" checked={included} disabled={busy} onChange={(e) => {
            const definition = data.families[family]?.slots[0];
            if (!definition) return;
            setDraft({ ...draft, effects: e.target.checked ? [...draft.effects, structuredClone(definition)]
              : draft.effects.filter((item) => pageOf(item) !== family) });
            setRevision((value) => value + 1);
          }} />Include {FX_FAMILIES[family].name}</label>
          <button className={page === family ? "active" : ""} disabled={busy} onClick={() => setPage(family)}>
            Edit {FX_FAMILIES[family].name}
          </button>
        </div>;
      })}</nav>
      <fieldset disabled={busy}>
        {effect ? <FxDialog key={`${revision}:${page}`} track={track} page={page} stripName={stripName}
          stripActive={stripActive} preview={preview} component={component} onClose={onClose}
          embedded={{ definition: effect, onChange: update }} />
          : <p>This family is disabled in this Combo. Enable it above to edit its settings.</p>}
      </fieldset>
      {!!errors.length && <ul className="fx-invalid" role="alert">{errors.map((text) => <li key={text}>{text}</li>)}</ul>}
      <p className="muted">Use the normal Dimmer, Colour and Move pages for live knob tuning. COLOR edits the running
        Colour palette. Changes remain runtime-only until Store current Combo. Move retains existing permissions and limits.</p>
    </>}
  </DialogFrame>;
}
