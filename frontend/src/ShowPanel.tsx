import { useCallback, useEffect, useState } from "react";
import {
  getScenes, getSequences, getShow, recallScene, sequenceCommand, setQuantize, setLiveModifiers,
  type LiveModifiers,
  type Scene, type Sequence, type SequenceRuntime, type ShowState,
} from "./api";
import SceneDialog from "./SceneDialog";
import SequenceDialog from "./SequenceDialog";
import { useBeatPulse } from "./beat";
import { sequenceCountdown } from "./sequenceCountdown";

type Command = "pause" | "resume" | "stop" | "restart" | "next";

function Playback({ runtime, label, preview, run, beatOn, nextQueued }: {
  runtime: SequenceRuntime | null; label: string; preview?: boolean; beatOn: boolean; nextQueued?: boolean;
  run: (id: string, command: Command, preview?: boolean) => void;
}) {
  if (!runtime) return <div className="sequence-playback"><strong className="sequence-context">{label}</strong><span className="muted">No sequence</span></div>;
  const active = runtime.status === "running" || runtime.status === "paused";
  return <div className={`sequence-playback ${runtime.status}`}>
    <strong className="sequence-context">{label}</strong>
    <span className="sequence-summary" title={`${runtime.name} · ${runtime.status} · ${runtime.loop ? "loop" : "one shot"}`}>
      <strong>{runtime.name}</strong> · {runtime.status} · step {runtime.step_index + 1}{runtime.step_count ? `/${runtime.step_count}` : ""}{runtime.scene_name ? `: ${runtime.scene_name}` : ""}
    </span>
    {active && runtime.waiting
      ? <strong className={beatOn ? "sequence-waiting on" : "sequence-waiting"} title="Flashes on the beat">WAITING FOR NEXT</strong>
      : <span className="sequence-countdown">{active && runtime.until_next != null
        ? `next in ${sequenceCountdown(runtime.until_next, runtime.timing, runtime.countdown_step)}`
        : `${runtime.phase} ${sequenceCountdown(runtime.remaining ?? 0, runtime.timing, runtime.countdown_step)}`}</span>}
    {active && runtime.next_scene_name && <span className="sequence-next">→ {runtime.next_scene_name}</span>}
    <progress max={1} value={runtime.progress} aria-label={`${label} ${runtime.phase} progress`} />
    <div className="show-actions" role="group" aria-label={`${label} sequence transport`}>
      {active && <button className={nextQueued ? "sequence-next-button apc-queued waiting"
        : runtime.waiting && beatOn ? "sequence-next-button waiting" : "sequence-next-button"}
        title="NEXT (SCENE LAUNCH 5): go to the next step now, also from a timed step"
        onClick={() => run(runtime.id, "next", preview)}>NEXT</button>}
      {active && <button onClick={() => run(runtime.id, runtime.status === "paused" ? "resume" : "pause", preview)}>
        {runtime.status === "paused" ? "Resume" : "Pause"}</button>}
      <button aria-label={`Stop ${label.toLowerCase()} sequence`} onClick={() => run(runtime.id, "stop", preview)}>Stop</button>
      <button aria-label={`Restart ${label.toLowerCase()} sequence`} onClick={() => run(runtime.id, "restart", preview)}>Restart</button>
    </div>
    {runtime.error && <span role="alert" className="error">{runtime.error}</span>}
  </div>;
}

/** Remains mounted above every page, including the controller. */
export function ShowStatusStrip({ preview = false, onState }: { preview?: boolean; onState?: (state: ShowState) => void }) {
  const [show, setShow] = useState<ShowState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [modifierBusy, setModifierBusy] = useState(false);
  const [tintDraft, setTintDraft] = useState<string | null>(null);
  const load = useCallback(async () => { const state = await getShow(); setShow(state); onState?.(state); }, [onState]);
  useEffect(() => {
    let active = true;
    const poll = async () => {
      try { const state = await getShow(); if (active) { setShow(state); onState?.(state); setError(null); } }
      catch (e) { if (active) setError(e instanceof Error ? e.message : String(e)); }
    };
    void poll();
    const timer = window.setInterval(poll, 250);
    return () => { active = false; window.clearInterval(timer); };
  }, [onState]);
  const action = async (id: string, command: Command, context?: boolean) => {
    try { await sequenceCommand(id, command, context); await load(); setError(null); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  const toggleQuantize = async () => {
    try { setShow(await setQuantize(!show?.quantize?.enabled)); setError(null); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  const changeModifiers = async (patch: Partial<Omit<LiveModifiers, "choices">>) => {
    if (!show?.modifiers || modifierBusy) return;
    setModifierBusy(true);
    try {
      const { speed, sequence_speed, tint, excluded_strips } = show.modifiers;
      const state = await setLiveModifiers({ speed, sequence_speed, tint, excluded_strips, ...patch });
      setShow(state);
      onState?.(state);
      setError(null);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setModifierBusy(false); }
  };
  const beatOn = useBeatPulse();
  const queued = show?.quantize?.queued ?? [];
  const nextQueued = queued.includes("NEXT");
  return <section className="show-status" aria-label="Sequence playback">
    <div className="sequence-statuses">
      <Playback label="LIVE" runtime={preview ? show?.live_sequence ?? null : show?.sequence ?? null}
        preview={false} beatOn={beatOn} nextQueued={nextQueued} run={(id, c, p) => void action(id, c, p)} />
      {preview && <Playback label="PREVIEW" runtime={show?.sequence ?? null} preview beatOn={beatOn} nextQueued={nextQueued} run={(id, c, p) => void action(id, c, p)} />}
      {show?.quantize?.available && <div className="quantize-status">
        <button className={show.quantize.enabled ? "quantize-toggle on" : "quantize-toggle"}
          title="Quantize (METRONOME): looks, accents, sequence pads and NEXT fire on the next beat"
          onClick={() => void toggleQuantize()}>Quantize: {show.quantize.enabled ? "next beat" : "off"}</button>
        {queued.length > 0 && <strong className="quantize-queued apc-queued">Queued: {queued.join(", ")}</strong>}
      </div>}
    </div>
    {show?.modifiers && <fieldset className="live-modifiers" disabled={modifierBusy}>
      <legend>LIVE MODIFIERS · always live, also in preview</legend>
      <div className="modifier-speed" role="group" aria-label="FX speed">
      <span>FX speed</span>
      {([0.25, 0.5, 1, 2, 4] as const).map((speed) => <button type="button" key={speed}
        aria-pressed={show.modifiers?.speed === speed}
        onClick={() => void changeModifiers({ speed })}>{speed === 0.25 ? "¼×" : speed === 0.5 ? "½×" : `${speed}×`}</button>)}
      </div>
      <div className="modifier-speed" role="group" aria-label="Sequence speed">
      <span title="Scales timed holds only; fades and WAIT FOR NEXT are unchanged">Sequence speed</span>
      {([0.25, 0.5, 1, 2, 4] as const).map((sequence_speed) => <button type="button" key={`sequence-${sequence_speed}`}
        title="Sequence hold speed: ¼× is slower, 4× is faster; fades stay unchanged"
        aria-pressed={show.modifiers?.sequence_speed === sequence_speed}
        onClick={() => void changeModifiers({ sequence_speed })}>{sequence_speed === 0.25 ? "¼×" : sequence_speed === 0.5 ? "½×" : `${sequence_speed}×`}</button>)}
      </div>
      <label>Tint <input type="color" aria-label="Live tint colour" value={tintDraft ?? show.modifiers.tint ?? "#ffffff"}
        onChange={(e) => setTintDraft(e.target.value)} /></label>
      <button type="button" onClick={() => void changeModifiers({ tint: tintDraft ?? show.modifiers?.tint ?? "#ffffff" })}>Apply tint</button>
      {show.modifiers.tint && <span className="live-tint-active" style={{ borderColor: show.modifiers.tint }}>
        Tint {show.modifiers.tint}
      </span>}
      <button type="button" aria-pressed={show.modifiers.tint === null}
        onClick={() => void changeModifiers({ tint: null })}>Tint off</button>
      <details>
        <summary>{show.modifiers.excluded_strips.length
          ? `Tint exclusions (${show.modifiers.excluded_strips.length})`
          : "Tint: all lighting"}</summary>
        <p className="muted">Check fixtures/groups to exclude from tint. FX speed stays global.</p>
        {show.modifiers.choices.map((choice) => <label key={choice.track}>
          <input type="checkbox" checked={show.modifiers?.excluded_strips.includes(choice.track) ?? false}
            onChange={(e) => void changeModifiers({ excluded_strips: e.target.checked
              ? [...show.modifiers!.excluded_strips, choice.track]
              : show.modifiers!.excluded_strips.filter((track) => track !== choice.track) })} />Exclude {choice.label}
        </label>)}
      </details>
      <button type="button" onClick={() => void changeModifiers({ speed: 1, sequence_speed: 1, tint: null })}>Reset modifiers</button>
    </fieldset>}
    {show?.look_notice && <div className="error look-blocked" role="status">Blocked: {show.look_notice}</div>}
    {error && <div className="error" role="alert">{error}</div>}
  </section>;
}

export default function ShowPanel({ onScenesChanged }: { onScenesChanged?: () => void }) {
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [sequences, setSequences] = useState<Sequence[]>([]);
  const [dialog, setDialog] = useState<{ kind: "scene"; scene?: Scene } | { kind: "sequence"; sequence?: Sequence } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [running, setRunning] = useState<SequenceRuntime | null>(null);
  useEffect(() => {
    const poll = () => { getShow().then((state) => setRunning(state.sequence)).catch(() => undefined); };
    poll();
    const timer = window.setInterval(poll, 1000);
    return () => window.clearInterval(timer);
  }, []);
  const matches = (name: string) => name.toLowerCase().includes(query.trim().toLowerCase());
  const shownScenes = scenes.filter((scene) => matches(scene.name));
  const shownSequences = sequences.filter((sequence) => matches(sequence.name));
  const refresh = useCallback(async () => {
    const [s, q] = await Promise.all([getScenes(), getSequences()]);
    setScenes(s); setSequences(q); onScenesChanged?.();
  }, [onScenesChanged]);
  useEffect(() => {
    let active = true;
    Promise.all([getScenes(), getSequences()]).then(([s, q]) => {
      if (active) { setScenes(s); setSequences(q); }
    }).catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);
  const launch = async (action: () => Promise<unknown>) => {
    try { await action(); setError(null); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  const launchSequence = async (sequence: Sequence) => {
    const show = await getShow();
    const runtime = show.sequence;
    await sequenceCommand(sequence.id, runtime?.id === sequence.id && runtime.status === "running" ? "pause"
      : runtime?.id === sequence.id && runtime.status === "paused" ? "resume" : "start");
  };
  return <section className="show-library">
    <input type="search" className="show-search" aria-label="Search looks and sequences" placeholder="Search looks and sequences by name"
      value={query} onChange={(e) => setQuery(e.target.value)} />
    <header><h2>Looks</h2><button onClick={() => setDialog({ kind: "scene" })}>Create look</button></header>
    <p className="muted">Partial looks coexist. Only overlapping controlled attributes stop a sequence. Click to recall; Shift-click or Edit opens saved values.</p>
    <div className="scene-grid">{shownScenes.map((scene) => <div key={scene.id} className="scene">
      <button className="scene-recall" onClick={(e) => e.shiftKey ? setDialog({ kind: "scene", scene }) : void launch(() => recallScene(scene.id))}
        title={Object.keys(scene.groups).map((g) => `${g} (group)`).concat(Object.keys(scene.fixtures), Object.keys(scene.positions ?? {})).join(", ")}>
        {scene.name}<small className="scope-badges">{Object.keys(scene.groups).map((g) => `G · ${g}`).concat(
          [...new Set([...Object.keys(scene.fixtures), ...Object.keys(scene.positions ?? {})])],
        ).join(" / ")}</small><small>Fade {scene.fade_timing === "beats" ? `${scene.fade_beats} beats` : `${scene.fade_seconds}s`}</small>
      </button><button onClick={() => setDialog({ kind: "scene", scene })}>Edit</button>
    </div>)}</div>
    {query && shownScenes.length === 0 && <p className="muted">No look matches “{query}”.</p>}
    <header><h2>Sequences</h2><button onClick={() => setDialog({ kind: "sequence" })}>Create sequence</button></header>
    <div className="scene-grid">{shownSequences.map((sequence) => <div key={sequence.id} className="scene">
      <button className="scene-recall" onClick={(e) => e.shiftKey ? setDialog({ kind: "sequence", sequence }) : void launch(() => launchSequence(sequence))}>
        {sequence.name}{running?.id === sequence.id && (running.status === "running" || running.status === "paused") && <small className="running-badge">● {running.status}</small>}<small>{sequence.steps.length} steps · {sequence.timing} · {sequence.loop ? "loop" : "one shot"}</small>
      </button><button onClick={() => setDialog({ kind: "sequence", sequence })}>Edit</button>
    </div>)}</div>
    {query && shownSequences.length === 0 && <p className="muted">No sequence matches “{query}”.</p>}
    {error && <p className="banner error" role="alert">{error}</p>}
    {dialog?.kind === "scene" && <SceneDialog scene={dialog.scene} onClose={() => setDialog(null)} onSaved={() => void refresh().catch((e: Error) => setError(e.message))} />}
    {dialog?.kind === "sequence" && <SequenceDialog sequence={dialog.sequence} onClose={() => setDialog(null)} onSaved={() => void refresh().catch((e: Error) => setError(e.message))} />}
  </section>;
}
