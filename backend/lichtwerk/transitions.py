"""Central time-based interpolation for logical attributes, and its background runner."""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MAX_FADE_SECONDS = 600.0


@dataclass(frozen=True)
class Fade:
    """A linear fade of one attribute, timed with a monotonic clock."""

    start_value: float
    target: float
    start_time: float
    duration: float
    timing_clock: Callable[[float], float] | None = None

    def value_at(self, now: float) -> float:
        if self.timing_clock is not None:
            now = self.timing_clock(now)
        if self.duration <= 0 or now >= self.start_time + self.duration:
            return self.target
        progress = max(0.0, (now - self.start_time) / self.duration)
        return self.start_value + (self.target - self.start_value) * progress

    def finished(self, now: float) -> bool:
        if self.timing_clock is not None:
            now = self.timing_clock(now)
        return self.duration <= 0 or now >= self.start_time + self.duration


class TransitionRunner:
    """Calls `tick` at a fixed rate on a background thread so fades never block the API."""

    def __init__(self, tick: Callable[[], object], rate_hz: int) -> None:
        if not 1 <= rate_hz <= 100:
            raise ValueError("Transition rate must be between 1 and 100 Hz.")
        self._tick = tick
        self._interval = 1.0 / rate_hz
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lichtwerk-transitions", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        failing = False
        while not self._stop.wait(self._interval):
            try:
                self._tick()
                failing = False
            except Exception:  # keep fading other fixtures; log once per failure streak
                if not failing:
                    logger.exception("Transition update failed; will keep retrying.")
                failing = True
