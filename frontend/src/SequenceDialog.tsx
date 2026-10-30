import { useEffect, useState } from "react";
import {
  deleteSequence, getApc40, getFixtures, getGroups, getScenes, getSequences, getShow, getStatus, saveSequence, sequenceCommand,
  assignApc40SequencePad, clearApc40SequencePad, type Scene, type Sequence, type SequenceStep,
  type FixtureInfo, type GroupInfo, type Apc40Strip,
} from "./api";
import DialogFrame from "./DialogFrame";
import { BEATS, editorScopes, newShowId } from "./showFields";
import { BeatField, NumberField, validNumber } from "./EditorFields";
import SceneDialog from "./SceneDialog";
import SequenceLiveControls from "./SequenceLiveControls";
import "./editor.css";

export interface SequenceDialogProps {
  sequence?: Sequence;
  pad?: number;
  onClose: () => void;
  onSaved?: () => void;
}

export default function SequenceDialog({ sequence, pad, onClose, onSaved }: SequenceDialogProps) {
  const [draft, setDraft] = useState<Sequence>(() => structuredClone(sequence ?? {
    id: newShowId(), name: "", timing: "beats", loop: false, steps: [], live_controls: {},
  }));
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [sequences, setSequences] = useState<Sequence[]>([]);
  const [existing, setExisting] = useState("");
  const [colour, setColour] = useState("green");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [persisted, setPersisted] = useState(!!sequence);
  const [runtimeId, setRuntimeId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [colours, setColours] = useState(["green", "blue", "red", "amber", "purple", "cyan", "white"]);
  const [fixtures, setFixtures] = useState<FixtureInfo[]>([]);
  const [groups, setGroups] = useState<GroupInfo[]>([]);
  const [strips, setStrips] = useState<Apc40Strip[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [creatingScene, setCreatingScene] = useState(false);
  const [preview, setPreview] = useState(false);
  const [baseline, setBaseline] = useState(() => JSON.stringify(draft));
  useEffect(() => {
    let active = true;
    getApc40().then((state) => {
      if (!active) return;
      setStrips(state.strips);
      const colour = state.pads.find((p) => p.pad === pad)?.scene_colour;
      if (colour) setColour(colour);
      if (Object.keys(state.scene_colours).length) setColours(Object.keys(state.scene_colours));
    }).catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [pad]);
  useEffect(() => {
    let active = true;
    Promise.all([getScenes(), getSequences(), getShow(), getFixtures(), getGroups(), getStatus()]).then(([s, q, show, f, g, status]) => {
      if (active) {
        setPreview(status.preview_active);
        setScenes(s); setSequences(q); setRuntimeId(show.sequence?.id ?? null);
        setFixtures(f); setGroups(g); setLoaded(true);
      }
    }).catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);
  const change = (next: Sequence) => { setDraft(next); setSaved(false); };
  const step = (index: number, patch: Partial<SequenceStep>) => change({
    ...draft, steps: draft.steps.map((s, i) => i === index ? { ...s, ...patch } : s),
  });
  const move = (index: number, offset: number) => {
    const steps = [...draft.steps];
    [steps[index], steps[index + offset]] = [steps[index + offset], steps[index]];
    change({ ...draft, steps });
  };
  const perform = async (action: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const validation: string[] = [];
  if (!draft.name.trim()) validation.push("Enter a sequence name.");
  if (draft.name.length > 80) validation.push("Sequence names can have at most 80 characters.");
  if (!draft.steps.length) validation.push("Add at least one scene step.");
  draft.steps.forEach((s, i) => {
    if (loaded && !scenes.some((scene) => scene.id === s.scene_id)) validation.push(`Step ${i + 1}: choose an existing scene.`);
    const valid = draft.timing === "beats" ? BEATS.includes(s.hold) : validNumber(s.hold, 0.01, 600);
    if (!valid) validation.push(`Step ${i + 1}: choose a valid hold after fade.`);
    if (s.fade != null && !(draft.timing === "beats" ? [0, ...BEATS].includes(s.fade) : validNumber(s.fade, 0, 600)))
      validation.push(`Step ${i + 1}: choose a valid incoming fade.`);
  });
  if (loaded) for (const [id, attrs] of Object.entries(draft.live_controls ?? {})) {
    const fixture = fixtures.find((f) => f.id === id);
    if (!fixture || attrs.some((a) => !fixture.attributes.includes(a)))
      validation.push(`Live controls for ${id} contain missing fixture fields. Remove or repair them before saving.`);
  }
  const canSave = loaded && !validation.length;
  const save = async (apply = false) => {
    if (!canSave) return;
    const stored = await saveSequence({ ...draft, name: draft.name.trim() }, !persisted); setSaved(true);
    setPersisted(true); setDraft(stored); setBaseline(JSON.stringify(stored));
    if (pad !== undefined) await assignApc40SequencePad(pad, stored.id, colour);
    onSaved?.();
    if (apply) await sequenceCommand(stored.id, "apply");
  };
  const duration = (value: number, onChange: (value: number) => void, label: string, allowZero = false) =>
    draft.timing === "beats" ? <BeatField label={label} cut={allowZero} value={value} onChange={onChange} />
      : <NumberField label={label} unit="s" min={allowZero ? 0 : 0.01} max={600} value={value} onChange={onChange} />;
  const liveInvalid = loaded && Object.entries(draft.live_controls ?? {}).some(([id, attrs]) => {
    const fixture = fixtures.find((f) => f.id === id);
    return !fixture || attrs.some((a) => !fixture.attributes.includes(a));
  });
  const referenced = scenes.filter((s) => draft.steps.some((step) => step.scene_id === s.id));
  const fadeLabel = (scene?: Scene) => !scene ? "missing scene"
    : (scene.fade_timing ?? "seconds") === "beats" ? `${scene.fade_beats ?? 1} beats` : `${scene.fade_seconds} s`;
  const dirty = JSON.stringify(draft) !== baseline;
  const applyReason = existing ? "Assigning an existing sequence does not refresh a run."
    : !persisted ? "Save, then launch from a pad."
    : runtimeId !== draft.id ? "Only the running sequence can be refreshed here. Launch it from its pad."
    : "";
  const deleteSeq = persisted && <button type="button" className="danger" disabled={busy} onClick={() => {
    if (window.confirm(`Delete "${draft.name}" everywhere?`)) void perform(async () => { await deleteSequence(draft.id); onSaved?.(); onClose(); });
  }}>Delete sequence everywhere</button>;
  const unassign = pad !== undefined && <button type="button" disabled={busy}
    onClick={() => void perform(async () => { await clearApc40SequencePad(pad); onSaved?.(); onClose(); })}>Unassign pad</button>;
  return <DialogFrame id="sequence-dialog" size="medium" dirty={dirty && !existing} busy={busy} onClose={onClose}
    title={`${sequence ? "Edit" : "Create"} sequence${sequence ? ` · ${sequence.name}` : ""}${pad !== undefined ? ` · pad ${pad + 1}` : ""}`}
    context="Editing a sequence definition. Saving never starts or changes a running sequence by itself." preview={preview} saveNote
    status={error ? <div role="alert" className="banner error">{error}</div> : undefined}
    footer={<>
      <button type="submit" form="sequence-form" disabled={busy || (!existing && !canSave)}>
        {saved ? "Saved" : existing ? "Assign sequence" : "Save for next launch"}</button>
      <button type="button" disabled={busy || !!existing || !persisted || runtimeId !== draft.id || !canSave}
        onClick={() => void perform(() => save(true))}>Save &amp; refresh running sequence</button>
      {applyReason && !existing && <small className="dialog-hint muted">Refresh unavailable: {applyReason}</small>}
      {!existing && !canSave && !!validation.length && <small className="dialog-hint editor-error">
        Save unavailable: {validation[0]}{validation.length > 1 ? ` (+${validation.length - 1} more above)` : ""}</small>}
      {(unassign || deleteSeq) && <details className="dialog-more"><summary>More</summary>
        <div className="dialog-more-menu">{unassign}{deleteSeq}</div></details>}
    </>}>
    {creatingScene && <SceneDialog onClose={() => setCreatingScene(false)}
      onSaved={() => { void getScenes().then(setScenes); }} />}
    <p className="muted">Reusable scenes play in order. Each step takes incoming fade + hold after fade.</p>
    {pad !== undefined && !sequence && <label>Assign existing sequence<select value={existing} onChange={(e) => setExisting(e.target.value)}>
      <option value="">Create new below</option>{sequences.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
    </select></label>}
    <form id="sequence-form" noValidate onSubmit={(e) => { e.preventDefault(); void perform(async () => {
      if (existing && pad !== undefined) { await assignApc40SequencePad(pad, existing, colour); onSaved?.(); onClose(); }
      else await save();
    }); }}>
      <fieldset disabled={busy}>
        <label>Name<input required={!existing} maxLength={80} aria-invalid={!draft.name.trim()} value={draft.name} onChange={(e) => change({ ...draft, name: e.target.value })} /></label>
        <label>Timing for entire sequence<select value={draft.timing} onChange={(e) => {
          const timing = e.target.value as Sequence["timing"];
          if (draft.steps.length && !window.confirm(`Switch all timings to ${timing}? The numbers stay exactly as entered; they are not converted.`)) return;
          change({ ...draft, timing });
        }}><option value="seconds">Seconds</option><option value="beats">Beats · follows current BPM immediately</option></select></label>
        <small className="muted">Changing units keeps the entered numbers; it does not convert or round them. Choose supported beat lengths where needed.</small>
        <label>Playback<select value={draft.loop ? "loop" : "once"} onChange={(e) => change({ ...draft, loop: e.target.value === "loop" })}>
          <option value="once">One shot · keep final state</option><option value="loop">Loop</option>
        </select></label>
        {pad !== undefined && <label>Pad colour<select value={colour} onChange={(e) => setColour(e.target.value)}>
          {colours.map((c) => <option key={c}>{c}</option>)}
        </select></label>}
        {draft.timing === "beats" && <p className="muted">Inherited seconds fades round upward to a supported beat length at launch (maximum 32). Zero remains a cut.</p>}
        <ol className="sequence-steps">{draft.steps.map((s, i) => <li key={i} className="sequence-step">
          <label>Scene {i + 1}<select required value={s.scene_id} onChange={(e) => step(i, { scene_id: e.target.value })}>
            {!scenes.some((scene) => scene.id === s.scene_id) && <option value={s.scene_id}>Missing scene · {s.scene_id}</option>}
            {scenes.map((scene) => <option key={scene.id} value={scene.id}>{scene.name}</option>)}
          </select></label>
          <label>Incoming fade<select value={s.fade == null ? "inherit" : "override"} onChange={(e) => step(i, { fade: e.target.value === "inherit" ? null : draft.timing === "beats" ? 1 : 0 })}>
            <option value="inherit">Inherit scene ({fadeLabel(scenes.find((scene) => scene.id === s.scene_id))})</option>
            <option value="override">Override for this step</option>
          </select></label>
          {s.fade != null && duration(s.fade, (fade) => step(i, { fade }), `Step ${i + 1} incoming fade`, true)}
          <div>Hold after fade ({draft.timing === "beats" ? "beats" : "seconds"}){duration(s.hold, (hold) => step(i, { hold }), `Step ${i + 1} hold`)}</div>
          <div className="show-actions">
            <button type="button" disabled={i === 0} onClick={() => move(i, -1)} aria-label={`Move step ${i + 1} up`}>↑</button>
            <button type="button" disabled={i === draft.steps.length - 1} onClick={() => move(i, 1)} aria-label={`Move step ${i + 1} down`}>↓</button>
            <button type="button" onClick={() => change({ ...draft, steps: [...draft.steps.slice(0, i + 1), { ...s }, ...draft.steps.slice(i + 1)] })}>Duplicate</button>
            <button type="button" onClick={() => change({ ...draft, steps: draft.steps.filter((_, n) => n !== i) })}>Remove</button>
          </div>
        </li>)}</ol>
        <button type="button" disabled={!scenes.length} onClick={() => change({
          ...draft, steps: [...draft.steps, { scene_id: scenes[0].id, fade: null, hold: draft.timing === "beats" ? 4 : 1 }],
        })}>Add scene step</button>
        <button type="button" onClick={() => setCreatingScene(true)}>Create new scene…</button>
        {loaded && !scenes.length && <p className="muted">No scenes exist yet. Create one first; your sequence draft is kept.</p>}
        <details className="sequence-live" open={liveInvalid}>
          <summary>Live controls · {Object.values(draft.live_controls ?? {}).reduce((n, attrs) => n + attrs.length, 0)} selected{liveInvalid ? " · needs attention" : ""}</summary>
        <SequenceLiveControls scopes={editorScopes(fixtures, groups, strips)} value={draft.live_controls ?? {}}
          onChange={(live_controls) => change({ ...draft, live_controls })} />
        {loaded && Object.keys(draft.live_controls ?? {}).filter((id) => !fixtures.some((f) => f.id === id)).map((id) =>
          <div className="editor-warning" role="alert" key={id}>Missing fixture: {id}. Saved live controls are retained.
            <button type="button" onClick={() => {
              const next = { ...draft.live_controls }; delete next[id]; change({ ...draft, live_controls: next });
            }}>Remove missing fixture's live controls</button>
          </div>)}
        {loaded && fixtures.flatMap((fixture) => {
          const missing = (draft.live_controls?.[fixture.id] ?? []).filter((a) => !fixture.attributes.includes(a));
          return missing.length ? [<div className="editor-warning" role="alert" key={fixture.id}>
            {fixture.name}: unavailable live-control fields retained: {missing.join(", ")}.
            <button type="button" onClick={() => change({
              ...draft, live_controls: { ...draft.live_controls,
                [fixture.id]: (draft.live_controls?.[fixture.id] ?? []).filter((a) => fixture.attributes.includes(a)) },
            })}>Remove unavailable live controls</button>
          </div>] : [];
        })}
        </details>
        <details><summary>Referenced fixtures / groups</summary>
          {referenced.map((s) => <p key={s.id}>{s.name}: {Object.entries(s.groups).map(([g, a]) => `${g} (group): ${Object.keys(a).join(", ")}`).concat(
            Object.entries(s.fixtures).map(([f, a]) => `${f}: ${Object.keys(a).join(", ")}`),
            Object.keys(s.positions ?? {}).map((f) => `${f}: named position / permissions`),
          ).join("; ")}</p>)}
        </details>
        {!existing && !!validation.length && <div role="status" className="editor-error">{validation.map((message) => <p key={message}>{message}</p>)}</div>}
      </fieldset>
    </form>
  </DialogFrame>;
}
