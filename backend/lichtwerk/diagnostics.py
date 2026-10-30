"""Read-only diagnostics: patch overview, setup warnings and recent application errors."""

import logging
from collections import deque
from datetime import datetime, timezone
from threading import Lock

from pydantic import BaseModel

from .patch import Patch
from .room import RigStore, is_moving_head


class RecentErrorEntry(BaseModel):
    time: str
    message: str


class PatchEntry(BaseModel):
    id: str
    name: str
    universe: int
    first_address: int
    last_address: int
    mode: str
    enabled: bool


class DiagnosticsResponse(BaseModel):
    output_mode: str
    armed_universes: list[int]
    simulated_universes: list[int]
    patch: list[PatchEntry]
    warnings: list[str]
    recent_errors: list[RecentErrorEntry]


class RecentErrors(logging.Handler):
    """Keeps the last few WARNING+ log messages (messages only, never stack traces)."""

    def __init__(self, capacity: int = 20) -> None:
        super().__init__(level=logging.WARNING)
        self._entries: deque[RecentErrorEntry] = deque(maxlen=capacity)
        self._entries_lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        entry = RecentErrorEntry(
            time=datetime.now(timezone.utc).strftime("%H:%M:%S"), message=record.getMessage()
        )
        with self._entries_lock:
            self._entries.append(entry)

    def entries(self) -> list[RecentErrorEntry]:
        with self._entries_lock:
            return list(reversed(self._entries))


def setup_warnings(patch: Patch, rig: RigStore) -> list[str]:
    """Things that still need configuration before moving heads can be aimed."""
    warnings: list[str] = []
    for fixture in patch.fixtures:
        if not fixture.patch.enabled:
            warnings.append(f"{fixture.patch.name} is disabled in config/patch.json.")
    room = rig.room_file()
    unmeasured = [t.name for t in room.targets if t.point is None]
    if unmeasured:
        warnings.append(f"{len(unmeasured)} target(s) have no coordinates yet.")
    for fixture in patch.fixtures:
        if not is_moving_head(fixture):
            continue
        setup = rig.head(fixture.patch.id)
        name = fixture.patch.name
        if setup.position is None:
            warnings.append(f"{name}: mounting position is not set.")
        if not setup.calibrated:
            warnings.append(f"{name}: not calibrated, so it cannot aim at targets.")
    return warnings


def build_diagnostics(
    patch: Patch,
    rig: RigStore,
    output_mode: str,
    universes: list[int],
    armed_universes: list[int],
    errors: RecentErrors,
) -> DiagnosticsResponse:
    return DiagnosticsResponse(
        output_mode=output_mode,
        armed_universes=armed_universes,
        simulated_universes=[u for u in universes if u not in armed_universes],
        patch=[
            PatchEntry(
                id=f.patch.id,
                name=f.patch.name,
                universe=f.patch.universe,
                first_address=f.patch.start_address,
                last_address=f.end_address,
                mode=f.mode.name,
                enabled=f.patch.enabled,
            )
            for f in patch.fixtures
        ],
        warnings=setup_warnings(patch, rig),
        recent_errors=errors.entries(),
    )
