"""Logical lighting FX runtime, with independent preview/live snapshots."""

import logging
import math
from contextlib import contextmanager
from dataclasses import dataclass, replace
from threading import RLock, local
from uuid import uuid4
from typing import Optional

from .effects import (
    COLOUR_TYPES,
    EFFECT_TYPES,
    MOVEMENT_TYPES,
    PRESET_COUNT,
    PRESETS,
    SHAPES,
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
from .room import RigStore, target_in_included_areas
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
    combo_id: str | None = None
    combo_name: str | None = None
    combo_slot: int | None = None
    combo_families: tuple[EffectType, ...] = ()


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
        self._lock = RLock()
        self._live: dict[EffectKey, RunningEffect] = {}
        self._preview: Optional[dict[EffectKey, RunningEffect]] = None
        # Accent FX (render-time, live only): drawn over the normal FX, never displacing them.
        self._overlay: dict[EffectKey, RunningEffect] = {}
        self._dirty: set[str] = set()
        self._movement_errors: dict[tuple[str, bool], str] = {}
        self._notice: Optional[str] = None
        self._frame_time = local()
        self._speed = 1.0
        self._time_anchor = self._tempo.now()
        self._beat_anchor = self._tempo.beats(self._time_anchor)
        self._fx_time_anchor = self._time_anchor
        self._fx_beat_anchor = self._beat_anchor

    def _time_sample(self, now: Optional[float] = None) -> tuple[float, float]:
        with self._lock:
            now = self._tempo.now() if now is None else now
            beats = self._tempo.beats(now)
            return (
                self._fx_time_anchor + (now - self._time_anchor) * self._speed,
                self._fx_beat_anchor + (beats - self._beat_anchor) * self._speed,
            )

    def set_speed(self, speed: float) -> None:
        if isinstance(speed, bool) or speed not in (0.25, 0.5, 1.0, 2.0, 4.0):
            raise ValueError("FX speed must be 0.25, 0.5, 1, 2 or 4.")
        with self._lock:
            if self._speed == speed:
                return
            now = self._tempo.now()
            self._fx_time_anchor, self._fx_beat_anchor = self._time_sample(now)
            self._time_anchor = now
            self._beat_anchor = self._tempo.beats(now)
            self._speed = speed
            for source in (self._live, self._preview or {}, self._overlay):
                for effect in source.values():
                    self._dirty.update(self._physical_fixture_id(item) for item in effect.fixtures)

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
            self._frame_time.sample = self._time_sample()
        try:
            yield
        finally:
            self._frame_time.sample = previous

    def _now(self) -> float:
        sample = getattr(self._frame_time, "sample", None)
        return self._time_sample()[0] if sample is None else sample[0]

    def _beats(self, now: float) -> float:
        sample = getattr(self._frame_time, "sample", None)
        if sample is not None:
            return sample[1]
        with self._lock:
            real_time = self._time_anchor + (now - self._fx_time_anchor) / self._speed
            return self._fx_beat_anchor + (self._tempo.beats(real_time) - self._beat_anchor) * self._speed

    def _editing(self) -> dict[EffectKey, RunningEffect]:
        return self._preview if self._preview is not None else self._live

    def _changed(self, effect: Optional[RunningEffect], overlay: bool = False) -> None:
        if effect is not None and (overlay or self._preview is None):
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
        if definition.params.stops:
            if component == "ring":
                raise ValueError("Target and zone routes are only available for beam Move effects.")
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
        patterned = params.pattern != "none" and params.size > 0
        for fixture_id in fixtures:
            setup = rig.head(fixture_id)
            motion = self._patch.get(fixture_id).definition.motion
            if motion is None:
                raise ValueError(f"Fixture '{fixture_id}' has no configured movement range.")
            assignment = document.assignments.get(fixture_id)
            for target_id in params.target_ids:
                target = rig.target(target_id)
                if target.point is None:
                    raise ValueError(f"Target '{target_id}' has no measured coordinates.")
                if patterned and not target_in_included_areas(document, assignment, target.point):
                    raise ValueError(
                        f"Target '{target_id}' is outside fixture '{fixture_id}' included areas."
                    )
                try:
                    aim_at(setup, motion, target.point, 0.5)
                except PositioningError as error:
                    raise ValueError(
                        f"Target '{target_id}' is unreachable for fixture '{fixture_id}': {error}"
                    ) from error
            for zone_id in params.zone_ids:
                zone = document.zone(zone_id)
                if (assignment is None or zone_id not in assignment.include_zones
                        or zone_id in assignment.exclude_zones):
                    raise ValueError(
                        f"Zone '{zone.name}' is excluded for fixture '{fixture_id}'. Include it first."
                    )
                if document.size is None:
                    raise ValueError("Zone routes need a measured room size.")
                try:
                    aim_at(setup, motion, zone.point_at(0.5, 0.5, document.size), 0.5)
                except PositioningError as error:
                    raise ValueError(
                        f"Zone '{zone.name}' is unreachable for fixture '{fixture_id}': {error}"
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
        combo_id: str | None = None,
        combo_name: str | None = None,
        combo_slot: int | None = None,
        combo_families: tuple[EffectType, ...] = (),
        overlay: bool = False,
    ) -> None:
        """Run an unchanged factory preset or a supplied stored definition.

        `overlay` runs it in the accent layer: over the normal FX, replacing nothing there.
        """
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
            combo_id=combo_id, combo_name=combo_name,
            combo_slot=combo_slot, combo_families=combo_families,
        )
        new_key = (owner, family(effect_type), component)
        with self._lock:
            effects = self._overlay if overlay else self._editing()
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
                    self._changed(removed, overlay)
                    if conflicts and key != new_key:
                        displaced.append(f"{key[0]} ({', '.join(removed.fixtures)})")
            if not overlay:
                self._notice = (
                    "Replaced conflicting FX instance(s): " + "; ".join(displaced)
                    if displaced else None
                )
            effects[new_key] = current
            self._changed(current, overlay)

    def start_combo(
        self, owner: str, row: int, name: str,
        launches: list[tuple[FxDefinition, list[str]]], *,
        component: EffectComponent = "beam", overlay: bool = False,
    ) -> None:
        # Validate the entire bundle before replacing anything; freeze launch time for seconds FX.
        with self._lock, self.frame():
            for definition, fixtures in launches:
                self.validate_start(definition.kind, fixtures, definition, component=component)
            self.stop_all(owner, component=component, overlay=overlay)
            token = uuid4().hex
            pages = tuple(family(definition.kind) for definition, _ in launches)
            notices = []
            for definition, fixtures in launches:
                self.start(owner, family(definition.kind), row, fixtures, definition=definition,
                           component=component, combo_id=token, combo_name=name,
                           combo_slot=row, combo_families=pages, overlay=overlay)
                if self._notice and not overlay:
                    notices.append(self._notice)
            if not overlay:
                self._notice = "; ".join(notices) or None

    def combo_effects(self, owner: str, *, component: EffectComponent = "beam") -> list[RunningEffect]:
        with self._lock:
            return [replace(effect, params=effect.params.model_copy(deep=True))
                    for key, effect in self._editing().items()
                    if key[0] == owner and effect.component == component and effect.combo_id is not None]

    def stop_combo(self, owner: str, *, component: EffectComponent = "beam") -> None:
        with self._lock:
            effects = self._editing()
            for key, effect in list(effects.items()):
                if key[0] == owner and effect.component == component and effect.combo_id is not None:
                    self._changed(effects.pop(key))

    def stop(
        self, owner: str, effect_type: EffectType, *, component: EffectComponent = "beam"
    ) -> None:
        with self._lock:
            self._changed(self._editing().pop((owner, family(effect_type), component), None))

    def stop_all(
        self, owner: Optional[str] = None, *, component: Optional[EffectComponent] = None,
        overlay: bool = False,
    ) -> None:
        with self._lock:
            effects = self._overlay if overlay else self._editing()
            for key, effect in list(effects.items()):
                if (owner is None or key[0] == owner) and (
                    component is None or effect.component == component
                ):
                    self._changed(effect, overlay)
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
            if name == "speed" and effect.params.touring:
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
            elif name == "shape":
                shape_index = round(level * (len(SHAPES) - 1))
                shape_value = SHAPES[min(max(shape_index, 0), len(SHAPES) - 1)]
                update = {"shape": shape_value}
            elif name in ("size", "spread", "softness", "maximum"):
                update = {name: float(level)}
            else:
                raise ValueError(f"Unknown effect parameter '{name}'.")
            now = self._now()
            phase_offset = effect.phase_offset
            phase_anchor = effect.phase_anchor
            launched_at = effect.launched_at
            launch_beat = effect.launch_beat
            if effect.params.touring and "travel" in update:
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

    def set_palette(
        self, owner: str, palette: list[str], *, component: EffectComponent = "beam",
    ) -> None:
        """Edit runtime colour FX without restarting its clock or changing its preset."""
        with self._lock:
            effects = self._editing()
            key = (owner, EffectType.RAINBOW, component)
            effect = effects.get(key)
            if effect is None or effect.kind not in COLOUR_TYPES:
                raise ValueError("No Colour FX is running on this strip. Launch one first.")
            params = EffectParams.model_validate({
                **effect.params.model_dump(), "palette": palette, "colour_mode": "palette",
            })
            effects[key] = replace(effect, params=params)

    def set_route(
        self, owner: str, target_ids: list[str], zone_ids: list[str], *,
        component: EffectComponent = "beam",
    ) -> None:
        """Replace the running Move FX route at runtime; its preset stays unchanged.

        If the stop the heads are at (or travelling to) is still in the new route, the tour
        continues from that stop's hold, so adding or removing other stops never jumps.
        """
        with self._lock:
            effects = self._editing()
            key = (owner, EffectType.CIRCLE, component)
            effect = effects.get(key)
            if effect is None or effect.kind not in MOVEMENT_TYPES or component != "beam":
                raise ValueError("No Move FX is running on this strip. Launch one first.")
            params = EffectParams.model_validate({
                **effect.params.model_dump(), "target_ids": target_ids, "zone_ids": zone_ids,
            })
            if params.stops:
                self._validate_tour_targets(effect.fixtures, params, self._rig)
            now = self._now()
            old = effect.params
            current: Optional[str] = None
            if old.stops:
                duration = old.travel + old.hold
                segment = 0 if old.single_point else math.floor(
                    self._tour_elapsed(effect, now) / duration
                )
                current = old.stops[segment % len(old.stops)]
            elapsed = 0.0
            if current is not None and current in params.stops and params.touring:
                elapsed = params.stops.index(current) * (params.travel + params.hold) + params.travel
            launched_at, launch_beat = effect.launched_at, effect.launch_beat
            if params.timing == "seconds":
                launched_at = now - elapsed
            else:
                launch_beat = self._beats(now) - elapsed
            effects[key] = replace(
                effect, params=params, launched_at=launched_at, launch_beat=launch_beat,
            )
            self._changed(effect)

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
        self, owner: str, *, component: Optional[EffectComponent] = None, overlay: bool = False,
    ) -> bool:
        with self._lock:
            return any(
                key[0] == owner and (component is None or effect.component == component)
                for key, effect in (self._overlay if overlay else self._editing()).items()
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
            effects = list((self._editing() if preview else self._live).values())
            if not preview:
                effects += self._overlay.values()
            return any(
                effect.kind in MOVEMENT_TYPES and effect.active and fixture_id in effect.fixtures
                for effect in effects
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
            for effect in [*self._live.values(), *self._overlay.values()]:
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
        rig: Optional[RigStore] = None, overlay: bool = False,
    ) -> EffectFrame:
        """Apply the normal FX, or with `overlay` only the accent FX (live output only)."""
        with self._lock:
            source = self._overlay if overlay else (self._editing() if preview else self._live)
            effects = [
                (effect.kind, effect)
                for effect in source.values()
                if effect.active and effect.component == "beam" and fixture_id in effect.fixtures
            ]
            ring_effects = [
                (effect.kind, effect)
                for effect in source.values()
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
                if kind in MOVEMENT_TYPES and effect.params.stops:
                    tour = self._route(
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

    def _route(
        self, fixture_id: str, state: Values, kind: EffectType, effect: RunningEffect,
        now: float, index: int, count: int, rig: Optional[RigStore], preview: bool,
    ) -> Optional[tuple[Values, bool, bool]]:
        """Render a single anchored point or a target/zone tour as (state, dark, static)."""
        if rig is None or "pan" not in state or "tilt" not in state:
            self._movement_error(fixture_id, preview, "Target or zone route has no configured room rig.")
            return None
        params = effect.params
        try:
            document = rig.room_file(live=not preview)
            setup = rig.head(fixture_id)
            motion = self._patch.get(fixture_id).definition.motion
            if motion is None:
                raise PositioningError("The fixture has no movement range.")
            targets = {target.id: target for target in document.targets}
            pattern_kind = kind if params.pattern == "current" else (
                EffectType.CIRCLE if params.pattern == "circle" else
                EffectType.FIGURE8 if params.pattern == "figure8" else None
            )
            size = params.size if pattern_kind is not None else 0.0

            def offset(phase: float) -> tuple[float, float]:
                if pattern_kind is None or size == 0.0:
                    return 0.0, 0.0
                dx, dy = movement_offset(pattern_kind, phase)
                if params.mirror in ("x", "xy"):
                    dx = -dx
                if params.mirror in ("y", "xy"):
                    dy = -dy
                if params.mirror != "none" and params.mirror_alternate and index % 2:
                    dx = -dx
                return dx, dy

            def centre(stop: str, reference: float):
                if params.target_ids:
                    point = targets[stop].point
                    if point is None:
                        raise PositioningError("A route target has no measured coordinates.")
                else:
                    if document.size is None:
                        raise PositioningError("Zone routes need a measured room size.")
                    point = document.zone(stop).point_at(0.5, 0.5, document.size)
                return aim_at(setup, motion, point, reference)

            def aim(stop: str, phase: float, reference: float) -> tuple[float, float]:
                dx, dy = offset(phase)
                if params.zone_ids:
                    assert document.size is not None
                    point = document.zone(stop).point_at(
                        0.5 + 0.5 * size * dx, 0.5 + 0.5 * size * dy, document.size
                    )
                    result = aim_at(setup, motion, point, reference)
                    return result.pan, result.tilt
                base = centre(stop, reference)
                return (
                    setup.limits.clamp_pan(base.pan + size * 30.0 * dx / motion.pan_degrees),
                    setup.limits.clamp_tilt(base.tilt + size * 30.0 * dy / motion.tilt_degrees),
                )

            out = dict(state)
            if "speed" in out:
                out["speed"] = 1.0
            static = bool(params.target_ids) and size == 0.0
            if params.single_point:
                out["pan"], out["tilt"] = aim(
                    params.stops[0], self._phase(effect, now, None, index, count), state["pan"]
                )
                self._movement_errors.pop((fixture_id, preview), None)
                return out, False, static

            stops = params.stops
            elapsed = self._tour_elapsed(effect, now)
            duration = params.travel + params.hold
            tour_duration = duration * len(stops)
            spread_offset = params.spread * index / count * tour_duration if count else 0.0
            alternate_offset = (
                0.5 * tour_duration * (index % 2)
                if params.selection == "alternating" else 0.0
            )
            elapsed = max(0.0, elapsed) + spread_offset + alternate_offset
            segment = math.floor(elapsed / duration)
            within = elapsed - segment * duration
            stop = stops[segment % len(stops)]
            previous = stops[(segment - 1) % len(stops)]
            if within + 1e-9 < params.travel:
                if params.target_ids:
                    end = centre(stop, state["pan"])
                    end_pan, end_tilt = end.pan, end.tilt
                else:
                    end_pan, end_tilt = aim(stop, 0.0, state["pan"])
                if segment == 0:
                    start_pan, start_tilt = state["pan"], state["tilt"]
                elif params.target_ids:
                    start = centre(previous, state["pan"])
                    start_pan, start_tilt = start.pan, start.tilt
                else:
                    start_pan, start_tilt = aim(previous, 1.0, state["pan"])
                fraction = within / params.travel
                fraction = fraction * fraction * (3.0 - 2.0 * fraction)
                out.update(
                    pan=start_pan + (end_pan - start_pan) * fraction,
                    tilt=start_tilt + (end_tilt - start_tilt) * fraction,
                )
                self._movement_errors.pop((fixture_id, preview), None)
                return out, True, False
            local_phase = (max(0.0, within - params.travel) / params.hold) % 1.0
            out["pan"], out["tilt"] = aim(stop, local_phase, state["pan"])
            self._movement_errors.pop((fixture_id, preview), None)
            return out, False, static
        except (KeyError, PositioningError) as error:
            self._movement_error(fixture_id, preview, str(error))
            return None

    def stationary_target(
        self, fixture_id: str, values: Values, *, preview: bool = False
    ) -> bool:
        """True only at an exact, permitted-by-runtime stationary target hold endpoint."""
        with self._lock:
            effects = list((self._editing() if preview else self._live).values())
            if not preview:
                effects += self._overlay.values()
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
            frame = self._route(
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
