"""Aiming moving heads at targets and include zones, assignments and calibration fitting.

Uses the rig data (room, targets, zones, head setups) and moves heads through the
FixtureController, so fades, limits, master and the beam guard behave exactly like any
other command.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .fixture_service import FixtureController, FixtureDisabledError
from .positioning import (
    Aim,
    CalibrationFit,
    HeadSetup,
    NotConfiguredError,
    Observation,
    Point,
    PositioningError,
    aim_at,
    beam_floor_point,
    fit_calibration,
)
from .room import HeadAssignment, RigStore


class UnreachableError(PositioningError):
    """The target is blocked by a zone rule or cannot be reached by the head."""


DEFAULT_SPOT = (0.5, 0.5)


class SceneSpot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    u: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    v: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)


class ScenePositionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_id: Optional[str] = None
    zone_id: Optional[str] = None
    spot: Optional[SceneSpot] = None
    included_zone_ids: list[str] = Field(default_factory=list)
    excluded_zone_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def one_destination(self) -> "ScenePositionEntry":
        if (self.target_id is None) == (self.zone_id is None):
            raise ValueError("A scene position needs exactly one target or zone.")
        if self.spot is not None and self.zone_id is None:
            raise ValueError("A zone spot requires a zone.")
        return self


class PositionService:
    def __init__(self, controller: FixtureController, rig: RigStore) -> None:
        self._controller = controller
        self._rig = rig
        # Beam spot inside each head's include zone (0-1 along length, width). Runtime only.
        self._spots: dict[str, tuple[float, float]] = {}
        self._preview_spots: Optional[dict[str, tuple[float, float]]] = None
        self._active_strips: dict[str, str] = {}
        self._preview_active_strips: Optional[dict[str, str]] = None
        self._controller.set_position_listener(self._manual_aim)

    @property
    def rig(self) -> RigStore:
        return self._rig

    def start_preview(self) -> None:
        self._rig.start_preview()
        self._preview_active_strips = dict(self._active_strips)
        self._preview_spots = dict(self._spots)

    def go(self) -> None:
        self._rig.go()
        if self._preview_active_strips is not None:
            self._active_strips = self._preview_active_strips
            self._preview_active_strips = None
        if self._preview_spots is not None:
            self._spots = self._preview_spots
            self._preview_spots = None

    def _active(self) -> dict[str, str]:
        return (self._preview_active_strips if self._rig.editing_preview()
                and self._preview_active_strips is not None else self._active_strips)

    def _editing_spots(self) -> dict[str, tuple[float, float]]:
        return (self._preview_spots if self._rig.editing_preview()
                and self._preview_spots is not None else self._spots)

    def validate_scene_positions(self, entries: dict[str, dict]) -> None:
        document = self._rig.room_file()
        targets = {target.id for target in document.targets}
        zones = {zone.id for zone in document.zones}
        for head, values in entries.items():
            if head not in self._rig.head_ids():
                raise ValueError(f"Scene position '{head}' is not a moving head.")
            entry = ScenePositionEntry.model_validate(values)
            if entry.target_id is not None and entry.target_id not in targets:
                raise ValueError(f"Scene references missing target '{entry.target_id}'.")
            for zone in [entry.zone_id, *entry.included_zone_ids, *entry.excluded_zone_ids]:
                if zone is not None and zone not in zones:
                    raise ValueError(f"Scene references missing zone '{zone}'.")
            HeadAssignment(
                aim_target=entry.target_id, aim_zone=entry.zone_id,
                include_zones=entry.included_zone_ids, exclude_zones=entry.excluded_zone_ids,
            )

    def capture_scene_positions(self, fixture_ids: list[str]) -> dict[str, dict]:
        entries = {}
        for head in fixture_ids:
            if head not in self._rig.head_ids():
                continue
            assignment = self._rig.assignment(head)
            if assignment.aim_target is None and assignment.aim_zone is None:
                continue
            u, v = self.spot(head)
            entries[head] = {
                "target_id": assignment.aim_target, "zone_id": assignment.aim_zone,
                "spot": {"u": u, "v": v} if assignment.aim_zone else None,
                "included_zone_ids": assignment.include_zones,
                "excluded_zone_ids": assignment.exclude_zones,
            }
        return entries

    def apply_scene_positions(
        self, entries: dict[str, dict], fade_seconds: float = 0,
        preview: Optional[bool] = None,
    ) -> None:
        selected = self._controller.editing_preview() if preview is None else preview
        with self._controller.editing_context(selected), self._rig.editing_context(selected):
            self.validate_scene_positions(entries)
            assignments = {}
            aims = {}
            spots = {}
            document = self._rig.room_file()
            for head, values in entries.items():
                if not self._controller.patch.get(head).patch.enabled:
                    continue
                entry = ScenePositionEntry.model_validate(values)
                motion, setup = self._head(head)
                if entry.target_id is not None:
                    point = self.resolve_target(entry.target_id)
                    assignment = self._rig.assignment(head).model_copy(update={
                        "aim_target": entry.target_id, "aim_zone": None,
                    })
                else:
                    assert entry.zone_id is not None
                    if document.size is None:
                        raise NotConfiguredError("Measure the room before recalling zone scenes.")
                    spot = entry.spot or SceneSpot()
                    point = document.zone(entry.zone_id).point_at(spot.u, spot.v, document.size)
                    spots[head] = (spot.u, spot.v)
                    assignment = HeadAssignment(
                        aim_zone=entry.zone_id, include_zones=entry.included_zone_ids,
                        exclude_zones=entry.excluded_zone_ids,
                    )
                aims[head] = aim_at(setup, motion, point, self._controller.state(head)["pan"])
                assignments[head] = assignment
            strips = {}
            for owner, target in self._active().items():
                heads = self.heads_of(target)
                if set(heads) & assignments.keys():
                    strips[owner] = self._common_assignment([
                        assignments.get(head, self._rig.assignment(head)) for head in heads
                    ])
            self._rig.set_position_settings(assignments, strips)
            self._editing_spots().update(spots)
            with self._controller.controlled_writes():
                for head, aim in aims.items():
                    self._controller.apply(
                        {head: {"pan": aim.pan, "tilt": aim.tilt}}, fade_seconds,
                        static_target=assignments[head].aim_target is not None,
                        preserve_aim=True,
                    )

    def strip_assignment(self, owner: str) -> HeadAssignment:
        return self._rig.room_file().strip_assignments.get(owner, HeadAssignment()).model_copy(deep=True)

    def strip_active(self, owner: str) -> bool:
        return owner in self._active()

    @staticmethod
    def _common_assignment(values: list[HeadAssignment]) -> HeadAssignment:
        first = values[0]
        return HeadAssignment(
            aim_target=first.aim_target if all(a.aim_target == first.aim_target for a in values) else None,
            aim_zone=first.aim_zone if all(a.aim_zone == first.aim_zone for a in values) else None,
            include_zones=[z for z in first.include_zones if all(z in a.include_zones for a in values)],
            exclude_zones=list(dict.fromkeys(z for a in values for z in a.exclude_zones)),
        )

    def initialize_strips(
        self, owners_targets: dict[str, str], active_owners_targets: dict[str, str],
    ) -> None:
        """Seed group and individual settings once, without recalling any startup aim."""
        document = self._rig.room_file()
        strips = dict(document.strip_assignments)
        for head in self._rig.head_ids():
            strips.setdefault(f"fixture:{head}", self._rig.assignment(head))
        for owner, target in owners_targets.items():
            heads = self.heads_of(target)
            strips.setdefault(owner, self._common_assignment([strips[f"fixture:{h}"] for h in heads]))
        assignments = {
            head: strips[owner]
            for owner, target in active_owners_targets.items()
            for head in self.heads_of(target)
        }
        self._rig.set_position_settings(assignments, strips)
        if self._preview_active_strips is not None:
            self._preview_active_strips = dict(active_owners_targets)
        else:
            self._active_strips = dict(active_owners_targets)
        self._controller.refresh_all()

    def set_strip_assignment(
        self, owner: str, target: str, assignment: HeadAssignment, fade_seconds: float = 0,
        *, recall: bool = True,
    ) -> list[str]:
        heads = self.heads_of(target)
        if recall:
            self._controller.check_manual({head: {"pan": 0.0, "tilt": 0.0} for head in heads})
        if not self._active():
            self.initialize_strips({owner: target}, {owner: target})
        active = owner in self._active()
        self._rig.set_position_settings(
            {head: assignment for head in heads} if active else {},
            {owner: assignment},
        )
        if not active:
            return []
        if not recall:
            self._controller.refresh_all()
            return []
        return self._apply_heads(heads, fade_seconds)

    def activate_strips(self, owners_targets: dict[str, str]) -> None:
        assignments: dict[str, HeadAssignment] = {}
        strips: dict[str, HeadAssignment] = {}
        document = self._rig.room_file()
        # Preserve legacy per-head settings before a first group activation replaces
        # the active head assignments. Later-created individual strips use this seed.
        for head in self._rig.head_ids():
            seed = f"fixture:{head}"
            if seed not in document.strip_assignments:
                strips[seed] = self._rig.assignment(head)
        seeds = {**strips, **document.strip_assignments}
        for owner, target in owners_targets.items():
            heads = self.heads_of(target)
            saved = document.strip_assignments.get(owner)
            if saved is None:
                # Legacy files have per-head assignments only. Seed a new strip once,
                # retaining common permissions without changing the saved individual data.
                values = [seeds[f"fixture:{head}"] for head in heads]
                saved = self._common_assignment(values)
                strips[owner] = saved
            assignments.update({head: saved for head in heads})
        self._rig.set_position_settings(assignments, strips)
        if self._preview_active_strips is not None:
            self._preview_active_strips = dict(owners_targets)
        else:
            self._active_strips = dict(owners_targets)
        self._apply_heads(list(assignments), 0.0)

    def _manual_aim(self, heads: list[str]) -> None:
        assignments = {}
        strips = {}
        for head in heads:
            if head not in self._rig.head_ids():
                continue
            assignment = self._rig.assignment(head)
            assignments[head] = assignment.model_copy(update={"aim_target": None, "aim_zone": None})
        for owner, target in self._active().items():
            if set(self.heads_of(target)) & set(assignments):
                strips[owner] = self.strip_assignment(owner).model_copy(update={"aim_target": None, "aim_zone": None})
        if assignments:
            self._rig.set_position_settings(assignments, strips)

    def movement_allowed(self, head_id: str) -> bool:
        try:
            return self._movement_start(head_id) is not None
        except (PositioningError, FixtureDisabledError):
            return False

    def _movement_start(self, head_id: str) -> Optional[Aim]:
        guard = self._controller.guard
        if guard is None or not guard.status(head_id).active:
            return None
        state = self._controller.state(head_id)
        assignment = self._rig.assignment(head_id)
        motion, setup = self._head(head_id)
        document = self._rig.room_file()
        if assignment.aim_target is not None:
            aim = aim_at(setup, motion, self.resolve_target(assignment.aim_target), state["pan"])
            return aim if guard.allowed(head_id, aim.pan, aim.tilt, live=False) else None
        zones = [
            zone for zone in document.zones
            if zone.id in assignment.include_zones and zone.id not in assignment.exclude_zones
        ]
        zones.sort(key=lambda zone: zone.id != assignment.aim_zone)
        for zone in zones:
            assert document.size is not None
            try:
                aim = aim_at(setup, motion, zone.point_at(0.5, 0.5, document.size), state["pan"])
            except PositioningError:
                continue
            if guard.allowed(head_id, aim.pan, aim.tilt, live=False):
                return aim
        return None

    def prepare_movement(self, head_ids: list[str]) -> None:
        """Recall a permitted base aim only on deliberate FX launch, never on startup."""
        aims = {head: self._movement_start(head) for head in head_ids}
        if any(aim is None for aim in aims.values()):
            raise NotConfiguredError(
                "Movement FX need measured/calibrated heads and a reachable included area; "
                "selected targets must also lie in permitted areas."
            )
        with self._controller.controlled_writes():
            self._controller.apply({
                head: {"pan": aim.pan, "tilt": aim.tilt}
                for head, aim in aims.items() if aim is not None
            }, 0.0, preserve_aim=True)

    def assignment_aim_reached(self, head_id: str) -> bool:
        assignment = self._rig.assignment(head_id)
        document = self._rig.room_file()
        try:
            motion, setup = self._head(head_id)
            state = self._controller.state(head_id)
            if assignment.aim_target is not None:
                point = self.resolve_target(assignment.aim_target)
            elif assignment.aim_zone is not None and document.size is not None:
                point = document.zone(assignment.aim_zone).point_at(*self.spot(head_id), document.size)
            else:
                return False
            aim = aim_at(setup, motion, point, state["pan"])
            return abs(aim.pan - state["pan"]) < 1e-6 and abs(aim.tilt - state["tilt"]) < 1e-6
        except (PositioningError, FixtureDisabledError):
            return False

    def heads_of(self, target: str) -> list[str]:
        """Moving heads of a head id or a group name."""
        patch = self._controller.patch
        try:
            ids = [patch.get(target).patch.id]
        except KeyError:
            ids = [f.patch.id for f in patch.groups().get(target, ())]
        heads = [head_id for head_id in ids if head_id in self._rig.head_ids()]
        if not heads:
            raise KeyError(f"'{target}' is not a moving head or a group with moving heads.")
        return heads

    def _head(self, head_id: str):
        fixture = self._controller.patch.get(head_id)
        if not fixture.patch.enabled:
            raise FixtureDisabledError(f"Fixture '{head_id}' is disabled in the patch.")
        motion = fixture.definition.motion
        if motion is None:
            raise KeyError(f"'{head_id}' is not a moving head.")
        return motion, self._rig.head(head_id)

    def resolve_target(self, target_id: str) -> Point:
        target = self._rig.target(target_id)
        point = target.point
        if point is None:
            raise NotConfiguredError(
                f"Target '{target.name}' has no coordinates yet. Measure it and enter it in the room setup."
            )
        return point

    def aim_point(
        self, head_id: str, point: Point, fade_seconds: float, *,
        named: bool = False, preserve_aim: bool = False, allow_dark: bool = False,
    ) -> Aim:
        self._controller.check_manual({head_id: {"pan": 0.0, "tilt": 0.0}})
        motion, setup = self._head(head_id)
        try:
            aim = aim_at(setup, motion, point, self._controller.state(head_id)["pan"])
        except PositioningError as error:
            if isinstance(error, NotConfiguredError):
                raise
            raise UnreachableError(str(error)) from error
        guard = self._controller.guard
        if not named and not allow_dark and guard is not None and not guard.allowed(head_id, aim.pan, aim.tilt, live=False):
            raise UnreachableError(
                guard.reason(head_id, aim.pan, aim.tilt, live=False) or "The aim is not permitted."
            )
        self._controller.apply(
            {head_id: {"pan": aim.pan, "tilt": aim.tilt}}, fade_seconds,
            static_target=named, preserve_aim=preserve_aim,
        )
        return aim

    def aim_target(self, head_id: str, target_id: str, fade_seconds: float) -> Aim:
        point = self.resolve_target(target_id)
        aim = self.aim_point(head_id, point, fade_seconds, named=True)
        assignment = self._rig.assignment(head_id).model_copy(update={"aim_target": target_id, "aim_zone": None})
        self._rig.set_assignments({head_id: assignment})
        if not self.movement_allowed(head_id):
            self._controller.stop_movement_for({head_id})
        return aim

    def spot(self, head_id: str) -> tuple[float, float]:
        return self._editing_spots().get(head_id, DEFAULT_SPOT)

    def has_include_zone(self, head_id: str) -> bool:
        return self._rig.assignment(head_id).aim_zone is not None

    def set_spot(self, head_id: str, u: float, v: float, fade_seconds: float) -> Aim:
        """Move the beam spot inside the head's include zone (u, v: 0.0-1.0)."""
        self._head(head_id)
        document = self._rig.room_file()
        zone_id = self._rig.assignment(head_id).aim_zone
        if zone_id is None:
            raise NotConfiguredError("This head has no include zone assigned.")
        if document.size is None:
            raise NotConfiguredError("Measure the room size first (Setup page).")
        point = document.zone(zone_id).point_at(u, v, document.size)
        aim = self.aim_point(head_id, point, fade_seconds, preserve_aim=True, allow_dark=True)
        self._editing_spots()[head_id] = (u, v)
        return aim

    def set_group_spot(self, target: str, u: float, v: float, fade_seconds: float) -> list[str]:
        """Move the spot of every head in `target` that has an include zone. Returns problems."""
        notes = []
        for head_id in self.heads_of(target):
            if self._rig.assignment(head_id).aim_zone is None:
                continue
            try:
                self.set_spot(head_id, u, v, fade_seconds)
            except (PositioningError, FixtureDisabledError) as error:
                notes.append(f"{self._controller.patch.get(head_id).patch.name}: {error}")
        return notes

    def assign(self, target: str, assignment: HeadAssignment, fade_seconds: float) -> list[str]:
        """Give a head (or every head of a group) this aim and these exclude zones, then aim.

        The assignment is always saved. Heads that cannot be moved yet (not calibrated,
        room not measured, unreachable) stay where they are; the reasons are returned.
        """
        heads = self.heads_of(target)
        self._controller.check_manual({head: {"pan": 0.0, "tilt": 0.0} for head in heads})
        self._rig.set_assignments({head_id: assignment for head_id in heads})
        # A newly applied include zone always starts with the beam in its geometric centre.
        for head_id in heads:
            self._editing_spots().pop(head_id, None)
        return self._apply_heads(heads, fade_seconds)

    def _apply_heads(self, heads: list[str], fade_seconds: float) -> list[str]:
        self._controller.clear_static_aims(heads)
        self._controller.refresh_all()
        notes = []
        for head_id in heads:
            problem = self._apply_aim(head_id, fade_seconds)
            if problem:
                notes.append(f"{self._controller.patch.get(head_id).patch.name}: {problem}")
        return notes

    def _apply_aim(self, head_id: str, fade_seconds: float) -> Optional[str]:
        assignment = self._rig.assignment(head_id)
        if assignment.aim_target is None and assignment.aim_zone is None:
            return None
        if not self._controller.patch.get(head_id).patch.enabled:
            return "disabled in the patch, not moved."
        if not self._rig.head(head_id).calibrated:
            return "not measured/calibrated yet, saved but not moved."
        try:
            if assignment.aim_target is not None:
                self.aim_target(head_id, assignment.aim_target, fade_seconds)
            else:
                self.set_spot(head_id, *self.spot(head_id), fade_seconds)
        except PositioningError as error:
            return str(error)
        return None

    def go_rest(self, head_id: str, fade_seconds: float) -> None:
        _, setup = self._head(head_id)
        if setup.rest is None:
            raise NotConfiguredError(
                "No rest position is configured for this head. Find a safe pan/tilt at the club and save it."
            )
        self._controller.apply({head_id: {"pan": setup.rest.pan, "tilt": setup.rest.tilt}}, fade_seconds)

    def save_head(self, head_id: str, setup: HeadSetup) -> HeadSetup:
        self._head(head_id)
        saved = self._rig.set_head(head_id, setup)
        self._controller.clear_static_aims([head_id])
        self._controller.refresh(head_id)
        return saved

    def floor_point(self, head_id: str) -> Optional[Point]:
        motion, setup = self._head(head_id)
        if not setup.calibrated:
            return None
        state = self._controller.state(head_id)
        return beam_floor_point(setup, motion, state["pan"], state["tilt"])

    def current_surface_point(self, head_id: str) -> Point:
        """Snapshot the commanded beam's first room-surface intersection, without moving."""
        self._rig.require_live_setup()
        self._head(head_id)
        guard = self._controller.guard
        if guard is None:
            raise NotConfiguredError("Room geometry is unavailable.")
        status = guard.status(head_id)
        if not status.active:
            raise NotConfiguredError(f"Cannot store current position: {status.reason}.")
        state = self._controller.output_state(head_id)
        point = guard.hit(head_id, state["pan"], state["tilt"])
        if point is None:
            raise NotConfiguredError("The commanded beam does not intersect the measured room.")
        return point

    def fit(self, head_id: str, observations: list[tuple[float, float, str]]) -> CalibrationFit:
        """Propose a calibration from (pan, tilt, target id) records. Does not save."""
        motion, setup = self._head(head_id)
        if setup.position is None:
            raise NotConfiguredError("Set the head's mounting position before calibrating.")
        records = [
            Observation(pan=pan, tilt=tilt, target=self.resolve_target(target_id))
            for pan, tilt, target_id in observations
        ]
        return fit_calibration(setup.orientation, setup.position, motion, records)
