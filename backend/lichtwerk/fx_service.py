"""One validated command path for controller and HTTP FX launches."""

from dataclasses import dataclass
from typing import Optional

from .effect_engine import EffectEngine
from .effects import ComboDefinition, EffectComponent, EffectType, FxDefinition, MOVEMENT_TYPES, family
from .fixture_service import FixtureController
from .fx_presets import FxPresetStore
from .position_service import PositionService
from .positioning import PositioningError, aim_at
from .room import RoomFile, target_in_included_areas
from .rings import parse_segment_participant


@dataclass(frozen=True)
class TourTargetStatus:
    """Read-only Target Tour suitability of one named target for a strip's heads."""

    id: str
    name: str
    x: Optional[float]
    y: Optional[float]
    z: Optional[float]
    reachable: bool
    pattern_ok: bool
    note: str


@dataclass(frozen=True)
class TourZoneStatus:
    """Read-only zone-route suitability of one named zone for a strip's heads.

    state: "included" (usable), "excluded" (can be included with confirmation)
    or "unreachable".
    box is the schematic top-view rectangle (x0, y0, x1, y1) in metres.
    """

    id: str
    name: str
    surface: str
    box: Optional[tuple[float, float, float, float]]
    state: str
    note: str


@dataclass(frozen=True)
class CurrentAim:
    head: str
    name: str
    target_id: Optional[str]
    zone_id: Optional[str]
    include_zones: tuple[str, ...]


class FxService:
    def __init__(
        self, controller: FixtureController, positions: PositionService,
        effects: EffectEngine, store: FxPresetStore, rate_hz: int,
    ) -> None:
        self.controller = controller
        self.positions = positions
        self.effects = effects
        self.store = store
        self.rate_hz = rate_hz

    def participants(
        self, target: str, definition: FxDefinition, *,
        component: EffectComponent = "beam",
    ) -> list[str]:
        members = self.store.participants(target, component, definition.kind)
        enabled_ids = {f.patch.id for f in self.store.fixtures(target) if f.patch.enabled}
        if component == "ring":
            members = [
                member for member in members
                if (parsed := parse_segment_participant(member)) is not None
                and parsed[0] in enabled_ids
            ]
        else:
            members = [member for member in members if member in enabled_ids]
        ordered = [f for f in definition.params.order if f in members]
        ordered += [f for f in members if f not in ordered]
        if definition.params.reverse:
            ordered.reverse()
        if definition.params.selection == "odd":
            ordered = ordered[::2]
        elif definition.params.selection == "even":
            ordered = ordered[1::2]
        return ordered

    def movement_heads(self, target: str) -> list[str]:
        """Enabled moving heads of a strip that a Move FX would drive."""
        enabled_ids = {f.patch.id for f in self.store.fixtures(target) if f.patch.enabled}
        return [h for h in self.store.participants(target, "beam", "circle") if h in enabled_ids]

    def tour_targets(self, target: str) -> list[TourTargetStatus]:
        """Which named targets a Target Tour of this strip can visit, without moving anything."""
        heads = self.movement_heads(target)
        rig = self.positions.rig
        document = rig.room_file()
        guard = self.controller.guard
        result = []
        for item in document.targets:
            point = item.point
            reachable, pattern_ok, note = point is not None and bool(heads), point is not None and bool(heads), ""
            if point is None:
                note = "No measured coordinates yet."
            elif not heads:
                note = "No enabled moving heads on this strip."
            for head in heads if point is not None else []:
                motion = self.controller.patch.get(head).definition.motion
                if motion is None:
                    reachable = pattern_ok = False
                    note = f"'{head}' has no verified motion ranges."
                    break
                try:
                    aim = aim_at(rig.head(head), motion, point, self.controller.state(head)["pan"])
                except (PositioningError, ValueError, KeyError) as error:
                    reachable = pattern_ok = False
                    note = f"Unreachable for '{head}': {error}"
                    break
                if (guard is None or not guard.allowed(head, aim.pan, aim.tilt, live=False)
                        or not target_in_included_areas(document, document.assignments.get(head), point)):
                    pattern_ok = False
                    note = note or f"Outside included areas of '{head}': still hold only."
            result.append(TourTargetStatus(
                id=item.id, name=item.name, x=item.x, y=item.y, z=item.z,
                reachable=reachable, pattern_ok=pattern_ok, note=note,
            ))
        return result

    def tour_zones(self, target: str) -> list[TourZoneStatus]:
        """Which named zones a zone route of this strip can use, without moving anything."""
        heads = self.movement_heads(target)
        rig = self.positions.rig
        document = rig.room_file()
        size = document.size
        result = []
        for zone in document.zones:
            box = None
            if size is not None:
                a, b = zone.point_at(0.0, 0.0, size), zone.point_at(1.0, 1.0, size)
                box = (min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))
            state, note = "included", ""
            if not heads:
                state, note = "unreachable", "No enabled moving heads on this strip."
            elif size is None:
                state, note = "unreachable", "Measure the room size first."
            for head in heads if state != "unreachable" else []:
                assignment = rig.assignment(head)
                name = self.controller.patch.get(head).patch.name
                motion = self.controller.patch.get(head).definition.motion
                try:
                    if motion is None:
                        raise PositioningError("no verified motion ranges")
                    assert size is not None
                    aim_at(rig.head(head), motion, zone.point_at(0.5, 0.5, size),
                           self.controller.state(head)["pan"])
                except (PositioningError, ValueError, KeyError) as error:
                    state, note = "unreachable", f"Unreachable for '{name}': {error}"
                    break
                if zone.id not in assignment.include_zones or zone.id in assignment.exclude_zones:
                    state, note = "excluded", f"Excluded for '{name}' — tap to include with confirmation."
            result.append(TourZoneStatus(
                id=zone.id, name=zone.name, surface=zone.surface, box=box, state=state, note=note,
            ))
        return result

    def current_aims(self, target: str) -> list[CurrentAim]:
        rig = self.positions.rig
        return [
            CurrentAim(
                head=head, name=self.controller.patch.get(head).patch.name,
                target_id=(assignment := rig.assignment(head)).aim_target,
                zone_id=assignment.aim_zone,
                include_zones=tuple(z for z in assignment.include_zones if z not in assignment.exclude_zones),
            )
            for head in self.movement_heads(target)
        ]

    def movement_anchors(self, target: str) -> list[str]:
        """Plain-language description of what 'Follow aim / areas' does per head at launch."""
        rig = self.positions.rig
        document = rig.room_file()
        names = {zone.id: zone.name for zone in document.zones}
        lines = []
        for head in self.movement_heads(target):
            name = self.controller.patch.get(head).patch.name
            assignment = rig.assignment(head)
            if assignment.aim_target is not None:
                try:
                    label = f"around target '{rig.target(assignment.aim_target).name}'"
                except KeyError:
                    label = f"around missing target '{assignment.aim_target}'"
            else:
                zones = [names.get(z, z) for z in assignment.include_zones if z not in assignment.exclude_zones]
                label = ("in included area '" + zones[0] + "'" if len(zones) == 1
                         else f"through {len(zones)} included areas ({', '.join(zones)})" if zones
                         else "no included area")
            ready = "" if self.positions.movement_allowed(head) else " — cannot start: needs calibration and a reachable included area"
            lines.append(f"{name}: {label}{ready}")
        return lines

    def validate_start(
        self, target: str, page: EffectType, row: int, definition: FxDefinition, *,
        component: EffectComponent = "beam",
    ) -> list[str]:
        self.store.validate(target, page, row, definition, component=component)
        members = self.participants(target, definition, component=component)
        if not members:
            raise ValueError("This selection has no enabled compatible participants. Choose All or another target.")
        if component == "ring":
            if definition.kind in MOVEMENT_TYPES:
                raise ValueError("Move effects are not available for ring sections.")
            return members
        if definition.kind not in MOVEMENT_TYPES:
            return members
        if not definition.params.stops:
            if any(not self.positions.movement_allowed(head) for head in members):
                raise ValueError(
                    "Movement FX need an included area reachable with measured/calibrated heads. "
                    "Selected targets must lie in permitted areas."
                )
            return members
        rig = self.positions.rig
        document = rig.room_file()
        if document.size is None:
            raise ValueError("Target and zone routes need measured room dimensions and calibrated heads.")
        if definition.params.zone_ids:
            statuses = {status.id: status for status in self.tour_zones(target)}
            for zone_id in definition.params.zone_ids:
                status = statuses.get(zone_id)
                if status is None:
                    raise ValueError(f"Zone '{zone_id}' does not exist.")
                if status.state != "included":
                    raise ValueError(f"Zone '{status.name}': {status.note}")
            return members
        for head in members:
            fixture = self.controller.patch.get(head)
            motion = fixture.definition.motion
            if motion is None:
                raise ValueError(f"'{head}' has no verified motion ranges.")
            for target_id in definition.params.target_ids:
                point = self.positions.resolve_target(target_id)
                aim = aim_at(rig.head(head), motion, point, self.controller.state(head)["pan"])
                pattern = definition.params.pattern
                if pattern != "none" and definition.params.size > 0:
                    guard = self.controller.guard
                    if guard is None or not guard.allowed(head, aim.pan, aim.tilt, live=False):
                        raise ValueError(
                            f"Tour pattern at '{target_id}' requires an included area for '{head}'. "
                            "Choose Pattern: None for stationary-only targets."
                        )
        return members

    def start(
        self, owner: str, target: str, page: EffectType, row: int,
        definition: FxDefinition | None = None, *,
        component: EffectComponent = "beam", overlay: bool = False,
    ) -> None:
        """Start a FX. `overlay` = accent layer: drawn over the running FX, live output only,
        and the heads' base aim is not changed."""
        definition = definition or self.store.get(target, page, row, component=component)
        self.validate_start(target, page, row, definition, component=component)
        # Engine applies ordering/selection once; pass the entire compatible target here.
        fixtures = self.store.participants(target, component, definition.kind)
        enabled_ids = {f.patch.id for f in self.store.fixtures(target) if f.patch.enabled}
        if component == "ring":
            fixtures = [
                item for item in fixtures
                if (parsed := parse_segment_participant(item)) is not None
                and parsed[0] in enabled_ids
            ]
        else:
            fixtures = [item for item in fixtures if item in enabled_ids]
        if (component == "beam" and definition.kind in MOVEMENT_TYPES
                and not definition.params.stops and not overlay):
            self.positions.prepare_movement(self.participants(target, definition))
        self.effects.start(
            owner, page, row, fixtures, definition=definition, component=component, overlay=overlay,
        )

    def warnings(self, definition: FxDefinition) -> list[str]:
        params = definition.params
        duration = params.travel if params.touring and params.pattern == "none" else (
            params.seconds if params.timing == "seconds" else params.beats
        )
        period = duration if params.timing == "seconds" else duration * 60 / self.effects.tempo.bpm()
        warnings = []
        if period < 4 / self.rate_hz:
            warnings.append("Fewer than four output frames per cycle: this FX may not reproduce reliably. Use native strobe for strobing.")
        if definition.kind in MOVEMENT_TYPES:
            warnings.append("Move output follows calibration, limits and area permissions; fast patterns may exceed physical motor response.")
        return warnings

    def validate_combo_start(self, target: str, row: int, definition: ComboDefinition, *,
                             component: EffectComponent = "beam") -> list[tuple[FxDefinition, list[str]]]:
        launches: list[tuple[FxDefinition, list[str]]] = []
        enabled = {f.patch.id for f in self.store.fixtures(target) if f.patch.enabled}
        for effect in definition.effects:
            self.validate_start(target, family(effect.kind), row, effect, component=component)
            fixtures = self.store.participants(target, component, effect.kind)
            fixtures = [item for item in fixtures if (
                (parsed[0] if (parsed := parse_segment_participant(item)) else item) in enabled
            )]
            self.effects.validate_start(effect.kind, fixtures, effect, component=component)
            launches.append((effect, fixtures))
        return launches

    def start_combo(self, owner: str, target: str, row: int,
                    definition: ComboDefinition | None = None, *,
                    component: EffectComponent = "beam", overlay: bool = False) -> None:
        definition = definition or self.store.get_combo(target, row, component=component)
        launches = self.validate_combo_start(target, row, definition, component=component)
        for effect, _ in launches:
            if effect.kind in MOVEMENT_TYPES and not effect.params.stops and not overlay:
                self.positions.prepare_movement(self.participants(target, effect))
        self.effects.start_combo(owner, row, definition.name, launches, component=component, overlay=overlay)

    def validate_room(self, document: RoomFile) -> None:
        self.store.validate_room(document)
        current = self.positions.rig.room_file(live=True)
        old_targets = {t.id: t for t in current.targets}
        new_targets = {t.id: t for t in document.targets}
        for _, _, effect in self.effects.all_running():
            for target_id in effect.params.target_ids:
                old = old_targets.get(target_id)
                new = new_targets.get(target_id)
                if new is None:
                    raise ValueError("Stop the Target Tour before deleting its target.")
                if old is not None and (old.point != new.point or current.size != document.size):
                    raise ValueError("Stop the Target Tour before editing its target coordinates or room size.")
            old_zones = {z.id: z for z in current.zones}
            new_zones = {z.id: z for z in document.zones}
            for zone_id in effect.params.zone_ids:
                new_zone = new_zones.get(zone_id)
                if new_zone is None:
                    raise ValueError("Stop the zone Move FX before deleting its zone.")
                if old_zones.get(zone_id) not in (None, new_zone) or current.size != document.size:
                    raise ValueError("Stop the zone Move FX before editing its zone geometry or room size.")
