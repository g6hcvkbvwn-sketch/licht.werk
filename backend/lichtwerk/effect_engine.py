"""Logical lighting FX runtime, with independent preview/live snapshots."""

import logging
import math
from contextlib import contextmanager
from dataclasses import dataclass, replace
from threading import Lock, local
from typing import Optional

from .effects import (
    EFFECT_TYPES,
    MOVEMENT_TYPES,
    PRESET_COUNT,
    PRESETS,
    EffectComponent,
    EffectParams,
    EffectType,
    FxDefinition,
    Values,
    beats_from_level,
    family,
    fixture_phase,
    modulate as apply_effect,
    movement_offset,
    seconds_from_level,
)
from .patch import Patch
from .positioning import PositioningError, aim_at
from .rings import (
    RING_COLOURS,
    parse_segment_participant,
    ring_segments,
    segment_colour_attributes,
    segment_participant,
)
from .room import RigStore
from .tempo import TempoClock
from .zones import Zone

EffectKey = tuple[str, EffectType, str]
logger = logging.getLogger(__name__)
ZONE_TRAVEL_CYCLES = 0.25


@dataclass(frozen=True)
class RunningEffect:
    preset: int
    params: EffectParams
    fixtures: tuple[str, ...]
    active: bool = True
    kind: EffectType = EffectType.CHASE
    name: str = ""
    launched_at: float = 0.0
    launch_beat: float = 0.0
    phase_anchor: float = 0.0
    phase_offset: float = 0.0
    positions: tuple[int, ...] = ()
    participant_count: int = 0
    component: EffectComponent = "beam"


@dataclass(frozen=True)
class EffectFrame:
    values: Values
    dark_movement: bool = False
    static_target: bool = False


class EffectEngine:
    def __init__(
        self, patch: Patch, tempo: TempoClock, *, rig: Optional[RigStore] = None
    ) -> None:
        self._patch = patch
        self._tempo = tempo
        self._rig = rig
        self._lock = Lock()
        self._live: dict[EffectKey, RunningEffect] = {}
        self._preview: Optional[dict[EffectKey, RunningEffect]] = None
        self._dirty: set[str] = set()
        self._movement_errors: dict[tuple[str, bool], str] = {}
        self._notice: Optional[str] = None
        self._frame_time = local()

    @property
    def tempo(self) -> TempoClock:
        return self._tempo

    @property
    def rig(self) -> Optional[RigStore]:
        return self._rig

    @property
    def notice(self) -> Optional[str]:
        """Latest whole-instance replacement notice for status/API presentation."""
        with self._lock:
            return self._notice

    def configure_rig(self, rig: Optional[RigStore]) -> None:
        self._rig = rig

    @contextmanager
    def frame(self):
        """Freeze time/beat sampling for rendering and protection on this thread.

        Nested frames reuse the outer sample. No engine lock is held during the body.
        """
        previous = getattr(self._frame_time, "sample", None)
        if previous is None:
            now = self._tempo.now()
            self._frame_time.sample = (now, self._tempo.beats(now))
        try:
            yield
        finally:
            self._frame_time.sample = previous

    def _now(self) -> float:
        sample = getattr(self._frame_time, "sample", None)
        return self._tempo.now() if sample is None else sample[0]

    def _beats(self, now: float) -> float:
        sample = getattr(self._frame_time, "sample", None)
        return self._tempo.beats(now) if sample is None else sample[1]

    def _editing(self) -> dict[EffectKey, RunningEffect]:
        return self._preview if self._preview is not None else self._live

    def _changed(self, effect: Optional[RunningEffect]) -> None:
        if effect is not None and self._preview is None:
            self._dirty.update(self._physical_fixture_id(item) for item in effect.fixtures)

    @staticmethod
    def _physical_fixture_id(participant_id: str) -> str:
        participant = parse_segment_participant(participant_id)
        return participant[0] if participant is not None else participant_id

    def validate_start(
        self,
        kind: EffectType,
        fixtures: list[str] | tuple[str, ...],
        definition: FxDefinition,
        *,
        rig: Optional[RigStore] = None,
        component: EffectComponent = "beam",
    ) -> tuple[str, ...]:
        """Validate all targets before a launch can replace an existing family instance."""
        if component not in ("beam", "ring"):
            raise ValueError("Effect component must be 'beam' or 'ring'.")
        fixtures = self._expand_ring_fixtures(fixtures) if component == "ring" else tuple(fixtures)
        selected_fixtures = self._roster(fixtures, definition.params)[0]
        if not selected_fixtures:
            raise ValueError("An effect needs at least one selected fixture.")
        if component == "ring" and kind in MOVEMENT_TYPES:
            raise ValueError("Move effects are not available for ring sections.")
        for participant_id in selected_fixtures:
            parsed = parse_segment_participant(participant_id) if component == "ring" else None
            fixture_id = parsed[0] if parsed is not None else participant_id
            fixture = self._patch.get(fixture_id)
            valid_segment = (
                parsed is not None
                and parsed[1] in ring_segments(fixture)
                and _fits_kind(kind, fixture, component)
            )
            if (component == "ring" and not valid_segment) or (
                component == "beam" and not _fits_kind(kind, fixture, component)
            ):
                raise ValueError(f"Fixture '{participant_id}' cannot run {kind.value} on {component}.")
        if definition.params.target_ids:
            if component == "ring":
                raise ValueError("Target touring is only available for beam Move effects.")
            self._validate_tour_targets(selected_fixtures, definition.params, rig or self._rig)
        return selected_fixtures

    def _expand_ring_fixtures(
        self, fixtures: list[str] | tuple[str, ...]
    ) -> tuple[str, ...]:
        """Accept physical IDs or section IDs and normalize to ordered section IDs."""
        participants: list[str] = []
        for item in dict.fromkeys(fixtures):
            parsed = parse_segment_participant(item)
            if parsed is not None:
                participants.append(item)
                continue
            fixture = self._patch.get(item)
            participants.extend(
                segment_participant(item, segment) for segment in ring_segments(fixture)
            )
        return tuple(participants)

    @staticmethod
    def _roster(
        fixtures: list[str] | tuple[str, ...], params: EffectParams
    ) -> tuple[tuple[str, ...], tuple[int, ...], int]:
        values = list(dict.fromkeys(fixtures))
        ordered = [fixture_id for fixture_id in params.order if fixture_id in values]
        ordered.extend(fixture_id for fixture_id in values if fixture_id not in ordered)
        if params.reverse:
            ordered.reverse()
        selected = [
            (fixture_id, index) for index, fixture_id in enumerate(ordered)
            if _selected_index(index, params.selection)
        ]
        return tuple(item[0] for item in selected), tuple(item[1] for item in selected), len(ordered)

    def _validate_tour_targets(
        self, fixtures: tuple[str, ...], params: EffectParams, rig: Optional[RigStore]
    ) -> None:
        if rig is None:
            raise ValueError("Target touring requires the configured room rig.")
        document = rig.room_file()
        zones = {zone.id: zone for zone in document.zones}
        patterned = params.pattern != "none" and params.size > 0
        for fixture_id in fixtures:
            setup = rig.head(fixture_id)
            motion = self._patch.get(fixture_id).definition.motion
            if motion is None:
                raise ValueError(f"Fixture '{fixture_id}' has no configured movement range.")
            assignment = document.assignments.get(fixture_id)
            included = [] if assignment is None else [
                zones[zone_id] for zone_id in assignment.include_zones
                if zone_id in zones and zone_id not in assignment.exclude_zones
            ]
            excluded = [] if assignment is None else [
                zones[zone_id] for zone_id in assignment.exclude_zones if zone_id in zones
            ]
            for target_id in params.target_ids:
                target = rig.target(target_id)
                if target.point is None:
                    raise ValueError(f"Target '{target_id}' has no measured coordinates.")
                if patterned and (
                    document.size is None
                    or not included
                    or not any(zone.contains(target.point, document.size) for zone in included)
                    or any(zone.contains(target.point, document.size) for zone in excluded)
                ):
                    raise ValueError(
                        f"Target '{target_id}' is outside fixture '{fixture_id}' included areas."
                    )
                try:
                    aim_at(setup, motion, target.point, 0.5)
                except PositioningError as error:
                    raise ValueError(
                        f"Target '{target_id}' is unreachable for fixture '{fixture_id}': {error}"
                    ) from error

    # --- commands ---------------------------------------------------------------------------

    def start(
        self,
        owner: str,
        effect_type: EffectType,
        preset: int,
        fixtures: list[str],
        *,
        definition: Optional[FxDefinition] = None,
        component: EffectComponent = "beam",
    ) -> None:
        """Run an unchanged factory preset or a supplied stored definition."""
        if definition is None:
            if not 0 <= preset < PRESET_COUNT:
                raise ValueError(f"Preset {preset + 1} does not exist (1-{PRESET_COUNT}).")
            source = PRESETS[effect_type][preset]
            definition = FxDefinition(name=source.name, kind=effect_type, params=source.params)
        if family(definition.kind) != family(effect_type):
            raise ValueError("The FX definition does not belong to the requested family.")
        if component == "ring":
            fixtures = list(self._expand_ring_fixtures(fixtures))
        ordered_fixtures = self.validate_start(
            effect_type, fixtures, definition, component=component
        )
        _, positions, participant_count = self._roster(fixtures, definition.params)
        now = self._now()
        launch_beat = self._beats(now)
        current = RunningEffect(
            preset=preset,
            params=definition.params.model_copy(deep=True),
            fixtures=ordered_fixtures,
            kind=definition.kind,
            name=definition.name,
            launched_at=now,
            launch_beat=launch_beat,
            phase_anchor=now,
            positions=positions,
            participant_count=participant_count,
            component=component,
        )
        new_key = (owner, family(effect_type), component)
        with self._lock:
            effects = self._editing()
            displaced: list[str] = []
            for key, active in list(effects.items()):
                same_owner_page = key == new_key
                conflicts = (
                    active.active
                    and active.component == component
                    and family(active.kind) == family(effect_type)
                    and bool(set(active.fixtures).intersection(ordered_fixtures))
                )
                if same_owner_page or conflicts:
                    removed = effects.pop(key)
                    self._changed(removed)
                    if conflicts and key != new_key:
                        displaced.append(f"{key[0]} ({', '.join(removed.fixtures)})")
            self._notice = (
                "Replaced conflicting FX instance(s): " + "; ".join(displaced)
                if displaced else None
            )
            effects[new_key] = current

    def stop(
        self, owner: str, effect_type: EffectType, *, component: EffectComponent = "beam"
    ) -> None:
        with self._lock:
            self._changed(self._editing().pop((owner, family(effect_type), component), None))

    def stop_all(
        self, owner: Optional[str] = None, *, component: Optional[EffectComponent] = None
    ) -> None:
        with self._lock:
            effects = self._editing()
            for key, effect in list(effects.items()):
                if (owner is None or key[0] == owner) and (
                    component is None or effect.component == component
                ):
                    self._changed(effect)
                    del effects[key]

    def set_param(
        self, owner: str, effect_type: EffectType, name: str, level: float, *,
        component: EffectComponent = "beam",
    ) -> bool:
        if not 0.0 <= level <= 1.0:
            raise ValueError(f"Effect {name} must be between 0.0 and 1.0.")
        with self._lock:
            effects = self._editing()
            key = (owner, family(effect_type), component)
            effect = effects.get(key)
            if effect is None:
                return False
            if name == "speed" and effect.params.target_ids:
                travel = (
                    seconds_from_level(level)
                    if effect.params.timing == "seconds"
                    else beats_from_level(level)
                )
                update = {"travel": travel}
            elif name == "speed":
                update = {"seconds": seconds_from_level(level)} if effect.params.timing == "seconds" else {
                    "beats": beats_from_level(level)
                }
            elif name == "seconds":
                update = {"seconds": seconds_from_level(level)}
            elif name in ("size", "spread", "softness", "maximum"):
                update = {name: float(level)}
            else:
                raise ValueError(f"Unknown effect parameter '{name}'.")
            now = self._now()
            phase_offset = effect.phase_offset
            phase_anchor = effect.phase_anchor
            launched_at = effect.launched_at
            launch_beat = effect.launch_beat
            if effect.params.target_ids and "travel" in update:
                elapsed = self._tour_elapsed(effect, now)
                old_duration = effect.params.travel + effect.params.hold
                segment = math.floor(elapsed / old_duration)
                within = elapsed - segment * old_duration
                if within < effect.params.travel:
                    fraction = within / effect.params.travel
                    adjusted = fraction * update["travel"]
                else:
                    hold_fraction = (within - effect.params.travel) / effect.params.hold
                    adjusted = update["travel"] + hold_fraction * effect.params.hold
                rebased_elapsed = segment * (update["travel"] + effect.params.hold) + adjusted
                if effect.params.timing == "seconds":
                    launched_at = now - rebased_elapsed
                else:
                    launch_beat = self._beats(now) - rebased_elapsed
            if effect.params.timing == "seconds" and "seconds" in update:
                phase_offset = self._phase(effect, now)
                phase_anchor = now
            params = EffectParams.model_validate({**effect.params.model_dump(), **update})
            effects[key] = replace(
                effect, params=params, phase_anchor=phase_anchor, phase_offset=phase_offset,
                launched_at=launched_at, launch_beat=launch_beat,
            )
            return True

    def set_active(
        self, owner: str, active: bool, *, component: Optional[EffectComponent] = None
    ) -> None:
        with self._lock:
            effects = self._editing()
            owned = [
                (key, effect) for key, effect in effects.items()
                if key[0] == owner and (component is None or effect.component == component)
            ]
            for key, effect in owned:
                if not active:
                    if effect.active:
                        effects[key] = replace(effect, active=False)
                        self._changed(effect)
                    continue
                if effect.active:
                    continue
                conflicts = [
                    other_key for other_key, other in effects.items()
                    if other_key != key and other.active
                    and other.component == effect.component
                    and family(other.kind) == family(effect.kind)
                    and set(other.fixtures).intersection(effect.fixtures)
                ]
                for other_key in conflicts:
                    displaced = effects.pop(other_key)
                    self._changed(displaced)
                    self._notice = (
                        f"Replaced conflicting FX instance {other_key[0]} "
                        f"({', '.join(displaced.fixtures)})."
                    )
                effects[key] = replace(effect, active=True)
                self._changed(effect)

    # --- queries ----------------------------------------------------------------------------

    def running(
        self, owner: str, effect_type: EffectType, *,
        component: EffectComponent = "beam",
    ) -> Optional[RunningEffect]:
        with self._lock:
            effect = self._editing().get((owner, family(effect_type), component))
            return None if effect is None else replace(
                effect, params=effect.params.model_copy(deep=True)
            )

    def owner_running(
        self, owner: str, *, component: Optional[EffectComponent] = None
    ) -> bool:
        with self._lock:
            return any(
                key[0] == owner and (component is None or effect.component == component)
                for key, effect in self._editing().items()
            )

    def all_running(
        self, *, preview: Optional[bool] = None,
        component: Optional[EffectComponent] = None,
    ) -> list[tuple[str, EffectType, RunningEffect]]:
        """Snapshot both worlds by default, or select live/preview explicitly.

        Changed preview records retain the same owner/page as their live counterpart.
        Identical records shared between worlds are returned only once.
        """
        with self._lock:
            if preview is None:
                records = list(self._live.items())
                if self._preview is not None:
                    records.extend(
                        (key, effect) for key, effect in self._preview.items()
                        if self._live.get(key) != effect
                    )
            else:
                effects = self._editing() if preview else self._live
                records = list(effects.items())
            return [
                (owner, page, replace(effect, params=effect.params.model_copy(deep=True)))
                for (owner, page, _), effect in records
                if component is None or effect.component == component
            ]

    def active_definitions(
        self, *, preview: bool = False, component: Optional[EffectComponent] = None
    ) -> list[FxDefinition]:
        return [
            FxDefinition(name=effect.name, kind=effect.kind, params=effect.params)
            for _, _, effect in self.all_running(preview=preview, component=component)
            if effect.active
        ]

    def any_active(
        self, owner: Optional[str] = None, *,
        component: Optional[EffectComponent] = None,
    ) -> bool:
        """Report whether any active effect matches an optional owner/component filter."""
        with self._lock:
            return any(
                effect.active
                and (owner is None or key[0] == owner)
                and (component is None or effect.component == component)
                for key, effect in self._editing().items()
            )

    def movement_active(self, fixture_id: str, *, preview: bool = False) -> bool:
        with self._lock:
            effects = self._editing() if preview else self._live
            return any(
                effect.kind in MOVEMENT_TYPES and effect.active and fixture_id in effect.fixtures
                for effect in effects.values()
            )

    def stop_movement_for(self, fixture_ids: set[str]) -> None:
        with self._lock:
            effects = self._editing()
            for key, effect in list(effects.items()):
                if effect.kind in MOVEMENT_TYPES and fixture_ids.intersection(effect.fixtures):
                    self._changed(effects.pop(key))

    # --- preview ----------------------------------------------------------------------------

    def start_preview(self) -> None:
        with self._lock:
            if self._preview is None:
                self._preview = dict(self._live)

    def go(self) -> None:
        with self._lock:
            if self._preview is None:
                return
            for effect in self._live.values():
                self._dirty.update(self._physical_fixture_id(item) for item in effect.fixtures)
            self._live = self._preview
            self._preview = None

    # --- rendering --------------------------------------------------------------------------

    def take_render_set(self) -> set[str]:
        with self._lock:
            ids = set(self._dirty)
            self._dirty.clear()
            for effect in self._live.values():
                if effect.active:
                    ids.update(self._physical_fixture_id(item) for item in effect.fixtures)
            return ids

    def modulate(
        self, fixture_id: str, state: Values, *, preview: bool = False,
        rig: Optional[RigStore] = None,
    ) -> Values:
        return self.render(fixture_id, state, preview=preview, rig=rig).values

    def render(
        self, fixture_id: str, state: Values, *, preview: bool = False,
        rig: Optional[RigStore] = None,
    ) -> EffectFrame:
        with self._lock:
            effects = [
                (effect.kind, effect)
                for effect in (self._editing() if preview else self._live).values()
                if effect.active and effect.component == "beam" and fixture_id in effect.fixtures
            ]
            ring_effects = [
                (effect.kind, effect)
                for effect in (self._editing() if preview else self._live).values()
                if effect.active and effect.component == "ring"
            ]
        if not effects and not ring_effects:
            return EffectFrame(state)
        now = self._now()
        beats = self._beats(now)
        motion = self._patch.get(fixture_id).definition.motion
        selected_rig = rig or self._rig
        dark_movement = False
        static_target = False
        for page in EFFECT_TYPES:
            for kind, effect in effects:
                if family(kind) != page:
                    continue
                slot_index = effect.fixtures.index(fixture_id)
                index = effect.positions[slot_index] if effect.positions else slot_index
                count = effect.participant_count or len(effect.fixtures)
                phase = self._phase(effect, now, beats, index, count)
                if kind in MOVEMENT_TYPES and effect.params.target_ids:
                    tour = self._target_tour(
                        fixture_id, state, kind, effect, now, index, count, selected_rig, preview
                    )
                    if tour is None:
                        dark_movement = True
                    else:
                        state, dark, is_static = tour
                        dark_movement |= dark
                        static_target |= is_static
                elif kind in MOVEMENT_TYPES and selected_rig is not None:
                    touring = self._zone_movement(
                        fixture_id, state, kind, effect, beats, now, index, count, selected_rig, preview
                    )
                    if touring is not None:
                        state, dark = touring
                        dark_movement |= dark
                    else:
                        state = apply_effect(
                            kind, effect.params, phase, state, motion,
                            index=index, count=count,
                        )
                else:
                    state = apply_effect(
                        kind, effect.params, phase, state, motion,
                        index=index, count=count,
                    )
        if ring_effects:
            fixture = self._patch.get(fixture_id)
            for segment in ring_segments(fixture):
                participant_id = segment_participant(fixture_id, segment)
                attributes = segment_colour_attributes(fixture, segment)
                # Ring dimmer effects multiply the final ring colour, so apply them
                # after colour effects rather than replacing their palette result.
                for page in (EffectType.RAINBOW, EffectType.CHASE, EffectType.CIRCLE):
                    for kind, effect in ring_effects:
                        if family(kind) != page or participant_id not in effect.fixtures:
                            continue
                        slot_index = effect.fixtures.index(participant_id)
                        index = effect.positions[slot_index] if effect.positions else slot_index
                        count = effect.participant_count or len(effect.fixtures)
                        phase = self._phase(effect, now, beats, index, count)
                        colour = dict(zip(RING_COLOURS, (state.get(name, 0.0) for name in attributes)))
                        if kind is EffectType.CHASE:
                            factor = apply_effect(
                                kind, effect.params, phase, {"intensity": 1.0},
                                index=index, count=count,
                            )["intensity"]
                            colour = {name: value * factor for name, value in colour.items()}
                        else:
                            colour = apply_effect(
                                kind, effect.params, phase, colour, index=index, count=count
                            )
                        for name, attribute in zip(RING_COLOURS, attributes):
                            state[attribute] = colour[name]
        if dark_movement:
            state = {
                name: 0.0 if name in ("intensity", "strobe") else value
                for name, value in state.items()
            }
        return EffectFrame(state, dark_movement, static_target and not dark_movement)

    def _phase(
        self, effect: RunningEffect, now: float, beats: Optional[float] = None,
        index: int = 0, count: int = 1,
    ) -> float:
        params = effect.params
        offset = params.spread * index / count if count else 0.0
        if params.timing == "beats":
            position = self._beats(now) if beats is None else beats
            cycle = position / params.beats
        else:
            cycle = effect.phase_offset + (now - effect.phase_anchor) / params.seconds
        return (cycle - offset) % 1.0

    def _tour_elapsed(self, effect: RunningEffect, now: float) -> float:
        if effect.params.timing == "beats":
            return max(0.0, self._beats(now) - effect.launch_beat)
        return max(0.0, now - effect.launched_at)

    def _target_tour(
        self, fixture_id: str, state: Values, kind: EffectType, effect: RunningEffect,
        now: float, index: int, count: int, rig: Optional[RigStore], preview: bool,
    ) -> Optional[tuple[Values, bool, bool]]:
        if rig is None or "pan" not in state or "tilt" not in state:
            self._movement_error(fixture_id, preview, "Target tour has no configured room rig.")
            return None
        params = effect.params
        try:
            elapsed = self._tour_elapsed(effect, now)
            duration = params.travel + params.hold
            tour_duration = duration * len(params.target_ids)
            spread_offset = params.spread * index / count * tour_duration if count else 0.0
            alternate_offset = (
                0.5 * tour_duration * (index % 2)
                if params.selection == "alternating" else 0.0
            )
            elapsed = max(0.0, elapsed) + spread_offset + alternate_offset
            segment_duration = duration
            segment = math.floor(elapsed / segment_duration)
            phase = (elapsed / segment_duration) % 1.0
            within = elapsed - segment * segment_duration
            target_index = segment % len(params.target_ids)
            previous_index = (target_index - 1) % len(params.target_ids)
            document = rig.room_file(live=not preview)
            targets = {target.id: target for target in document.targets}
            target = targets[params.target_ids[target_index]]
            previous = targets[params.target_ids[previous_index]]
            if target.point is None or previous.point is None:
                raise PositioningError("A tour target has no measured coordinates.")
            setup = rig.head(fixture_id)
            motion = self._patch.get(fixture_id).definition.motion
            if motion is None:
                raise PositioningError("The fixture has no movement range.")
            current_aim = aim_at(setup, motion, target.point, state["pan"])
            if within + 1e-9 < params.travel:
                if segment == 0:
                    start_pan, start_tilt = state["pan"], state["tilt"]
                else:
                    start_aim = aim_at(setup, motion, previous.point, state["pan"])
                    start_pan, start_tilt = start_aim.pan, start_aim.tilt
                fraction = within / params.travel
                fraction = fraction * fraction * (3.0 - 2.0 * fraction)
                out = dict(state)
                out.update(
                    pan=start_pan + (current_aim.pan - start_pan) * fraction,
                    tilt=start_tilt + (current_aim.tilt - start_tilt) * fraction,
                )
                if "speed" in out:
                    out["speed"] = 1.0
                self._movement_errors.pop((fixture_id, preview), None)
                return out, True, False

            out = dict(state)
            pattern_kind = kind if params.pattern == "current" else (
                EffectType.CIRCLE if params.pattern == "circle" else
                EffectType.FIGURE8 if params.pattern == "figure8" else None
            )
            size = params.size if pattern_kind is not None else 0.0
            if pattern_kind is not None and size > 0.0:
                local_phase = (max(0.0, within - params.travel) / params.hold) % 1.0
                dx, dy = movement_offset(pattern_kind, local_phase)
                if params.mirror in ("x", "xy"):
                    dx = -dx
                if params.mirror in ("y", "xy"):
                    dy = -dy
                if params.mirror != "none" and params.mirror_alternate and index % 2:
                    dx = -dx
                pan = current_aim.pan + size * 30.0 * dx / motion.pan_degrees
                tilt = current_aim.tilt + size * 30.0 * dy / motion.tilt_degrees
                out["pan"] = setup.limits.clamp_pan(pan)
                out["tilt"] = setup.limits.clamp_tilt(tilt)
            else:
                out["pan"], out["tilt"] = current_aim.pan, current_aim.tilt
            if "speed" in out:
                out["speed"] = 1.0
            is_static = size == 0.0 and (
                params.pattern == "none" or params.size == 0.0
            )
            self._movement_errors.pop((fixture_id, preview), None)
            return out, False, is_static
        except (KeyError, PositioningError) as error:
            self._movement_error(fixture_id, preview, str(error))
            return None

    def stationary_target(
        self, fixture_id: str, values: Values, *, preview: bool = False
    ) -> bool:
        """True only at an exact, permitted-by-runtime stationary target hold endpoint."""
        with self._lock:
            effects = list((self._editing() if preview else self._live).values())
        now = self._now()
        for effect in effects:
            if (
                not effect.active or fixture_id not in effect.fixtures
                or effect.kind not in MOVEMENT_TYPES or not effect.params.target_ids
            ):
                continue
            base = dict(values)
            # Render already carries the target coordinates; only grant an exception when
            # this exact frame is marked static and the final values still equal that aim.
            frame = self._target_tour(
                fixture_id, base, effect.kind, effect, now,
                effect.positions[effect.fixtures.index(fixture_id)] if effect.positions
                else effect.fixtures.index(fixture_id),
                effect.participant_count or len(effect.fixtures), self._rig, preview,
            )
            if frame is None:
                continue
            expected, dark, is_static = frame
            if dark or not is_static or not {"pan", "tilt"} <= values.keys():
                continue
            setup = (self._rig.head(fixture_id) if self._rig is not None else None)
            if setup is None:
                continue
            if (
                setup.limits.clamp_pan(expected["pan"]) == expected["pan"]
                and setup.limits.clamp_tilt(expected["tilt"]) == expected["tilt"]
                and abs(values["pan"] - expected["pan"]) <= 1e-9
                and abs(values["tilt"] - expected["tilt"]) <= 1e-9
            ):
                return True
        return False

    def _movement_error(self, fixture_id: str, preview: bool, message: str) -> None:
        key = (fixture_id, preview)
        if self._movement_errors.get(key) != message:
            logger.warning("Multi-zone movement for %s is dark: %s", fixture_id, message)
            self._movement_errors[key] = message

    def _zone_movement(
        self, fixture_id: str, state: Values, kind: EffectType, effect: RunningEffect,
        beats: float, now: float, index: int, count: int, rig: RigStore, preview: bool,
    ) -> Optional[tuple[Values, bool]]:
        document = rig.room_file(live=not preview)
        assignment = document.assignments.get(fixture_id)
        if assignment is None or assignment.aim_target is not None:
            return None
        zones = [
            zone for zone in document.zones
            if zone.id in assignment.include_zones and zone.id not in assignment.exclude_zones
        ]
        if len(zones) < 2:
            return None
        out = dict(state)
        dark = False
        setup = rig.head(fixture_id)
        motion = self._patch.get(fixture_id).definition.motion
        if effect.params.timing == "beats":
            progress = beats / effect.params.beats
        else:
            progress = effect.phase_offset + (now - effect.phase_anchor) / effect.params.seconds
        if effect.params.selection == "alternating":
            progress += 0.5 * (index % 2)
        cycle = progress - effect.params.spread * index / count
        slot = math.floor(cycle / (1.0 + ZONE_TRAVEL_CYCLES))
        local = cycle - slot * (1.0 + ZONE_TRAVEL_CYCLES)
        zone = zones[slot % len(zones)]
        next_zone = zones[(slot + 1) % len(zones)]

        def point(area: Zone, phase: float):
            assert document.size is not None
            dx, dy = movement_offset(kind, phase)
            if effect.params.mirror in ("x", "xy"):
                dx = -dx
            if effect.params.mirror in ("y", "xy"):
                dy = -dy
            if effect.params.mirror != "none" and effect.params.mirror_alternate and index % 2:
                dx = -dx
            return area.point_at(
                0.5 + 0.5 * effect.params.size * dx,
                0.5 + 0.5 * effect.params.size * dy,
                document.size,
            )

        try:
            if document.size is None or motion is None:
                raise PositioningError("Multi-zone movement needs measured room geometry and head motion ranges.")
            if local <= 1.0:
                aim = aim_at(setup, motion, point(zone, local), state["pan"])
                out.update(pan=aim.pan, tilt=aim.tilt)
            else:
                start = aim_at(setup, motion, point(zone, 1.0), state["pan"])
                end = aim_at(setup, motion, point(next_zone, 0.0), state["pan"])
                fraction = (local - 1.0) / ZONE_TRAVEL_CYCLES
                fraction = fraction * fraction * (3.0 - 2.0 * fraction)
                out.update(
                    pan=start.pan + (end.pan - start.pan) * fraction,
                    tilt=start.tilt + (end.tilt - start.tilt) * fraction,
                )
                dark = True
            if "speed" in out:
                out["speed"] = 1.0
        except PositioningError as error:
            self._movement_error(fixture_id, preview, str(error))
            dark = True
        return out, dark


def _selected_index(index: int, selection: str) -> bool:
    return selection == "all" or (selection == "odd" and index % 2 == 0) or (
        selection == "even" and index % 2 == 1
    )


def _fits_kind(kind: EffectType, fixture, component: EffectComponent = "beam") -> bool:
    attributes = set(fixture.mode.attributes())
    if component == "ring":
        return kind not in MOVEMENT_TYPES and bool(ring_segments(fixture))
    if component != "beam":
        raise ValueError("Effect component must be 'beam' or 'ring'.")
    if kind in MOVEMENT_TYPES:
        return {"pan", "tilt"} <= attributes and fixture.definition.motion is not None
    if kind in (EffectType.RAINBOW, EffectType.TWO_COLOUR):
        return {"red", "green", "blue"} <= attributes
    return "intensity" in attributes
