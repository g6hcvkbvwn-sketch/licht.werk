import { useEffect, useMemo, useRef, useState } from "react";
import {
  ConnectionError,
  captureScene,
  getController,
  getMidi,
  putMidiBindings,
  sendControllerEvent,
  type ActionType,
  type ControllerState,
  type Scene,
  type SurfaceControl,
} from "./api";
import { Fader, Knob, Pad } from "./K2Controls";
import RoomMap from "./RoomMap";

// Actions that still act on the real output while preview mode is on.
const LIVE_ACTIONS = new Set<string>(["master", "blackout", "flash", "strobe_hold"]);

interface Props {
  scenes: Scene[];
  onScenesChanged: () => void;
}

/**
 * Sends on-screen control events in order without flooding the backend: while one request is
 * in flight, newer pot/fader positions replace older ones and encoder steps are added up.
 */
class EventSender {
  private queue: { kind: "note" | "cc"; number: number; value: number }[] = [];
  private latest = new Map<number, number>(); // cc number -> absolute value
  private steps = new Map<number, number>(); // cc number -> pending relative steps
  private busy = false;
  constructor(private onError: (e: unknown) => void) {}

  note(number: number, velocity: number) {
    this.queue.push({ kind: "note", number, value: velocity });
    void this.pump();
  }
  absolute(number: number, value: number) {
    this.latest.set(number, value);
    void this.pump();
  }
  relative(number: number, steps: number) {
    this.steps.set(number, (this.steps.get(number) ?? 0) + steps);
    void this.pump();
  }

  private next() {
    const queued = this.queue.shift();
    if (queued) return queued;
    for (const [number, value] of this.latest) {
      this.latest.delete(number);
      return { kind: "cc" as const, number, value };
    }
    for (const [number, total] of this.steps) {
      // Two's complement relative value, at most 63 steps per message.
      const step = Math.max(-63, Math.min(63, total));
      if (total - step === 0) this.steps.delete(number);
      else this.steps.set(number, total - step);
      if (step !== 0) return { kind: "cc" as const, number, value: step > 0 ? step : 128 + step };
    }
    return null;
  }

  private async pump() {
    if (this.busy) return;
    this.busy = true;
    try {
      for (let e = this.next(); e; e = this.next()) {
        try {
          await sendControllerEvent(e.kind, e.number, e.value);
        } catch (error) {
          this.onError(error);
        }
      }
    } finally {
      this.busy = false;
    }
  }
}

const rgbCss = (c: [number, number, number] | null) =>
  c ? `rgb(${c.map((v) => Math.round(v * 255)).join(",")})` : "transparent";

const controlName = (c: SurfaceControl) => {
  const where = c.area === "strip" ? `column ${c.column}` : c.area === "matrix" ? "matrix" : "bottom row";
  const names: Record<string, string> = {
    encoder: "Encoder", "encoder-push": "Encoder push", "pot-1": "Pot 1", "pot-2": "Pot 2",
    "pot-3": "Pot 3", "switch-1": "Switch 1", "switch-2": "Switch 2", "switch-3": "Switch 3",
    fader: "Fader",
  };
  const name = c.label ? `Button ${c.label}` : names[c.row] ?? c.id;
  return `${name}, ${where} (${c.kind === "cc" ? "CC" : "note"} ${c.number})`;
};

export default function ControllerView({ scenes, onScenesChanged }: Props) {
  const [state, setState] = useState<ControllerState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [assignMode, setAssignMode] = useState(false);
  // The side pane (scene capture, assign controls) starts collapsed to give the surface room.
  const [panelOpen, setPanelOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const sender = useRef(
    new EventSender((e) => {
      if (!(e instanceof ConnectionError)) setError(e instanceof Error ? e.message : String(e));
    }),
  ).current;

  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const next = await getController();
        if (!stop) setState(next);
      } catch {
        // The app header shows lost connections; keep the last state.
      }
    };
    void load();
    const timer = window.setInterval(load, 250);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, []);

  const byId = useMemo(() => new Map(state?.controls.map((c) => [c.id, c]) ?? []), [state]);
  if (!state) return <p className="muted">Loading controller…</p>;

  const columnTarget = (column: number) => state.columns[column - 1]?.target ?? null;
  const select = (id: string) => setSelectedId(id);
  const common = (c: SurfaceControl) => ({
    assignMode,
    selected: assignMode && selectedId === c.id,
    onSelect: () => select(c.id),
    live: state.preview && LIVE_ACTIONS.has(c.action?.type ?? ""),
  });
  // Show the target only where it differs from the column it sits in.
  const sub = (c: SurfaceControl) =>
    c.action && c.area === "strip" && c.action.target === columnTarget(c.column) ? "" : c.target_label;

  const knob = (id: string) => {
    const c = byId.get(id)!;
    return (
      <Knob
        {...common(c)}
        value={c.value}
        endless={c.relative}
        hue={c.action?.type === "hue"}
        label={c.action_label}
        sublabel={sub(c)}
        onValue={(v) => sender.absolute(c.number, v)}
        onSteps={(s) => sender.relative(c.number, s)}
      />
    );
  };
  const pad = (id: string, className = "") => {
    const c = byId.get(id)!;
    const isScene = c.action?.type === "scene";
    return (
      <Pad
        {...common(c)}
        led={c.led}
        label={isScene ? c.target_label : c.action_label}
        sublabel={isScene ? "" : sub(c)}
        corner={c.area === "matrix" ? c.label : undefined}
        className={className}
        onPress={(v) => sender.note(c.number, v)}
      />
    );
  };

  const selected = selectedId ? byId.get(selectedId) ?? null : null;

  return (
    <div className={panelOpen ? "controller" : "controller panel-collapsed"}>
      <div className="k2-strips">
        {state.columns.map((col) => {
          const n = col.column;
          const fader = byId.get(`fader-${n}`)!;
          return (
            <div className="strip" key={n}>
              <div className="strip-title">
                <span className="swatch" style={{ background: rgbCss(col.colour) }} />
                <span>{col.title}</span>
              </div>
              {knob(`encoder-${n}`)}
              {pad(`encoder-${n}-push`, "small")}
              {knob(`pot-1-${n}`)}
              {pad(`switch-1-${n}`, "small")}
              {knob(`pot-2-${n}`)}
              {pad(`switch-2-${n}`, "small")}
              {knob(`pot-3-${n}`)}
              {pad(`switch-3-${n}`, "small")}
              <Fader
                {...common(fader)}
                value={fader.value}
                label={fader.action_label}
                sublabel={fader.action?.type === "master" ? "" : sub(fader)}
                onValue={(v) => sender.absolute(fader.number, v)}
              />
            </div>
          );
        })}
      </div>

      <div className="k2-right">
        <div className="matrix">
          {"ABCDEFGHIJKLMNOP".split("").map((letter) => (
            <div key={letter}>{pad(`matrix-${letter}`)}</div>
          ))}
        </div>
        <div className="bottom-row">
          {pad("layer")}
          <div className="bottom-encoder">{knob("encoder-left")}</div>
          <div className="bottom-encoder">{knob("encoder-right")}</div>
          {pad("exit-setup", state.blackout ? "blackout-pad" : "")}
        </div>
        <div className="legend muted">
          <span className="dot amber" /> assigned <span className="dot green" /> active look{" "}
          <span className="dot red" /> held / blackout · Preview/Go: green = live, amber = preview
        </div>
      </div>

      {!panelOpen ? (
        <aside className="side-panel collapsed">
          <button
            className="panel-handle"
            title="Show look capture and ASSIGN CONTROLS"
            onClick={() => setPanelOpen(true)}
          >
            ◀ SCENES · ASSIGN
          </button>
          {error && (
            <button className="panel-error" title={error} onClick={() => setPanelOpen(true)}>
              !
            </button>
          )}
        </aside>
      ) : (
      <aside className="side-panel">
        <button
          className="panel-close"
          title="Hide this pane"
          onClick={() => {
            setPanelOpen(false);
            setAssignMode(false);
            setSelectedId(null);
          }}
        >
          HIDE ▶
        </button>
        {error && (
          <div className="banner error small-banner" onClick={() => setError(null)}>
            {error}
          </div>
        )}
        <button
          className={assignMode ? "assign-toggle active" : "assign-toggle"}
          onClick={() => {
            setAssignMode(!assignMode);
            setSelectedId(null);
          }}
        >
          {assignMode ? "DONE ASSIGNING" : "ASSIGN CONTROLS"}
        </button>
        {assignMode ? (
          <AssignPanel
            control={selected}
            state={state}
            scenes={scenes}
            onError={setError}
          />
        ) : (
          <>
            <CapturePanel scenes={scenes} activeScene={state.active_scene} onSaved={onScenesChanged} onError={setError} />
            <RoomMap compact />
          </>
        )}
        <p className="muted hint">
          Same as the Xone:K2 on MIDI channel {state.channel}: moving a control here or on the
          hardware does the same thing.
        </p>
      </aside>
      )}
    </div>
  );
}

function CapturePanel(props: {
  scenes: Scene[];
  activeScene: string | null;
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  const [name, setName] = useState("");
  const [fade, setFade] = useState(0);
  const active = props.scenes.find((s) => s.id === props.activeScene);
  return (
    <div className="panel-block">
      <div className="panel-title">Active look</div>
      <div className="active-scene">{active ? active.name : "—"}</div>
      <form
        className="side-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!name.trim()) return;
          captureScene(name.trim(), fade)
            .then(() => {
              setName("");
              props.onSaved();
            })
            .catch((err: Error) => props.onError(err.message));
        }}
      >
        <div className="panel-title">Save current look</div>
        <input placeholder="Look name" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
        <label className="inline">
          Fade (s)
          <input
            type="number" min={0} max={600} step={0.5} value={fade}
            onChange={(e) => setFade(Math.min(600, Math.max(0, Number(e.target.value) || 0)))}
          />
        </label>
        <button type="submit" className="primary" disabled={!name.trim()}>
          Save look
        </button>
        <p className="muted hint">Then use ASSIGN CONTROLS to put it on a matrix button.</p>
      </form>
    </div>
  );
}

function AssignPanel(props: {
  control: SurfaceControl | null;
  state: ControllerState;
  scenes: Scene[];
  onError: (message: string) => void;
}) {
  const { control, state } = props;
  const [type, setType] = useState<ActionType | "">("");
  const [target, setTarget] = useState("");
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    setType(control?.action?.type ?? "");
    setTarget(control?.action?.target ?? "");
    setSaved(null);
  }, [control?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!control) {
    return <p className="panel-block">Tap a knob, fader or button to change what it does.</p>;
  }
  const actions = state.actions.filter((a) => a.continuous === (control.kind === "cc"));
  const info = state.actions.find((a) => a.type === type);
  const targetOptions =
    info?.target === "scene"
      ? props.scenes.map((s) => ({ id: s.id, label: s.name }))
      : info?.target === "fixture"
        ? state.targets.filter((t) => t.actions.includes(type as ActionType))
        : [];
  const needsTarget = info !== undefined && info.target !== "none";
  const key = { kind: control.kind, channel: state.channel, number: control.number };

  const save = async (remove: boolean) => {
    try {
      const { bindings } = await getMidi();
      const sameKey = (b: (typeof bindings)[number]) =>
        b.key.kind === key.kind && b.key.channel === key.channel && b.key.number === key.number;
      let next = bindings.filter((b) => !sameKey(b));
      if (!remove) {
        const action = { type: type as ActionType, target: needsTarget ? target : null };
        // One action per control: the action moves here from wherever it was.
        next = next.filter((b) => !(b.action.type === action.type && b.action.target === action.target));
        next.push({ key, action, relative: control.relative });
      }
      await putMidiBindings(next);
      setSaved(remove ? "Removed." : "Saved.");
    } catch (e) {
      props.onError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="panel-block side-form">
      <div className="panel-title">{controlName(control)}</div>
      <div>
        Now: <strong>{control.action ? `${control.action_label} ${control.target_label}` : "nothing"}</strong>
      </div>
      <label className="stacked">
        Action
        <select
          value={type}
          onChange={(e) => {
            setType(e.target.value as ActionType);
            setTarget("");
          }}
        >
          <option value="">— choose —</option>
          {actions.map((a) => (
            <option key={a.type} value={a.type}>
              {a.label}
            </option>
          ))}
        </select>
      </label>
      {needsTarget && (
        <label className="stacked">
          {info?.target === "scene" ? "Look" : "Fixtures"}
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">— choose —</option>
            {targetOptions.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="button-row">
        <button
          className="primary"
          disabled={!type || (needsTarget && !target)}
          onClick={() => void save(false)}
        >
          Save
        </button>
        <button className="secondary" disabled={!control.action} onClick={() => void save(true)}>
          Remove
        </button>
      </div>
      {saved && <div className="ok-text">{saved}</div>}
      {control.relative && <p className="muted hint">Endless encoder: turns change the value step by step.</p>}
    </div>
  );
}
