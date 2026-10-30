"""Fail-closed beam permissions: permitted surface union minus explicit exclusions.

- At render time every unpermitted beam is dark. A stationary named-target exception is
  handled by the fixture controller, never by these area/path predicates.
- Manual pan/tilt moves stop at the edge of an exclude zone or of the head's include zone
  instead of crossing it (`limit_move`).

Missing room size, mounting position or calibration forbids illumination and says why.
Only an explicit runtime setup override bypasses protection. It uses commanded pan/tilt: the real head
lags slightly behind (see project.md section 5.4).
"""

from dataclasses import dataclass
import math
from typing import Optional

from .patch import Patch
from .positioning import Point, beam_direction
from .room import RigStore
from .zones import RoomSize, Zone, inside_room, room_hit

# Manual moves are checked at this many points along the path, then refined at the edge.
PATH_SAMPLES = 32
EDGE_STEPS = 12


@dataclass(frozen=True)
class GuardStatus:
    active: bool
    reason: Optional[str] = None


class BeamGuard:
    def __init__(self, rig: RigStore, patch: Patch) -> None:
        self._rig = rig
        self._patch = patch
        self._setup_overrides: set[str] = set()

    def setup_override(self, head_id: str) -> bool:
        return head_id in self._setup_overrides

    def set_setup_override(self, head_id: str, enabled: bool) -> None:
        self._rig.head(head_id)
        if enabled:
            self._setup_overrides.add(head_id)
        else:
            self._setup_overrides.discard(head_id)

    def status(self, head_id: str) -> GuardStatus:
        size = self._rig.room_file().size
        setup = self._rig.head(head_id)
        if size is None:
            return GuardStatus(False, "room size not measured")
        if setup.position is None:
            return GuardStatus(False, "head position not measured")
        if not setup.calibrated:
            return GuardStatus(False, "head not calibrated")
        if not inside_room(setup.position, size):
            return GuardStatus(False, "head position lies outside the room")
        return GuardStatus(True)

    def hit(self, head_id: str, pan: float, tilt: float) -> Optional[Point]:
        """Where the head's beam hits the room for this pan/tilt; None if the guard is inactive."""
        if not self.status(head_id).active or not all(math.isfinite(v) for v in (pan, tilt)):
            return None
        size = self._rig.room_file().size
        assert size is not None
        setup = self._rig.head(head_id)
        motion = self._patch.get(head_id).definition.motion
        if motion is None or setup.position is None:
            return None
        pan, tilt = setup.limits.clamp_pan(pan), setup.limits.clamp_tilt(tilt)
        return room_hit(setup.position, beam_direction(setup, motion, pan, tilt), size)

    def _zones(self, head_id: str, *, live: bool = True) -> tuple[RoomSize, list[Zone], list[Zone]]:
        document = self._rig.room_file(live=live)
        assignment = document.assignments.get(head_id)
        size = document.size
        assert size is not None
        if assignment is None:
            return size, [], []
        excludes = [document.zone(zone_id) for zone_id in assignment.exclude_zones]
        includes = [document.zone(zone_id) for zone_id in assignment.include_zones]
        return size, excludes, includes

    def blocking_zone(self, head_id: str, pan: float, tilt: float, *, live: bool = True) -> Optional[Zone]:
        """The assigned exclude zone the beam would hit, or None."""
        point = self.hit(head_id, pan, tilt)
        if point is None:
            return None
        size, excludes, _ = self._zones(head_id, live=live)
        return next((zone for zone in excludes if zone.contains(point, size)), None)

    def allowed(self, head_id: str, pan: float, tilt: float, *, live: bool = True) -> bool:
        """Permission for this ray, without the separate static named-target exception."""
        if self.setup_override(head_id):
            return True
        point = self.hit(head_id, pan, tilt)
        if point is None:
            return False
        size, excludes, includes = self._zones(head_id, live=live)
        if any(zone.contains(point, size) for zone in excludes):
            return False
        return any(zone.contains(point, size) for zone in includes)

    def reason(self, head_id: str, pan: float, tilt: float, *, live: bool = True) -> Optional[str]:
        if self.setup_override(head_id):
            return "SETUP OVERRIDE: area protection bypassed for calibration"
        status = self.status(head_id)
        if not status.active:
            return status.reason
        blocked = self.blocking_zone(head_id, pan, tilt, live=live)
        if blocked:
            return f"Excluded zone: {blocked.name}"
        if not self.allowed(head_id, pan, tilt, live=live):
            return "Beam is outside all permitted zones"
        return None

    def limit_move(
        self, head_id: str, start: tuple[float, float], end: tuple[float, float],
        *, live: bool = True,
    ) -> tuple[float, float]:
        """The furthest pan/tilt toward `end` reachable without crossing a zone border.

        If the head already points somewhere not allowed, the move is not restricted, so it
        can always be moved back out.
        """
        if not self.status(head_id).active or not self.allowed(head_id, *start, live=live):
            return end

        def at(fraction: float) -> tuple[float, float]:
            return (
                start[0] + (end[0] - start[0]) * fraction,
                start[1] + (end[1] - start[1]) * fraction,
            )

        good = 0.0
        for step in range(1, PATH_SAMPLES + 1):
            fraction = step / PATH_SAMPLES
            if not self.allowed(head_id, *at(fraction), live=live):
                bad = fraction
                for _ in range(EDGE_STEPS):
                    middle = (good + bad) / 2
                    if self.allowed(head_id, *at(middle), live=live):
                        good = middle
                    else:
                        bad = middle
                return at(good)
            good = fraction
        return end
