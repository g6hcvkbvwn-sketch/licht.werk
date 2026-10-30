"""Shared tempo (BPM) for effects: tap tempo, typed BPM and fine adjustment.

`beats(now)` counts beats continuously. Changing the BPM never makes it jump: the count is
re-anchored at the moment of the change. A tap re-anchors on a whole beat, so effects restart
their cycle on the tap (the "one").
"""

import time
from collections.abc import Callable
from threading import Lock
from typing import Optional

MIN_BPM = 20.0
MAX_BPM = 300.0
DEFAULT_BPM = 128.0
TAP_RESET_SECONDS = 2.0  # a longer pause starts a new tap sequence
TAP_MEMORY = 8  # taps averaged


def validate_bpm(bpm: object) -> float:
    if isinstance(bpm, bool) or not isinstance(bpm, (int, float)):
        raise ValueError("BPM must be a number.")
    if not MIN_BPM <= bpm <= MAX_BPM:
        raise ValueError(f"BPM must be between {MIN_BPM:g} and {MAX_BPM:g}.")
    return float(bpm)


class TempoClock:
    def __init__(self, clock: Callable[[], float] = time.monotonic, bpm: float = DEFAULT_BPM) -> None:
        self._clock = clock
        self._lock = Lock()
        self._bpm = validate_bpm(bpm)
        self._anchor_time = clock()
        self._anchor_beats = 0.0
        self._taps: list[float] = []

    def bpm(self) -> float:
        with self._lock:
            return self._bpm

    def now(self) -> float:
        """Sample the injected monotonic clock used by beats and launch-relative FX."""
        return self._clock()

    def beats(self, now: Optional[float] = None) -> float:
        with self._lock:
            return self._beats(self._clock() if now is None else now)

    def _beats(self, now: float) -> float:
        return self._anchor_beats + (now - self._anchor_time) * self._bpm / 60.0

    def _rebase(self, now: float, bpm: float, beats: Optional[float] = None) -> None:
        self._anchor_beats = self._beats(now) if beats is None else beats
        self._anchor_time = now
        self._bpm = min(max(bpm, MIN_BPM), MAX_BPM)

    def set_bpm(self, bpm: float) -> float:
        bpm = validate_bpm(bpm)
        with self._lock:
            self._rebase(self._clock(), bpm)
            return self._bpm

    def adjust(self, delta: float) -> float:
        """Change the BPM by `delta` (kept inside the allowed range)."""
        with self._lock:
            self._rebase(self._clock(), round(self._bpm + delta, 1))
            return self._bpm

    def tap(self) -> float:
        """One tap. From the second tap on, the BPM follows the average tap interval."""
        now = self._clock()
        with self._lock:
            if self._taps and now - self._taps[-1] > TAP_RESET_SECONDS:
                self._taps.clear()
            self._taps.append(now)
            self._taps = self._taps[-TAP_MEMORY:]
            bpm = self._bpm
            if len(self._taps) >= 2:
                interval = (self._taps[-1] - self._taps[0]) / (len(self._taps) - 1)
                if interval > 0:
                    bpm = round(60.0 / interval, 1)
            self._rebase(now, bpm, beats=float(round(self._beats(now))))
            return self._bpm
