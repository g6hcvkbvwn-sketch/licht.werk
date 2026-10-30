import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { setFixtureState, type HeadInfo } from "./api";

/**
 * Raw pan/tilt nudging in degrees for calibration: steer the beam exactly onto a target,
 * then press "rec" next to it. Values are the logical 0-1 pan/tilt sent through the normal
 * fixture state, so limits and output safety stay in the backend.
 */

const STEPS = [10, 1, 0.2];

function clamp(value: number): number {
  return Math.min(Math.max(value, 0), 1);
}

interface Props {
  head: HeadInfo;
  onMoved: () => void;
  onError: (message: string) => void;
}

export default function FineAim({ head, onMoved, onError }: Props) {
  const [pan, setPan] = useState(head.pan);
  const [tilt, setTilt] = useState(head.tilt);
  const busy = useRef(false);

  // Follow the server unless a nudge is still being sent.
  useEffect(() => {
    if (!busy.current) {
      setPan(head.pan);
      setTilt(head.tilt);
    }
  }, [head.pan, head.tilt]);

  const send = async (values: { pan?: number; tilt?: number }) => {
    busy.current = true;
    try {
      await setFixtureState(head.id, values);
      onMoved();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      busy.current = false;
    }
  };

  const nudge = (axis: "pan" | "tilt", degrees: number) => {
    if (axis === "pan") {
      const next = clamp(pan + degrees / head.pan_degrees);
      setPan(next);
      void send({ pan: next });
    } else {
      const next = clamp(tilt + degrees / head.tilt_degrees);
      setTilt(next);
      void send({ tilt: next });
    }
  };

  const onKey = (e: KeyboardEvent) => {
    const step = e.shiftKey ? 1 : 0.2;
    const moves: Record<string, ["pan" | "tilt", number]> = {
      ArrowLeft: ["pan", -step],
      ArrowRight: ["pan", step],
      ArrowDown: ["tilt", -step],
      ArrowUp: ["tilt", step],
    };
    const move = moves[e.key];
    if (!move) return;
    e.preventDefault();
    nudge(...move);
  };

  const row = (axis: "pan" | "tilt", value: number, range: number) => (
    <div className="fine-aim-row">
      <span className="fine-aim-label">
        {axis === "pan" ? "Pan" : "Tilt"} {(value * range).toFixed(1)}°
      </span>
      {STEPS.map((step) => (
        <button key={`-${step}`} className="small" onClick={() => nudge(axis, -step)}>
          −{step}°
        </button>
      ))}
      {[...STEPS].reverse().map((step) => (
        <button key={`+${step}`} className="small" onClick={() => nudge(axis, step)}>
          +{step}°
        </button>
      ))}
    </div>
  );

  return (
    <div className="fine-aim" tabIndex={0} onKeyDown={onKey}>
      <div className="fine-aim-title">
        Fine aim <span className="muted">(click here, then arrow keys: 0.2°, Shift = 1°)</span>
      </div>
      {row("pan", pan, head.pan_degrees)}
      {row("tilt", tilt, head.tilt_degrees)}
    </div>
  );
}
