"""Quantize: hold performance actions (looks, accents, sequences, NEXT) until the next beat.

The queue only stores and times actions. The owner (the APC control) fires due actions from
the show tick, so they run with the same locks as a direct press.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Optional

# A press this soon after a beat (in beats) counts as on that beat and fires at once, so a
# slightly late press does not wait a whole beat.
LATE_GRACE = 0.1


@dataclass
class Queued:
    key: str  # one queued action per key: "look", "sequence", "next", "accent:<pad>"
    label: str
    action: Callable[[bool], None]  # argument: the pad is still held when it fires
    preview: bool
    target: float  # beat number at which it fires
    pad: Optional[int] = None
    held: bool = True


class Quantizer:
    def __init__(self, beats: Optional[Callable[[], float]]) -> None:
        self._beats = beats
        self._lock = Lock()
        self._enabled = False
        self._queue: dict[str, Queued] = {}

    def available(self) -> bool:
        return self._beats is not None

    def enabled(self) -> bool:
        return self._enabled and self._beats is not None

    def set_enabled(self, enabled: bool) -> None:
        if enabled and self._beats is None:
            raise ValueError("Quantize needs the tempo clock (FX engine), which is not available.")
        with self._lock:
            self._enabled = enabled
            if not enabled:
                # Nothing waits for a beat any more: queued actions fire on the next tick.
                for item in self._queue.values():
                    item.target = -math.inf

    def submit(self, key: str, label: str, action: Callable[[bool], None], *,
               preview: bool, pad: Optional[int] = None) -> bool:
        """Queue the action for the next beat. False: quantize is off or the press is on the
        beat; the caller runs the action now."""
        if not self.enabled():
            return False
        beat = self._beats()
        if beat - math.floor(beat) < LATE_GRACE:
            with self._lock:
                self._queue.pop(key, None)
            return False
        with self._lock:
            self._queue[key] = Queued(key, label, action, preview, math.floor(beat) + 1.0, pad)
        return True

    def release(self, key: str) -> bool:
        """The pad of a queued action was let go. True if such an action is queued."""
        with self._lock:
            item = self._queue.get(key)
            if item is None:
                return False
            item.held = False
            return True

    def due(self) -> list[Queued]:
        """Remove and return the actions whose beat has come, in the order they were pressed."""
        with self._lock:
            if not self._queue:
                return []
            beat = self._beats() if self._beats is not None else math.inf
            ready = [item for item in self._queue.values() if beat >= item.target]
            for item in ready:
                del self._queue[item.key]
            return ready

    def queued(self) -> list[Queued]:
        with self._lock:
            return list(self._queue.values())

    def queued_pads(self) -> set[int]:
        return {item.pad for item in self.queued() if item.pad is not None}

    def is_queued(self, key: str) -> bool:
        with self._lock:
            return key in self._queue

    def state(self) -> dict:
        return {
            "available": self.available(), "enabled": self.enabled(),
            "queued": [item.label for item in self.queued()],
        }
