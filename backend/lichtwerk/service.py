"""Application-level DMX commands and simulated output state."""

from threading import RLock

from .dmx import UniverseBuffer, validate_channel, validate_universe, validate_value
from .output import DmxOutput


class LightingService:
    """Owns desired DMX state and publishes its effective simulated output."""

    def __init__(
        self, output: DmxOutput, universes: tuple[int, ...], *, blackout_active: bool = False
    ) -> None:
        if not universes:
            raise ValueError("Configure at least one simulated DMX universe.")
        self._output = output
        self._lock = RLock()
        self._buffers = {universe: UniverseBuffer(universe) for universe in universes}
        self._blackout_active = blackout_active
        self._blackout_keep: dict[int, frozenset[int]] = {}
        for universe in sorted(self._buffers):
            self._publish(universe)

    def set_channel(self, universe: int, channel: int, value: int) -> None:
        validate_universe(universe)
        validate_channel(channel)
        validate_value(value)
        with self._lock:
            try:
                buffer = self._buffers[universe]
            except KeyError as error:
                raise ValueError(f"Universe {universe} is not configured.") from error
            buffer.set_channel(channel, value)
            self._publish(universe)

    def set_channels(self, universe: int, values: dict[int, int]) -> None:
        """Apply several channels of one universe in a single published update."""
        validate_universe(universe)
        for channel, value in values.items():
            validate_channel(channel)
            validate_value(value)
        with self._lock:
            try:
                buffer = self._buffers[universe]
            except KeyError as error:
                raise ValueError(f"Universe {universe} is not configured.") from error
            for channel, value in values.items():
                buffer.set_channel(channel, value)
            self._publish(universe)

    def republish_all(self) -> None:
        """Send the current effective state of every universe (startup or explicit retry)."""
        with self._lock:
            for universe in sorted(self._buffers):
                self._publish(universe)

    def set_blackout(self, active: bool) -> None:
        if type(active) is not bool:
            raise ValueError("Blackout active state must be a boolean.")
        with self._lock:
            self._blackout_active = active
            for universe in sorted(self._buffers):
                self._publish(universe)

    def set_blackout_keep(self, channels: dict[int, frozenset[int]]) -> None:
        """Channels (per universe) that keep their value during blackout, e.g. pan/tilt."""
        for universe, numbers in channels.items():
            validate_universe(universe)
            for channel in numbers:
                validate_channel(channel)
        with self._lock:
            self._blackout_keep = {u: frozenset(c) for u, c in channels.items()}
            if self._blackout_active:
                for universe in sorted(self._buffers):
                    self._publish(universe)

    def blackout_active(self) -> bool:
        with self._lock:
            return self._blackout_active

    def universes(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(sorted(self._buffers))

    def read_output(self, universe: int) -> tuple[int, ...]:
        validate_universe(universe)
        with self._lock:
            try:
                buffer = self._buffers[universe]
            except KeyError as error:
                raise KeyError(f"Universe {universe} is not configured.") from error
            return self._effective(universe)

    def _effective(self, universe: int) -> tuple[int, ...]:
        buffer = self._buffers[universe].snapshot()
        if not self._blackout_active:
            return buffer
        keep = self._blackout_keep.get(universe, frozenset())
        # DMX channels are 1-based; buffer index 0 is channel 1.
        return tuple(value if index + 1 in keep else 0 for index, value in enumerate(buffer))

    def _publish(self, universe: int) -> None:
        self._output.send_universe(universe, self._effective(universe))
