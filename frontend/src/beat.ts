import { useEffect, useState } from "react";

/** Part of each beat the beat indicators are lit (same as the APC LEDs, apc40.py BEAT_FLASH). */
export const BEAT_FLASH = 0.25;

/**
 * One shared beat clock for every on-screen beat indicator (BEAT, NEXT, WAITING FOR NEXT),
 * so they always flash together. Each backend response re-syncs it; the phase is taken at the
 * middle of the request's round trip, and small differences are smoothed to avoid jitter.
 */
type Anchor = { phase: number; at: number; bpm: number };
let anchor: Anchor | null = null;
const listeners = new Set<() => void>();
let timer: number | null = null;

export function beatPhase(now = performance.now()): number | null {
  if (!anchor) return null;
  const phase = anchor.phase + ((now - anchor.at) / 60000) * anchor.bpm;
  return ((phase % 1) + 1) % 1;
}

export function syncBeat(bpm: number | null | undefined, phase: number | null | undefined, sentAt: number, receivedAt: number) {
  if (bpm == null || phase == null) return;
  const at = (sentAt + receivedAt) / 2;
  const predicted = anchor && anchor.bpm === bpm ? beatPhase(at) : null;
  if (predicted !== null) {
    const error = ((phase - predicted + 1.5) % 1) - 0.5;
    // Small differences are network jitter: follow them gently. Larger ones (tap, nudge): jump.
    if (Math.abs(error) < 0.08) phase = predicted + error * 0.3;
  }
  anchor = { phase, at, bpm };
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === null) timer = window.setInterval(() => listeners.forEach((l) => l()), 20);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
  };
}

/** True during the first BEAT_FLASH part of every beat. */
export function useBeatPulse(): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => subscribe(() => {
    const phase = beatPhase();
    setOn(phase !== null && phase < BEAT_FLASH);
  }), []);
  return on;
}
