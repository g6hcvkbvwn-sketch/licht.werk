import { useEffect, useRef, useState } from "react";
import {
  APC40,
  ConnectionError,
  assignApc40Pad,
  clearApc40Pad,
  getApc40,
  getScenes,
  getSequences,
  sendApc40Event,
  setApc40Tempo,
  type Apc40EffectType,
  type Apc40Pad,
  type Apc40State,
  type Apc40Strip,
  type Scene,
  type Sequence,
} from "./api";
import { Fader, Knob } from "./K2Controls";
import RoomMap from "./RoomMap";
import PositionDialog from "./PositionDialog";
import SceneDialog from "./SceneDialog";
import SequenceDialog from "./SequenceDialog";
import FxDialog from "./FxDialog";
import { isFxPage, type FxPage, type FxComponent } from "./fxApi";

type Event = { kind: "note" | "cc"; channel: number; number: number; value: number };

/**
 * Sends on-screen events in order. While a request is in flight, newer knob/fader positions
 * replace older ones and relative knob steps (CUE LEVEL, TEMPO) are added up, so dragging never
 * floods the backend.
 */
class Apc40Sender {
  private queue: Event[] = [];
  private latest = new Map<string, Event>();
  private steps = new Map<number, number>(); // relative CC number -> pending steps
  private busy = false;
  constructor(
    private onState: (state: Apc40State) => void,
    private onError: (e: unknown) => void,
  ) {}

  note(channel: number, number: number, velocity: number) {
    this.queue.push({ kind: "note", channel, number, value: velocity });
    void this.pump();
  }
  absolute(channel: number, number: number, value: number) {
    this.latest.set(`${channel}:${number}`, { kind: "cc", channel, number, value });
    void this.pump();
  }
  relative(number: number, steps: number) {
    this.steps.set(number, (this.steps.get(number) ?? 0) + steps);
    void this.pump();
  }

  private next(): Event | null {
    const queued = this.queue.shift();
    if (queued) return queued;
    for (const [key, event] of this.latest) {
      this.latest.delete(key);
      return event;
    }
    for (const [number, pending] of this.steps) {
      const step = Math.max(-63, Math.min(63, pending));
      if (pending - step === 0) this.steps.delete(number);
      else this.steps.set(number, pending - step);
      if (step === 0) continue;
      return { kind: "cc", channel: 1, number, value: step > 0 ? step : 128 + step };
    }
    return null;
  }

  private async pump() {
    if (this.busy) return;
    this.busy = true;
    try {
      for (let e = this.next(); e; e = this.next()) {
        try {
          this.onState(await sendApc40Event(e.kind, e.channel, e.number, e.value));
        } catch (error) {
          this.onError(error);
        }
      }
    } finally {
      this.busy = false;
    }
  }
}

const noSelect = { assignMode: false, selected: false, onSelect: () => {} };

// A strip whose fixtures are currently controlled by the other mode (group vs. single).
const standby = (s: Apc40Strip) => (s.available && !s.active ? "apc-standby" : "");

function rgbCss(rgb: [number, number, number]): string {
  return `rgb(${rgb.map((c) => Math.round(Math.min(Math.max(c, 0), 1) * 255)).join(", ")})`;
}

/** Current colour of the strip's fixtures (one segment per fixture), or STANDBY when inactive. */
function StripColour({ strip }: { strip: Apc40Strip }) {
  if (!strip.available) return <div className="apc-colour-bar empty" />;
  if (!strip.active) {
    return (
      <div className="apc-colour-bar standby" title={`${strip.group ? "Single strips" : "Group strip"} in control`}>
        STANDBY
      </div>
    );
  }
  if (strip.ring_selected) {
    return <div className="apc-colour-bar" aria-label={`${strip.label} ring sections`}>
      {strip.ring_colours.map((rgb, i) => <span key={i} className="apc-colour-seg"
        style={{ background: rgbCss(rgb) }} title={`${strip.label} ring section ${i % 6 + 1}`} />)}
    </div>;
  }
  return (
    <div className="apc-colour-bar">
      {strip.colours.map((rgb, i) => {
        const black = !rgb || rgb.every((c) => c < 0.02);
        return (
          <span
            key={i}
            className={black ? "apc-colour-seg black" : "apc-colour-seg"}
            style={black ? undefined : { background: rgbCss(rgb!) }}
            title={`${strip.fixture_names[i]}: ${black ? "no colour set (flash stays dark)" : "current colour"}`}
          >
            {black ? "no colour" : ""}
          </span>
        );
      })}
    </div>
  );
}
// The backend sends palette colours as bare hex ("FF5400").
const css = (hex: string) => `#${hex}`;

/** Momentary hardware button: press on pointer down, release on pointer up. */
function HwButton(props: {
  label: string;
  lit: "off" | "amber" | "green" | "red" | "blue";
  disabled?: boolean;
  title?: string;
  className?: string;
  onPress: (velocity: number) => void;
}) {
  const [pressed, setPressed] = useState(false);
  const release = () => {
    if (!pressed) return;
    setPressed(false);
    props.onPress(0);
  };
  return (
    <button
      type="button"
      title={props.title}
      disabled={props.disabled}
      className={`pad small led-${props.lit}${pressed ? " pressed" : ""} ${props.className ?? ""}`}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        setPressed(true);
        props.onPress(127);
      }}
      onPointerUp={release}
      onPointerCancel={release}
      onLostPointerCapture={release}
    >
      <span className="pad-label">{props.label}</span>
    </button>
  );
}

interface Props {
  scenes: Scene[];
}

export default function Apc40View({ scenes }: Props) {
  const [state, setState] = useState<Apc40State | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [assigning, setAssigning] = useState(false);
  const [assignPad, setAssignPad] = useState<number | null>(null);
  const [addingPosition, setAddingPosition] = useState(false);
  const [managingPosition, setManagingPosition] = useState(false);
  const [newPositionKind, setNewPositionKind] = useState<"target" | "zone">("target");
  const [positionItem, setPositionItem] = useState<string | null>(null);
  const [fxDialog, setFxDialog] = useState<{ track: number; page: FxPage; stripName: string; component: FxComponent } | null>(null);
  const lastDialogRequest = useRef(0);
  const lastShowRequest = useRef(0);
  const [showDialog, setShowDialog] = useState<{
    kind: "scene" | "sequence"; pad: number; scene?: Scene; sequence?: Sequence;
  } | null>(null);
  const [beatOn, setBeatOn] = useState(false);
  const tempoAnchor = useRef({ phase: 0, at: performance.now(), bpm: 128 });
  const sender = useRef(
    new Apc40Sender(setState, (e) => {
      if (!(e instanceof ConnectionError)) setError(e instanceof Error ? e.message : String(e));
    }),
  ).current;

  useEffect(() => {
    const shift = (e: KeyboardEvent) => {
      if (e.type === "keydown" && e.target instanceof HTMLElement
        && e.target.closest("input, textarea, select, [role=dialog]")) return;
      if (e.key === "Shift" && !e.repeat) sender.note(1, APC40.shift, e.type === "keydown" ? 127 : 0);
    };
    const release = () => {
      sender.note(1, APC40.shift, 0);
      for (let track = 1; track <= 8; track++) sender.note(track, APC40.recordArm, 0);
    };
    window.addEventListener("keydown", shift);
    window.addEventListener("keyup", shift);
    window.addEventListener("blur", release);
    return () => {
      window.removeEventListener("keydown", shift);
      window.removeEventListener("keyup", shift);
      window.removeEventListener("blur", release);
      release();
    };
  }, [sender]);

  useEffect(() => {
    if (state?.beat_phase !== null && state?.beat_phase !== undefined && state.bpm !== null)
      tempoAnchor.current = { phase: state.beat_phase, at: performance.now(), bpm: state.bpm };
  }, [state]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      const { phase, at, bpm } = tempoAnchor.current;
      setBeatOn((phase + ((performance.now() - at) / 60000) * bpm) % 1 < 0.2);
    }, 25);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!state) return;
    if (state.position_dialog_id === 0) lastDialogRequest.current = 0;
    if (state.position_dialog_id > lastDialogRequest.current) {
      lastDialogRequest.current = state.position_dialog_id;
      if (state.position_dialog_kind && !state.preview && state.pad_mode === "positions") {
        setNewPositionKind(state.position_dialog_kind);
        setPositionItem(state.position_dialog_item);
        setManagingPosition(Boolean(state.position_dialog_item));
        setAddingPosition(!state.position_dialog_item);
      }
    }
  }, [state]);

  useEffect(() => {
    if (!state) return;
    const request = state.show_dialog;
    if (!request.id) lastShowRequest.current = 0;
    if (!request.kind || request.id <= lastShowRequest.current) return;
    lastShowRequest.current = request.id;
    const kind = request.kind;
    if (!request.item) {
      setShowDialog({ kind, pad: request.pad });
      return;
    }
    if (kind === "scene") {
      void getScenes().then((items) => {
        const scene = items.find((item) => item.id === request.item);
        if (!scene) throw new Error("This scene no longer exists. Unassign its pad or choose another scene.");
        setShowDialog({ kind, pad: request.pad, scene });
      }).catch((e: Error) => setError(e.message));
    } else {
      void getSequences().then((items) => {
        const sequence = items.find((item) => item.id === request.item);
        if (!sequence) throw new Error("This sequence no longer exists. Unassign its pad or choose another sequence.");
        setShowDialog({ kind, pad: request.pad, sequence });
      }).catch((e: Error) => setError(e.message));
    }
  }, [state]);

  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const next = await getApc40();
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

  if (!state) return <p className="muted">Loading APC40…</p>;

  const selectedStrip = state.strips.find((s) => s.selected) ?? null;
  const fxOpen = state.pad_mode === "effects";
  const fxStrip = state.strips.find((s) => s.fx_knobs) ?? null;
  const fxType = state.effect_types.find((t) => t.selected) ?? null;
  const positionStrip = state.strips.find((s) => s.position_selected);
  // In preview these controls still act on the real output: mark them.
  const live = state.preview ? "live-control" : "";
  // Scene assignment only makes sense while the pads show scenes.
  const assignMode = assigning && state.pad_mode === "scenes";

  const pressPad = (pad: Apc40Pad) => (velocity: number) => {
    if (assignMode) {
      if (velocity > 0) setAssignPad(pad.pad);
      return;
    }
    sender.note(1, pad.note, velocity);
  };

  return (
    <div className="apc">
      {showDialog?.kind === "scene" && <SceneDialog scene={showDialog.scene} pad={showDialog.pad}
        onClose={() => setShowDialog(null)} onSaved={() => { void getApc40().then(setState); }} />}
      {showDialog?.kind === "sequence" && <SequenceDialog sequence={showDialog.sequence} pad={showDialog.pad}
        onClose={() => setShowDialog(null)} onSaved={() => { void getApc40().then(setState); }} />}
      {fxDialog && <FxDialog {...fxDialog} preview={state.preview}
        stripActive={state.strips.some((strip) => strip.track === fxDialog.track && strip.active)}
        onClose={() => setFxDialog(null)} />}
      <div className="apc-main">
        <div className="apc-row apc-titles">
          {state.strips.map((s) => (
            <div key={s.track} className={["apc-title", s.available ? "" : "unavailable", standby(s)].join(" ")}>
              <span className="apc-track">{s.track}</span> {s.label}
            </div>
          ))}
          <div className="apc-title">Master</div>
        </div>
        <div className="apc-row">
          {state.strips.map((s) => (
            <Knob
              key={s.track}
              {...noSelect}
              className={standby(s)}
              value={s.saturation}
              endless={false}
              label={s.saturation === null ? "" : s.hazer ? "Fan" : s.ring_selected ? "Ring intensity" : "Saturation"}
              sublabel=""
              onValue={(v) => sender.absolute(1, APC40.trackKnobFirst + s.track - 1, v)}
              onSteps={() => {}}
            />
          ))}
          <div />
        </div>

        <div className={assignMode ? "apc-pads assigning" : "apc-pads"}>
          {state.effect_types.map((t) => (
            <SceneLaunchButton
              key={t.index}
              type={t}
              onPress={(v) => sender.note(1, APC40.sceneLaunchFirst + t.index - 1, v)}
            />
          ))}
          {state.pads.map((pad) => (
            <PadButton
              key={pad.pad}
              pad={pad}
              chosen={assignMode && assignPad === pad.pad}
              onPress={pressPad(pad)}
            />
          ))}
        </div>

        <div className="apc-row">
          {state.strips.map((s) => (
            <HwButton
              key={s.track}
              label={s.selected ? "COLOR ◂" : "COLOR"}
              lit={s.selected || s.colour_set ? "green" : "off"}
              className={s.selected ? "apc-blink" : ""}
              disabled={!s.selectable && !s.selected}
              title="CLIP STOP: select this strip for colour pads and device knobs (press again to go back)"
              onPress={(v) => sender.note(s.track, APC40.clipStop, v)}
            />
          ))}
          <HwButton
            label={state.blackout ? "BLACKOUT ON" : "BLACKOUT"}
            lit={state.blackout ? "red" : "off"}
            className={[state.blackout ? "blackout-pad" : "", live].join(" ")}
            title="MASTER button: blackout on/off"
            onPress={(v) => sender.note(1, APC40.master, v)}
          />
        </div>
        <div className="apc-row">
          {state.strips.map((s) => (
            <HwButton
              key={s.track}
              label={s.position_selected ? "TARGET/ZONE" : s.fx_knobs ? "FX KNOBS ◂" : "FX"}
              lit={s.fx_knobs || s.fx_running || s.position_selected ? "amber" : "off"}
              className={s.fx_knobs ? "apc-blink fast" : s.fx_running || s.position_selected ? "apc-blink" : ""}
              disabled={!s.available}
              title="TRACK SELECT: choose FX knobs. SHIFT + moving-head track opens target/zone pads. Blinking = FX running, fast = FX editing."
              onPress={(v) => sender.note(s.track, APC40.trackSelect, v)}
            />
          ))}
          <HwButton label="STOP FX" lit="off" disabled={state.fx_track === null}
            title="STOP ALL CLIPS: stop all effects of the chosen FX strip (preview only while previewing)"
            onPress={(v) => sender.note(1, APC40.stopAllClips, v)} />
        </div>
        <div className="apc-buttons">
          {state.strips.map((s) => (
            <div className="apc-strip-buttons" key={s.track}>
            <HwButton
              label={String(s.track)}
              lit={s.active ? "amber" : "off"}
              disabled={!s.available}
              title="Track activator: include this strip in faders and colour"
              onPress={(v) => sender.note(s.track, APC40.activator, v)}
            />
            <HwButton label={s.ring_available ? "RING" : "A/B"} lit={s.ring_selected ? "amber" : "off"}
              disabled={!s.ring_available} title="A/B: select ring editing without changing output; fader and FLASH stay beam-only"
              onPress={(v) => sender.note(s.track, APC40.ring, v)} />
            <HwButton label="STROBE" lit={s.strobe_on ? "blue" : "off"}
              className={standby(s)} disabled={!s.available || s.hazer}
              title="SOLO: fixture strobe on/off (rate = CUE LEVEL)"
              onPress={(v) => sender.note(s.track, APC40.solo, v)} />
            <HwButton
              label={s.hazer ? "HAZE" : "FLASH"}
              lit={s.flash_held ? "red" : "off"}
              className={[s.available ? live : "", standby(s)].join(" ")}
              disabled={!s.available}
              title={s.hazer ? "Hold: live pump and fan maximum; release restores live settings" : "RECORD ARM: full brightness while held"}
              onPress={(v) => sender.note(s.track, APC40.recordArm, v)}
            />
            </div>
          ))}
          <div className="apc-master-cell apc-cue">
            <Knob
              {...noSelect}
              value={null}
              endless
              label={`${state.strobe_hz.toFixed(1)} Hz`}
              sublabel="Strobe rate"
              onValue={() => {}}
              onSteps={(s) => sender.relative(APC40.cueLevel, s)}
            />
          </div>
        </div>
        <div className="apc-row apc-colour-row">
          {state.strips.map((s) => (
            s.hazer ? <div key={s.track} /> : <StripColour key={s.track} strip={s} />
          ))}
          <div />
        </div>
        <div className="apc-row apc-faders">
          {state.strips.map((s) => (
            <Fader
              key={s.track}
              {...noSelect}
              className={standby(s)}
              value={s.intensity}
              label={s.available ? s.hazer ? "Pump" : "Intensity" : ""}
              sublabel=""
              onValue={(v) => sender.absolute(s.track, APC40.trackFader, v)}
            />
          ))}
          <div className={["apc-master-fader", live].join(" ")}>
            <Fader
              {...noSelect}
              value={state.master}
              label="Intensity"
              sublabel=""
              onValue={(v) => sender.absolute(1, APC40.masterFader, v)}
            />
          </div>
        </div>
      </div>

      <aside className="apc-side">
        <RoomMap compact />
        <div className="panel-title">Pads · {state.pad_mode.toUpperCase()}</div>
        <div className="apc-show-pages">
          <HwButton label="← SCENES" lit={state.pad_mode === "scenes" ? "green" : "off"}
            onPress={(v) => sender.note(1, APC40.left, v)} />
          <HwButton label="SEQUENCES →" lit={state.pad_mode === "sequences" ? "green" : "off"}
            onPress={(v) => sender.note(1, APC40.right, v)} />
        </div>
        <div className="apc-show-transport">
          <HwButton label="PLAY / PAUSE" lit="off" title="PLAY: sequence pause/resume; SHIFT + PLAY: restart"
            onPress={(v) => sender.note(1, APC40.play, v)} />
          <HwButton label="STOP" lit="off" title="RECORD: stop sequence at current values (not blackout)"
            onPress={(v) => sender.note(1, APC40.record, v)} />
        </div>
        <div className={state.connected ? "apc-status ok" : "apc-status"}>
          {state.connected ? `APC40 connected (${state.device})` : "APC40 not connected: on-screen only"}
        </div>
        <HwButton label={state.preview ? "GO" : "PREVIEW"} lit={state.preview ? "red" : "off"}
          className={state.preview ? "preview-go" : ""} title="SESSION RECORD: preview / Go"
          onPress={(v) => sender.note(1, APC40.sessionRecord, v)} />
        {(error || state.error) && (
          <div className="banner error small-banner" onClick={() => setError(null)}>
            {error ?? state.error}
          </div>
        )}
        {state.fx_notice && <div role="status" className="banner small-banner">{state.fx_notice}</div>}

        {state.bpm !== null && (
          <TempoPanel
            bpm={state.bpm}
            beatOn={beatOn}
            onTap={(v) => sender.note(1, APC40.tapTempo, v)}
            onSteps={(steps) => sender.relative(APC40.tempo, steps)}
            onBpm={(bpm) =>
              setApc40Tempo(bpm).then(setState, (e) => setError(e instanceof Error ? e.message : String(e)))
            }
          />
        )}

        <div className="panel-title">
          {fxOpen
            ? `Effect knobs · ${fxType?.label ?? ""}${fxStrip ? ` · ${fxStrip.label}${fxStrip.ring_selected && fxType?.type !== "circle" ? " · Ring" : ""}` : ""}`
            : `${selectedStrip?.ring_selected ? "Ring colour" : "Device knobs"} ${selectedStrip ? `· ${selectedStrip.label}` : ""}`}
        </div>
        {positionStrip && <div className="muted hint">
          Targets / zones · {positionStrip.label}.
          <div>
            <button className={state.position_mode === "aim" ? "active" : ""}
              onClick={() => sender.note(1, APC40.up, 127)}>AIM · ↑</button>
            <button className={state.position_mode === "permissions" ? "active" : ""}
              onClick={() => sender.note(1, APC40.down, 127)}>PERMISSIONS · ↓</button>
          </div>
          <div>Included areas: {state.pads.filter((pad) => pad.included).map((pad) => pad.label).join(", ") || "none"}</div>
          <button disabled={state.preview} onClick={() => { setPositionItem(null); setAddingPosition(true); }}>Add target/zone</button>
          <button disabled={state.preview} onClick={() => { setPositionItem(null); setManagingPosition(true); }}>Edit/delete target/zone</button>
          {state.preview && <span>Finish preview before editing room definitions.</span>}
        </div>}
        {fxOpen && <div className="fx-edit-action">
          <button type="button" disabled={state.fx_track === null || !fxType || !isFxPage(fxType.type)}
            onClick={() => {
              if (state.fx_track !== null && fxType && isFxPage(fxType.type)) setFxDialog({
                track: state.fx_track, page: fxType.type,
                component: fxStrip?.ring_selected && fxType.type !== "circle" ? "ring" : "beam",
                stripName: state.strips.find((strip) => strip.track === state.fx_track)?.label ?? `Strip ${state.fx_track}`,
              });
            }}>Edit FX presets</button>
          {state.fx_track === null && <small className="muted">Choose a strip with FX KNOBS / TRACK SELECT first.</small>}
        </div>}
        {selectedStrip || fxOpen ? (
          <div className="apc-device">
            {state.knobs.map((k) => (
              <Knob
                key={k.knob}
                {...noSelect}
                value={k.value}
                endless={false}
                hue={k.function === "hue"}
                label={k.label ?? ""}
                sublabel={k.display ?? ""}
                onValue={(v) => sender.absolute(1, APC40.deviceKnobFirst + k.knob - 1, v)}
                onSteps={() => {}}
              />
            ))}
          </div>
        ) : null}

        <div className="apc-shift">
          <HwButton label="SHIFT" lit={state.shift ? "amber" : "off"}
            title="Hold with TRACK SELECT for target/zone menu, or an existing pad to edit"
            onPress={(v) => sender.note(1, APC40.shift, v)} />
          <span>Target/Zone · keyboard Shift also works</span>
        </div>
        {(addingPosition || managingPosition) && <PositionDialog
          key={`${managingPosition ? "edit" : "new"}:${newPositionKind}:${positionItem ?? ""}`}
          initialKind={newPositionKind}
          initialItem={positionItem}
          headIds={state.strips.filter((s) => !s.group && s.family === "heads"
            && (positionStrip?.group || s.track === positionStrip?.track))
            .flatMap((s) => s.target ? [s.target] : [])}
          manage={managingPosition}
          onClose={() => { setAddingPosition(false); setManagingPosition(false); }}
        />}

        {state.pad_mode === "scenes" && (
          <button
            className={assigning ? "assign-toggle active" : "assign-toggle"}
            onClick={() => {
              setAssigning(!assigning);
              setAssignPad(null);
            }}
          >
            {assigning ? "DONE ASSIGNING" : "ASSIGN SCENE PADS"}
          </button>
        )}
        {assignMode && (
          <PadAssigner
            pad={assignPad === null ? null : state.pads[assignPad]}
            scenes={scenes}
            colours={state.scene_colours}
            onState={setState}
            onError={(m) => setError(m)}
          />
        )}

        <div className="muted hint">
          Last hardware input:{" "}
          {state.last_event
            ? `${state.last_event.kind} ch ${state.last_event.channel} #${state.last_event.number} = ${state.last_event.value}`
            : "—"}
        </div>
      </aside>
    </div>
  );
}

/** SCENE LAUNCH 1-5: effect type on the effects page (dark when the page is closed or unfit). */
function SceneLaunchButton(props: {
  type: Apc40EffectType;
  onPress: (velocity: number) => void;
}) {
  const { type } = props;
  const lit = type.available;
  return (
    <button
      type="button"
      disabled={!lit}
      className={["apc-launch", lit && type.selected ? "selected" : "", lit && type.running ? "apc-blink" : ""].join(" ")}
      style={{
        gridRow: type.index,
        ...(lit ? { borderColor: css(type.colour), background: type.selected ? css(type.colour) : undefined } : {}),
      }}
      title={`SCENE LAUNCH ${type.index}: show the ${type.label} effects on the pads (press again to close). Blinks while one runs`}
      onPointerDown={() => props.onPress(127)}
      onPointerUp={() => props.onPress(0)}
    >
      {type.label}
    </button>
  );
}

/** Shared effect tempo: TAP TEMPO button, TEMPO knob (0.1 BPM per step) and typed BPM. */
function TempoPanel(props: {
  bpm: number;
  beatOn: boolean;
  onTap: (velocity: number) => void;
  onSteps: (steps: number) => void;
  onBpm: (bpm: number) => void;
}) {
  const [typed, setTyped] = useState<string | null>(null);
  const submit = () => {
    const value = Number(typed);
    if (typed !== null && Number.isFinite(value)) props.onBpm(value);
    setTyped(null);
  };
  return (
    <div className="apc-tempo">
      <span className={props.beatOn ? "apc-beat on" : "apc-beat"} title="METRONOME: backend-synchronised beat">BEAT</span>
      <HwButton label="TAP" lit="off" title="TAP TEMPO: tap on the beat" onPress={props.onTap} />
      <Knob
        {...noSelect}
        value={null}
        endless
        label="Tempo"
        sublabel=""
        onValue={() => {}}
        onSteps={props.onSteps}
      />
      <label className="apc-bpm">
        <input
          type="number"
          min={20}
          max={300}
          step={0.1}
          value={typed ?? props.bpm.toFixed(1)}
          onChange={(e) => setTyped(e.target.value)}
          onBlur={submit}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
        />
        BPM
      </label>
    </div>
  );
}

function PadButton(props: { pad: Apc40Pad; chosen: boolean; onPress: (velocity: number) => void }) {
  const [pressed, setPressed] = useState(false);
  const { pad } = props;
  const release = () => {
    if (!pressed) return;
    setPressed(false);
    props.onPress(0);
  };
  return (
    <button
      type="button"
      className={[
        "apc-pad",
        pad.included ? "included" : "",
        pad.active ? "active" : "",
        pad.missing ? "missing" : "",
        props.chosen ? "chosen" : "",
        pressed ? "pressed" : "",
      ].join(" ")}
      style={{
        gridColumn: pad.pad % 8 + 1,
        gridRow: Math.floor(pad.pad / 8) + 1,
        ...(pad.colour ? { background: css(pad.colour), borderColor: css(pad.colour) } : {}),
      }}
      aria-pressed={pad.kind === "zone" ? pad.included : undefined}
      title={pad.kind === "zone"
        ? `${pad.label}: ${pad.explicitly_excluded ? "explicitly excluded" : pad.included ? "included" : "not included"}. Use Aim/Permissions mode; SHIFT + press edits.`
        : undefined}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        setPressed(true);
        props.onPress(127);
      }}
      onPointerUp={release}
      onPointerCancel={release}
      onLostPointerCapture={release}
    >
      {(pad.included || pad.active) && (
        <span className="apc-pad-play">{pad.included ? "✓" : ""}{pad.active ? " ▶" : ""}</span>
      )}
      <span className="apc-pad-label">{pad.label ?? ""}</span>
      {pad.fixture_badges.length > 0 && <small title={pad.fixture_badges.join(", ")}>
        {pad.fixture_badges.join(" · ")}
      </small>}
      {pad.explicitly_excluded && <span className="apc-pad-play">EXCLUDED</span>}
    </button>
  );
}

function PadAssigner(props: {
  pad: Apc40Pad | null;
  scenes: Scene[];
  colours: Record<string, string>;
  onState: (state: Apc40State) => void;
  onError: (message: string) => void;
}) {
  const { pad } = props;
  const [scene, setScene] = useState("");
  const [colour, setColour] = useState("white");
  useEffect(() => {
    setScene(pad?.scene ?? props.scenes[0]?.id ?? "");
    setColour(pad?.scene_colour ?? "white");
    // Reset the form only when another pad is picked.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pad?.pad]);

  const run = async (action: () => Promise<Apc40State>) => {
    try {
      props.onState(await action());
    } catch (e) {
      props.onError(e instanceof Error ? e.message : String(e));
    }
  };

  if (!pad) return <p className="muted hint">Click a pad to give it a scene.</p>;
  return (
    <div className="panel-block">
      <div className="panel-title">Pad {pad.pad + 1}</div>
      <select value={scene} onChange={(e) => setScene(e.target.value)}>
        {props.scenes.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </select>
      <div className="apc-colours">
        {Object.entries(props.colours).map(([name, hex]) => (
          <button
            key={name}
            type="button"
            title={name}
            className={colour === name ? "apc-colour chosen" : "apc-colour"}
            style={{ background: css(hex) }}
            onClick={() => setColour(name)}
          />
        ))}
      </div>
      <div className="apc-assign-buttons">
        <button disabled={!scene} onClick={() => void run(() => assignApc40Pad(pad.pad, scene, colour))}>
          Save
        </button>
        <button disabled={!pad.scene} onClick={() => void run(() => clearApc40Pad(pad.pad))}>
          Clear pad
        </button>
      </div>
    </div>
  );
}
