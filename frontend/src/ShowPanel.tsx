import { useCallback, useEffect, useState } from "react";
import {
  getScenes, getSequences, getShow, recallScene, sequenceCommand,
  type Scene, type Sequence, type SequenceRuntime, type ShowState,
} from "./api";
import SceneDialog from "./SceneDialog";
import SequenceDialog from "./SequenceDialog";

type Command = "pause" | "resume" | "stop" | "restart";

function Playback({ runtime, label, preview, run }: {
  runtime: SequenceRuntime | null; label: string; preview?: boolean;
  run: (id: string, command: Command, preview?: boolean) => void;
}) {
  if (!runtime) return <div className="sequence-playback"><strong className="sequence-context">{label}</strong><span className="muted">No sequence</span></div>;
  const active = runtime.status === "running" || runtime.status === "paused";
  return <div className={`sequence-playback ${runtime.status}`}>
    <strong className="sequence-context">{label}</strong>
    <span className="sequence-summary" title={`${runtime.name} · ${runtime.status} · ${runtime.loop ? "loop" : "one shot"}`}>
      <strong>{runtime.name}</strong> · {runtime.status} · {runtime.step_index + 1}{runtime.scene_name ? `: ${runtime.scene_name}` : ""}
    </span>
    <span className="sequence-countdown">{runtime.phase} {runtime.remaining !== undefined
      ? `${Math.max(0, runtime.remaining).toFixed(1)} ${runtime.timing === "beats" ? "beats" : "s"}`
      : `${Math.round(Math.max(0, 1 - runtime.progress) * 100)}%`}</span>
    <progress max={1} value={runtime.progress} aria-label={`${label} ${runtime.phase} progress`} />
    <div className="show-actions" role="group" aria-label={`${label} sequence transport`}>
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
  return <section className="show-status" aria-label="Sequence playback">
    <div className="sequence-statuses">
      <Playback label="LIVE" runtime={preview ? show?.live_sequence ?? null : show?.sequence ?? null}
        preview={false} run={(id, c, p) => void action(id, c, p)} />
      {preview && <Playback label="PREVIEW" runtime={show?.sequence ?? null} preview run={(id, c, p) => void action(id, c, p)} />}
    </div>
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
    <input type="search" className="show-search" aria-label="Search scenes and sequences" placeholder="Search scenes and sequences by name"
      value={query} onChange={(e) => setQuery(e.target.value)} />
    <header><h2>Scenes</h2><button onClick={() => setDialog({ kind: "scene" })}>Create scene</button></header>
    <p className="muted">Partial scenes coexist. Only overlapping controlled attributes stop a sequence. Click to recall; Shift-click or Edit opens saved values.</p>
    <div className="scene-grid">{shownScenes.map((scene) => <div key={scene.id} className="scene">
      <button className="scene-recall" onClick={(e) => e.shiftKey ? setDialog({ kind: "scene", scene }) : void launch(() => recallScene(scene.id))}
        title={Object.keys(scene.groups).map((g) => `${g} (group)`).concat(Object.keys(scene.fixtures), Object.keys(scene.positions ?? {})).join(", ")}>
        {scene.name}<small className="scope-badges">{Object.keys(scene.groups).map((g) => `G · ${g}`).concat(
          [...new Set([...Object.keys(scene.fixtures), ...Object.keys(scene.positions ?? {})])],
        ).join(" / ")}</small><small>Fade {scene.fade_timing === "beats" ? `${scene.fade_beats} beats` : `${scene.fade_seconds}s`}</small>
      </button><button onClick={() => setDialog({ kind: "scene", scene })}>Edit</button>
    </div>)}</div>
    {query && shownScenes.length === 0 && <p className="muted">No scene matches “{query}”.</p>}
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
