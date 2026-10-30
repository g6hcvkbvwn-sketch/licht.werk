"""DMX output boundary and simulation adapter."""

import logging
from collections.abc import Sequence
from typing import Optional, Protocol
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from .dmx import DMX_SLOT_COUNT, validate_universe, validate_value


logger = logging.getLogger(__name__)


class DmxOutput(Protocol):
    """Transport boundary for sending a complete DMX universe."""

    def send_universe(self, universe: int, slots: Sequence[int]) -> None:
        """Send a complete 512-slot universe."""


class SimulatedDmxOutput:
    """In-memory output adapter; it cannot communicate with physical hardware."""

    def __init__(self) -> None:
        self._universes: dict[int, tuple[int, ...]] = {}

    def send_universe(self, universe: int, slots: Sequence[int]) -> None:
        validate_universe(universe)
        if len(slots) != DMX_SLOT_COUNT:
            raise ValueError(f"A DMX universe must contain exactly {DMX_SLOT_COUNT} slots.")
        values = tuple(slots)
        for value in values:
            validate_value(value)
        self._universes[universe] = values

    def read_universe(self, universe: int) -> tuple[int, ...]:
        validate_universe(universe)
        try:
            return self._universes[universe]
        except KeyError as error:
            raise KeyError(f"Universe {universe} has not been initialized.") from error

    def initialized_universes(self) -> tuple[int, ...]:
        return tuple(sorted(self._universes))


class OutputError(RuntimeError):
    """A real output adapter could not deliver a universe."""


class OlaHttpDmxOutput:
    """Sends universes to a local olad through its HTTP /set_dmx endpoint.

    OLA replaces the whole universe on every call, so all 512 slots are sent.
    The OLA universe number equals the application universe number.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9090, timeout: float = 1.0) -> None:
        self._url = f"http://{host}:{port}/set_dmx"
        self._timeout = timeout

    def send_universe(self, universe: int, slots: Sequence[int]) -> None:
        validate_universe(universe)
        if len(slots) != DMX_SLOT_COUNT:
            raise ValueError(f"A DMX universe must contain exactly {DMX_SLOT_COUNT} slots.")
        for value in slots:
            validate_value(value)
        body = urlencode({"u": universe, "d": ",".join(str(v) for v in slots)}).encode()
        try:
            with urlopen(self._url, data=body, timeout=self._timeout) as response:  # noqa: S310
                reply = response.read().decode(errors="replace").strip()
        except (URLError, OSError) as error:
            raise OutputError(f"olad is not reachable at {self._url}: {error}") from error
        if reply.lower() != "ok":
            raise OutputError(f"olad rejected universe {universe}: {reply!r}")


class GatedDmxOutput:
    """Wraps real output until fully initialized startup or an explicit retry enables it.

    Only the listed universes can ever reach the real output. Any delivery
    failure disarms the gate (fail safe) and is kept for diagnostics.
    """

    def __init__(self, real_output: DmxOutput, allowed_universes: Sequence[int]) -> None:
        if not allowed_universes:
            raise ValueError("Select at least one universe that real output may use.")
        self._real = real_output
        self._allowed = tuple(sorted(allowed_universes))
        self._armed = False
        self._last_error: Optional[str] = None

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def allowed_universes(self) -> tuple[int, ...]:
        return self._allowed

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def arm(self) -> None:
        self._last_error = None
        self._armed = True

    def disarm(self) -> None:
        """Stop sending, after a best-effort zeroing of every allowed universe."""
        was_armed = self._armed
        self._armed = False
        if was_armed:
            for universe in self._allowed:
                try:
                    self._real.send_universe(universe, (0,) * DMX_SLOT_COUNT)
                except (OutputError, OSError) as error:
                    self._last_error = str(error)
                    logger.error("Could not zero universe %s while disarming: %s", universe, error)

    def send_universe(self, universe: int, slots: Sequence[int]) -> None:
        if not self._armed or universe not in self._allowed:
            return
        try:
            self._real.send_universe(universe, slots)
        except (OutputError, OSError) as error:
            self._armed = False
            self._last_error = str(error)
            logger.error("Real output disarmed after failure: %s", error)
