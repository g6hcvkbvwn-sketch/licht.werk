"""One validated command path for controller and HTTP FX launches."""

from .effect_engine import EffectEngine
from .effects import EffectComponent, EffectType, FxDefinition, MOVEMENT_TYPES
from .fixture_service import FixtureController
from .fx_presets import FxPresetStore
from .position_service import PositionService
from .positioning import aim_at
from .room import RoomFile
from .rings import parse_segment_participant


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
        if not definition.params.target_ids:
            if any(not self.positions.movement_allowed(head) for head in members):
                raise ValueError(
                    "Movement FX need an included area reachable with measured/calibrated heads. "
                    "Selected targets must lie in permitted areas."
                )
            return members
        rig = self.positions.rig
        if rig.room_file().size is None:
            raise ValueError("Target Tour needs measured room dimensions and calibrated heads.")
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
        component: EffectComponent = "beam",
    ) -> None:
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
        if component == "beam" and definition.kind in MOVEMENT_TYPES and not definition.params.target_ids:
            self.positions.prepare_movement(self.participants(target, definition))
        self.effects.start(
            owner, page, row, fixtures, definition=definition, component=component
        )

    def warnings(self, definition: FxDefinition) -> list[str]:
        params = definition.params
        duration = params.travel if params.target_ids and params.pattern == "none" else (
            params.seconds if params.timing == "seconds" else params.beats
        )
        period = duration if params.timing == "seconds" else duration * 60 / self.effects.tempo.bpm()
        warnings = []
        if period < 4 / self.rate_hz:
            warnings.append("Fewer than four output frames per cycle: this FX may not reproduce reliably. Use native strobe for strobing.")
        if definition.kind in MOVEMENT_TYPES:
            warnings.append("Move output follows calibration, limits and area permissions; fast patterns may exceed physical motor response.")
        return warnings

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
