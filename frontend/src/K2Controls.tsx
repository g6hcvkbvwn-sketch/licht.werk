import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { LedColour } from "./api";

const clamp = (v: number) => Math.min(1, Math.max(0, v));
// Pixels of vertical drag for the full range of a pot, and per encoder step.
const POT_DRAG_PX = 160;
const ENCODER_STEP_PX = 8;

interface Selectable {
  assignMode: boolean;
  selected: boolean;
  onSelect: () => void;
  live?: boolean; // acts on the real output even in preview mode (shows a LIVE badge)
  className?: string;
}

interface KnobProps extends Selectable {
  value: number | null;
  /** Encoders are endless: they report steps instead of a position. */
  endless: boolean;
  /** Draw the value as a colour wheel position (hue). */
  hue?: boolean;
  label: string;
  sublabel: string;
  onValue: (value127: number) => void;
  onSteps: (steps: number) => void;
  /** No function right now: greyed out and not controllable. */
  disabled?: boolean;
}

/** Rotary pot or endless encoder. Drag up/down or use the mouse wheel. */
export function Knob(props: KnobProps) {
  const [dragValue, setDragValue] = useState<number | null>(null);
  const drag = useRef<{ y: number; start: number; steps: number } | null>(null);
  const shown = dragValue ?? props.value;
  const assigned = props.sublabel !== "" || props.label !== "";

  const down = (e: ReactPointerEvent) => {
    if (props.disabled) return;
    if (props.assignMode) return props.onSelect();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { y: e.clientY, start: props.value ?? 0, steps: 0 };
  };
  const move = (e: ReactPointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dy = d.y - e.clientY;
    if (props.endless) {
      const steps = Math.trunc(dy / ENCODER_STEP_PX);
      if (steps !== d.steps) {
        props.onSteps(steps - d.steps);
        d.steps = steps;
      }
    } else {
      const v = clamp(d.start + dy / POT_DRAG_PX);
      if (Math.round(v * 127) !== Math.round((dragValue ?? -1) * 127)) props.onValue(Math.round(v * 127));
      setDragValue(v);
    }
  };
  const up = () => {
    drag.current = null;
    setDragValue(null);
  };
  const wheel = (e: React.WheelEvent) => {
    if (props.disabled || props.assignMode || e.deltaY === 0) return;
    const step = e.deltaY < 0 ? 1 : -1;
    if (props.endless) props.onSteps(step);
    else props.onValue(Math.round(clamp((props.value ?? 0) + step / 32) * 127));
  };

  // 270 degree arc from bottom-left to bottom-right.
  const angle = -135 + 270 * (shown ?? 0);
  const arc = (from: number, to: number) => {
    const p = (a: number) => {
      const r = ((a - 90) * Math.PI) / 180;
      return `${50 + 40 * Math.cos(r)} ${50 + 40 * Math.sin(r)}`;
    };
    return `M ${p(from)} A 40 40 0 ${to - from > 180 ? 1 : 0} 1 ${p(to)}`;
  };
  return (
    <div
      className={`knob-unit${props.selected ? " selected" : ""}${assigned ? "" : " unassigned"}${props.live ? " live-control" : ""}${props.disabled ? " knob-disabled" : ""} ${props.className ?? ""}`}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={up}
      onWheel={wheel}
      title={props.endless ? "Endless encoder: drag up/down or scroll" : "Drag up/down or scroll"}
    >
      <svg className="knob" viewBox="0 0 100 100">
        {props.hue ? (
          Array.from({ length: 24 }, (_, i) => (
            <path key={i} d={arc(i * 15, i * 15 + 16)} className="hue-segment" stroke={`hsl(${i * 15} 100% 50%)`} />
          ))
        ) : (
          <path d={arc(-135, 135)} className="knob-track" />
        )}
        {!props.hue && shown !== null && shown > 0.004 && (
          <path d={arc(-135, angle)} className="knob-value" />
        )}
        <circle cx="50" cy="50" r="28" className={props.endless ? "knob-cap endless" : "knob-cap"} />
        {shown !== null && (
          <line
            x1="50" y1="50" x2="50" y2="26"
            className="knob-pointer"
            transform={`rotate(${props.hue ? (shown * 360) : angle} 50 50)`}
          />
        )}
      </svg>
      <div className="control-label">{props.label || "—"}</div>
      <div className="control-sublabel">{props.sublabel}</div>
    </div>
  );
}

interface FaderProps extends Selectable {
  value: number | null;
  label: string;
  sublabel: string;
  onValue: (value127: number) => void;
}

/** Vertical fader: click or drag anywhere on the track. */
export function Fader(props: FaderProps) {
  const [dragValue, setDragValue] = useState<number | null>(null);
  const track = useRef<HTMLDivElement>(null);
  const shown = dragValue ?? props.value ?? 0;

  const setFrom = (clientY: number) => {
    const rect = track.current?.getBoundingClientRect();
    if (!rect) return;
    const v = clamp(1 - (clientY - rect.top - 9) / (rect.height - 18));
    if (Math.round(v * 127) !== Math.round((dragValue ?? -1) * 127)) props.onValue(Math.round(v * 127));
    setDragValue(v);
  };
  return (
    <div className={`fader-unit${props.selected ? " selected" : ""}${props.label ? "" : " unassigned"}${props.live ? " live-control" : ""} ${props.className ?? ""}`}>
      <div className="fader-readout">{props.value === null ? "—" : `${Math.round(shown * 100)}%`}</div>
      <div
        className="fader-track"
        ref={track}
        onPointerDown={(e) => {
          if (props.assignMode) return props.onSelect();
          e.currentTarget.setPointerCapture(e.pointerId);
          setFrom(e.clientY);
        }}
        onPointerMove={(e) => {
          if (dragValue !== null) setFrom(e.clientY);
        }}
        onPointerUp={() => setDragValue(null)}
        onPointerCancel={() => setDragValue(null)}
      >
        <div className="fader-fill" style={{ height: `calc(${shown} * (100% - 18px) + 9px)` }} />
        <div className="fader-cap" style={{ bottom: `calc(${shown} * (100% - 18px))` }} />
      </div>
      <div className="control-label">{props.label || "—"}</div>
      <div className="control-sublabel">{props.sublabel}</div>
    </div>
  );
}

interface PadProps extends Selectable {
  led: LedColour;
  label: string;
  sublabel: string;
  corner?: string;
  className?: string;
  onPress: (velocity: number) => void;
}

/** Backlit button: sends press on pointer down and release on pointer up, like the hardware. */
export function Pad(props: PadProps) {
  const [pressed, setPressed] = useState(false);
  const release = () => {
    if (!pressed) return;
    setPressed(false);
    props.onPress(0);
  };
  return (
    <button
      type="button"
      className={[
        "pad",
        `led-${props.led}`,
        pressed ? "pressed" : "",
        props.selected ? "selected" : "",
        props.label ? "" : "unassigned",
        props.live ? "live-control" : "",
        props.className ?? "",
      ].join(" ")}
      onPointerDown={(e) => {
        if (props.assignMode) return props.onSelect();
        e.currentTarget.setPointerCapture(e.pointerId);
        setPressed(true);
        props.onPress(127);
      }}
      onPointerUp={release}
      onPointerCancel={release}
      onLostPointerCapture={release}
    >
      {props.corner && <span className="pad-corner">{props.corner}</span>}
      <span className="pad-label">{props.label || "—"}</span>
      {props.sublabel && <span className="pad-sublabel">{props.sublabel}</span>}
    </button>
  );
}
