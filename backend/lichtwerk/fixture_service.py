"""Live logical fixture state, fades and master, rendered into DMX through the patch.

Precedence rules:
- Manual writes replace scene fades and override the current sequence step.
- A fade starts at the attribute's current (possibly mid-fade) value. A new command
  replaces only the fades of the attributes it sets; other fades keep running.
- Master scales intensity at render time only; blackout acts later, in LightingService.
  Neither changes live state nor stored scenes.
- The beam guard (exclude zones) also acts at render time only: it darkens a head's beam
  while it would hit an excluded area. Manual pan/tilt commands stop at zone borders.
- Preview mode (project.md 5.5): while on, every command changes a separate preview state that
  is never sent. `go()` promotes its current animated state and remaining fades. Holds (flash,
  strobe-hold) are render-time overrides of the live output, so they act live in preview too
  and never change logical state.
- Effects (effect_engine.py) also act at render time only: they modulate the live state before
  holds, master, limits and the beam guard, so a flash overrides a chase and master scales it.
"""

import time
from contextlib import contextmanager, nullcontext
from collections.abc import Callable, Iterable
from threading import RLock, local

from typing import TYPE_CHECKING, Optional

from .fixtures import INTENSITY_ATTRIBUTES, STROBE_ATTRIBUTES, render_mode
from .patch import Patch, ResolvedFixture
from .rings import expand_legacy_ring_values
from .service import LightingService
from .transitions import MAX_FADE_SECONDS, Fade

if TYPE_CHECKING:
    from .beam_guard import BeamGuard
    from .effect_engine import EffectEngine
    from .room import RigStore

Values = dict[str, float]


class FixtureDisabledError(Exception):
    """Raised when a command targets only fixtures that are disabled in the patch."""


def validate_level(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Value for '{name}' must be a number between 0.0 and 1.0.")
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"Value for '{name}' must be between 0.0 and 1.0.")
    return float(value)


class FixtureController:
    """Owns per-fixture logical values (0.0-1.0) and writes their DMX translation.

    Startup is dark with master 1.0: attributes start at their definition default (0.0
    unless the definition says otherwise) and moving heads start at their rest position
    if one is configured, else at pan/tilt 0.0. Strobe is capped at `max_strobe`.
    """

    def __init__(
        self,
        patch: Patch,
        lighting: LightingService,
        clock: Callable[[], float] = time.monotonic,
        rig: Optional["RigStore"] = None,
        max_strobe: float = 1.0,
    ) -> None:
        self._patch = patch
        self._rig = rig
        self._guard: Optional["BeamGuard"] = None
        if rig is not None:
            from .beam_guard import BeamGuard

            self._guard = BeamGuard(rig, patch)
        self._max_strobe = max_strobe
        self._lighting = lighting
        lighting.set_blackout_keep(patch.blackout_keep_channels())
        self._clock = clock
        self._lock = RLock()
        self._master = 1.0
        self._fades: dict[tuple[str, str], Fade] = {}
        self._preview_fades: dict[tuple[str, str], Fade] = {}
        self._edit_context = local()
        self._tick_listeners: list[Callable[[], None]] = []
        self._manual_filter: Optional[Callable[[dict[str, Values]], dict[str, Values]]] = None
        self._batch_depth = 0
        self._batch_fixtures: set[str] = set()
        self._controlled_depth = 0
        self._state: dict[str, Values] = {
            fixture.patch.id: self._initial_state(fixture) for fixture in patch.fixtures
        }
        self._preview: Optional[dict[str, Values]] = None
        self._static_aims: dict[str, tuple[float, float]] = {}
        self._preview_static_aims: Optional[dict[str, tuple[float, float]]] = None
        # Held buttons: owner -> fixture id -> attribute values forced at render time.
        self._holds: dict[str, dict[str, Values]] = {}
        self._effects: Optional["EffectEngine"] = None
        self._position_listener: Optional[Callable[[list[str]], None]] = None
        self._render(patch.fixtures)

    def set_position_listener(self, listener: Callable[[list[str]], None]) -> None:
        self._position_listener = listener

    def set_manual_filter(self, callback: Callable[[dict[str, Values]], dict[str, Values]]) -> None:
        self._manual_filter = callback

    def add_tick_listener(self, callback: Callable[[], None]) -> None:
        self._tick_listeners.append(callback)

    @contextmanager
    def editing_context(self, preview: bool):
        """Let the central show tick advance live and preview independently."""
        with self._lock:
            previous = getattr(self._edit_context, "live", False)
            self._edit_context.live = not preview
            try:
                if self._rig is not None:
                    with self._rig.editing_context(preview):
                        yield
                else:
                    yield
            finally:
                self._edit_context.live = previous

    def editing_preview(self) -> bool:
        return self._preview is not None and not getattr(self._edit_context, "live", False)

    def check_manual(self, changes: dict[str, Values]) -> None:
        with self._lock:
            if self._controlled_depth == 0 and self._manual_filter is not None:
                self._manual_filter(changes)

    @contextmanager
    def controlled_writes(self):
        """Only scene/sequence internals bypass the manual ownership guard."""
        with self._lock:
            self._controlled_depth += 1
            try:
                yield
            finally:
                self._controlled_depth -= 1

    @contextmanager
    def batch(self):
        """Publish only the final frame of a compound logical command."""
        with self._lock:
            self._batch_depth += 1
            try:
                yield
            finally:
                self._batch_depth -= 1
                if self._batch_depth == 0:
                    fixtures = [self._patch.get(key) for key in self._batch_fixtures]
                    self._batch_fixtures.clear()
                    self._render(fixtures)

    def cancel_fades(self, attributes: dict[str, set[str]]) -> None:
        with self._lock:
            fades = self._preview_fades if self.editing_preview() else self._fades
            state = self._editing()
            now = self._clock()
            changed: set[str] = set()
            for fixture_id, names in attributes.items():
                for name in names:
                    fade = fades.pop((fixture_id, name), None)
                    if fade is not None:
                        state[fixture_id][name] = fade.value_at(now)
                        changed.add(fixture_id)
            if not self.editing_preview():
                self._render(self._patch.get(key) for key in changed)

    def clear_static_aims(self, heads: Iterable[str]) -> None:
        with self._lock:
            aims = self._preview_static_aims if self.editing_preview() else self._static_aims
            for head in heads:
                aims.pop(head, None)

    def time_fades_in_beats(
        self, attributes: dict[str, set[str]], duration: float, beats: Callable[[float], float],
    ) -> None:
        """Use the shared continuous beat clock without replacing central fade handling."""
        with self._lock:
            fades = self._preview_fades if self.editing_preview() else self._fades
            start = beats(self._clock())
            for fixture_id, names in attributes.items():
                for name in names:
                    key = (fixture_id, name)
                    fade = fades.get(key)
                    if fade is not None:
                        fades[key] = Fade(fade.start_value, fade.target, start, duration, beats)

    def set_effects(self, effects: "EffectEngine") -> None:
        """Connect the effect engine; `tick` renders its fixtures continuously."""
        with self._lock:
            self._effects = effects

    def stop_movement_for(self, fixture_ids: set[str]) -> None:
        with self._lock:
            if self._effects is not None:
                self._effects.stop_movement_for(fixture_ids)
                self._render(self._patch.fixtures)

    def _initial_state(self, fixture: ResolvedFixture) -> Values:
        state = {name: 0.0 for name in fixture.mode.attributes()}
        state.update(fixture.mode.defaults())
        if self._rig is not None and "pan" in state and "tilt" in state:
            rest = self._rig.head(fixture.patch.id).rest
            if rest is not None:
                state["pan"], state["tilt"] = rest.pan, rest.tilt
        return state

    def refresh(self, fixture_id: str) -> None:
        """Re-render one fixture, e.g. after its movement limits changed."""
        fixture = self._patch.get(fixture_id)
        with self._lock:
            self._render([fixture])

    def refresh_all(self) -> None:
        """Re-render everything, e.g. after zones or the room changed."""
        with self._lock:
            self._render(self._patch.fixtures)

    @property
    def guard(self) -> Optional["BeamGuard"]:
        return self._guard

    @property
    def patch(self) -> Patch:
        return self._patch

    def fixtures(self) -> tuple[ResolvedFixture, ...]:
        return self._patch.fixtures

    def _editing(self) -> dict[str, Values]:
        """The state commands change: the preview state while preview is on, else live."""
        return self._preview if self.editing_preview() else self._state

    def state(self, fixture_id: str) -> Values:
        """Logical values as commands see them (the preview values while preview is on)."""
        self._patch.get(fixture_id)
        with self._lock:
            return dict(self._editing()[fixture_id])

    def live_state(self, fixture_id: str) -> Values:
        """Logical values being output now (ignores preview; holds not included)."""
        self._patch.get(fixture_id)
        with self._lock:
            return dict(self._state[fixture_id])

    def output_state(self, fixture_id: str) -> Values:
        """Live values with effects and holds applied, before master and blackout (display)."""
        self._patch.get(fixture_id)
        with self._lock, self._effects.frame() if self._effects is not None else nullcontext():
            return self._protected_state(fixture_id, self._held_state(fixture_id))

    def preview_output_state(self, fixture_id: str) -> Values:
        """Editing values with the editing permission mask, for the map."""
        with self._lock, self._effects.frame() if self._effects is not None else nullcontext():
            state = self.state(fixture_id)
            if self._effects is not None:
                frame = self._effects.render(fixture_id, state, preview=True, rig=self._rig)
                state = frame.values
                if frame.dark_movement:
                    for name in ("intensity", "strobe"):
                        if name in state:
                            state[name] = 0.0
            return self._protected_state(fixture_id, state, preview=True)

    def static_target_active(self, fixture_id: str, values: Values, *, preview: bool = False) -> bool:
        if self._guard is None or not self._guard.status(fixture_id).active:
            return False
        if self._effects is not None and self._effects.stationary_target(fixture_id, values, preview=preview):
            return True
        aims = self._preview_static_aims if preview and self._preview_static_aims is not None else self._static_aims
        endpoint = aims.get(fixture_id)
        moving = self._effects is not None and self._effects.movement_active(fixture_id, preview=preview)
        fading = not preview and any((fixture_id, name) in self._fades for name in ("pan", "tilt"))
        return (
            endpoint is not None and not moving and not fading
            and abs(values["pan"] - endpoint[0]) <= 1e-10
            and abs(values["tilt"] - endpoint[1]) <= 1e-10
        )

    def _protected_state(self, fixture_id: str, values: Values, *, preview: bool = False) -> Values:
        state = dict(values)
        if "pan" not in state or "tilt" not in state:
            return state
        if self._rig is None:
            for name in ("intensity", "strobe"):
                if name in state:
                    state[name] = 0.0
            return state
        limits = self._rig.head(fixture_id).limits
        state["pan"] = limits.clamp_pan(state["pan"])
        state["tilt"] = limits.clamp_tilt(state["tilt"])
        assert self._guard is not None
        exception = self.static_target_active(fixture_id, state, preview=preview)
        if not exception and not self._guard.allowed(fixture_id, state["pan"], state["tilt"], live=not preview):
            for name in ("intensity", "strobe"):
                if name in state:
                    state[name] = 0.0
        return state

    def snapshot(self) -> dict[str, Values]:
        """Current values of every enabled fixture (used to capture scenes; preview if on)."""
        with self._lock:
            state = self._editing()
            snapshot = {
                fixture.patch.id: dict(state[fixture.patch.id])
                for fixture in self._patch.fixtures
                if fixture.patch.enabled
            }
            fades = self._preview_fades if self.editing_preview() else self._fades
            now = self._clock()
            for (fixture_id, name), fade in fades.items():
                if fixture_id in snapshot:
                    snapshot[fixture_id][name] = fade.value_at(now)
            return snapshot

    # --- preview ----------------------------------------------------------------------------

    def preview_active(self) -> bool:
        with self._lock:
            return self._preview is not None

    def start_preview(self) -> None:
        """Copy the current live state and running fade phase into an independent preview."""
        with self._lock:
            if self._preview is not None:
                return
            changed = self._advance_fades(self._state, self._fades, self._clock())
            self._render(self._patch.get(key) for key in changed)
            preview = {fixture_id: dict(values) for fixture_id, values in self._state.items()}
            self._preview = preview
            self._preview_fades = dict(self._fades)
            self._preview_static_aims = dict(self._static_aims)

    def go(self) -> None:
        """Promote the current preview phase, preserving remaining fades, and leave preview."""
        with self._lock:
            if self._preview is None:
                return
            self._advance_fades(self._preview, self._preview_fades, self._clock())
            self._state = self._preview
            self._preview = None
            self._static_aims = self._preview_static_aims or {}
            self._preview_static_aims = None
            self._fades = self._preview_fades
            self._preview_fades = {}
            self._render(self._patch.fixtures)

    # --- holds (flash, strobe-hold) ---------------------------------------------------------

    def hold(self, owner: str, values: dict[str, Values]) -> None:
        """Force attribute values on the live output while a button is held."""
        with self._lock:
            self._holds[owner] = {fixture_id: dict(v) for fixture_id, v in values.items()}
            self._render(self._patch.get(fixture_id) for fixture_id in values)

    def release(self, owner: str) -> None:
        with self._lock:
            released = self._holds.pop(owner, None)
            if released:
                self._render(self._patch.get(fixture_id) for fixture_id in released)

    def holding(self, owner: str) -> bool:
        with self._lock:
            return owner in self._holds

    def _held_state(self, fixture_id: str) -> Values:
        state = dict(self._state[fixture_id])
        dark_movement = False
        if self._effects is not None:
            frame = self._effects.render(fixture_id, state, rig=self._rig)
            state, dark_movement = frame.values, frame.dark_movement
        for held in self._holds.values():
            state.update(held.get(fixture_id, {}))
        if dark_movement:
            for name in ("intensity", "strobe"):
                if name in state:
                    state[name] = 0.0
        return state

    def master(self) -> float:
        with self._lock:
            return self._master

    def transition_active(self) -> bool:
        with self._lock:
            return bool(self._fades)

    def set_values(self, fixture_id: str, values: Values) -> Values:
        """Immediately set some attributes of one fixture; cancels their running fades."""
        fixture = self._patch.get(fixture_id)
        if not fixture.patch.enabled:
            raise FixtureDisabledError(f"Fixture '{fixture_id}' is disabled in the patch.")
        normalized = expand_legacy_ring_values(fixture, values)
        allowed = fixture.mode.attributes()
        for name, value in normalized.items():
            if name not in allowed:
                raise ValueError(
                    f"Fixture '{fixture_id}' has no attribute '{name}' "
                    f"(available: {', '.join(allowed)})."
                )
            validate_level(name, value)
        changes = {fixture_id: normalized}
        if self._manual_filter is not None:
            changes = self._manual_filter(changes)
        self.apply(self._guard_manual(changes), fade_seconds=0.0)
        return self.state(fixture_id)

    def set_group_values(self, group: str, values: Values) -> list[str]:
        """Immediately set attributes on every enabled group member that has them.

        Returns the ids of the fixtures that changed.
        """
        members = self._patch.group(group)
        for name, value in values.items():
            validate_level(name, value)
            if not any(name in member.mode.attributes() for member in members):
                raise ValueError(f"No fixture in group '{group}' has attribute '{name}'.")
        enabled = [member for member in members if member.patch.enabled]
        if not enabled:
            raise FixtureDisabledError(f"Every fixture in group '{group}' is disabled.")
        changes = {
            member.patch.id: expand_legacy_ring_values(member, {
                name: value for name, value in values.items() if name in member.mode.attributes()
            })
            for member in enabled
        }
        changes = {fixture_id: v for fixture_id, v in changes.items() if v}
        if self._manual_filter is not None:
            changes = self._manual_filter(changes)
        self.apply(self._guard_manual(changes), fade_seconds=0.0)
        return list(changes)

    def _guard_manual(self, changes: dict[str, Values]) -> dict[str, Values]:
        """Stop manual pan/tilt moves at zone borders (see beam_guard.py)."""
        if self._guard is None:
            return changes
        guarded: dict[str, Values] = {}
        with self._lock:
            for fixture_id, values in changes.items():
                if "pan" not in values and "tilt" not in values:
                    guarded[fixture_id] = values
                    continue
                state = self._editing()[fixture_id]
                start = (state["pan"], state["tilt"])
                end = (values.get("pan", start[0]), values.get("tilt", start[1]))
                pan, tilt = self._guard.limit_move(fixture_id, start, end, live=self._preview is None)
                updated = dict(values)
                if "pan" in values or pan != start[0]:
                    updated["pan"] = pan
                if "tilt" in values or tilt != start[1]:
                    updated["tilt"] = tilt
                guarded[fixture_id] = updated
        return guarded

    def apply(
        self, changes: dict[str, Values], fade_seconds: float, *,
        static_target: bool = False, preserve_aim: bool = False,
    ) -> None:
        """Move attributes to new values, immediately or as a fade. Central for all commands.

        `changes` must already be validated (known enabled fixtures and attributes).
        """
        if not 0.0 <= fade_seconds <= MAX_FADE_SECONDS:
            raise ValueError(f"Fade time must be between 0 and {MAX_FADE_SECONDS:g} seconds.")
        changes = {
            fixture_id: expand_legacy_ring_values(self._patch.get(fixture_id), values)
            for fixture_id, values in changes.items()
        }
        now = self._clock()
        with self._lock:
            self.check_manual(changes)
            moved = [fixture_id for fixture_id, values in changes.items() if "pan" in values or "tilt" in values]
            if moved and not static_target and not preserve_aim and self._position_listener is not None:
                self._position_listener(moved)
            aims = self._preview_static_aims if self.editing_preview() else self._static_aims
            for fixture_id, values in changes.items():
                if "pan" in values or "tilt" in values:
                    if not preserve_aim:
                        aims.pop(fixture_id, None)
                    if static_target and "pan" in values and "tilt" in values:
                        aims[fixture_id] = (values["pan"], values["tilt"])
            preview = self.editing_preview()
            state_map = self._editing()
            fades = self._preview_fades if preview else self._fades
            changed = self._advance_fades(state_map, fades, now)
            touched: list[ResolvedFixture] = [self._patch.get(key) for key in changed]
            for fixture_id, values in changes.items():
                state = state_map[fixture_id]
                for name, target in values.items():
                    key = (fixture_id, name)
                    fades.pop(key, None)
                    if fade_seconds > 0 and state[name] != target:
                        fades[key] = Fade(state[name], float(target), now, fade_seconds)
                    else:
                        state[name] = float(target)
                touched.append(self._patch.get(fixture_id))
            if not preview:
                self._render(touched)

    def set_master(self, level: float) -> None:
        level = validate_level("master", level)
        with self._lock:
            self._master = level
            self._render(self._patch.fixtures)

    def tick(self) -> bool:
        """Advance running fades and effects to the current time.

        Returns True while fades remain.
        """
        for callback in self._tick_listeners:
            callback()
        now = self._clock()
        with self._lock:
            changed = self._effects.take_render_set() if self._effects is not None else set()
            if self._preview is not None:
                self._advance_fades(self._preview, self._preview_fades, now)
            if not self._fades and not changed:
                return False
            changed.update(self._advance_fades(self._state, self._fades, now))
            self._render(self._patch.get(fixture_id) for fixture_id in sorted(changed))
            return bool(self._fades)

    @staticmethod
    def _advance_fades(state: dict[str, Values], fades: dict[tuple[str, str], Fade], now: float) -> set[str]:
        changed: set[str] = set()
        for key, fade in list(fades.items()):
            fixture_id, name = key
            state[fixture_id][name] = fade.value_at(now)
            changed.add(fixture_id)
            if fade.finished(now):
                del fades[key]
        return changed

    def _render(self, fixtures: Iterable[ResolvedFixture]) -> None:
        """Translate fixtures into DMX, one published update per affected universe."""
        if self._batch_depth:
            self._batch_fixtures.update(fixture.patch.id for fixture in fixtures)
            return
        per_universe: dict[int, dict[int, int]] = {}
        with self._effects.frame() if self._effects is not None else nullcontext():
            for fixture in fixtures:
                if not fixture.patch.enabled:
                    continue
                state = self._protected_state(fixture.patch.id, self._held_state(fixture.patch.id))
                for name in INTENSITY_ATTRIBUTES:
                    if name in state:
                        state[name] *= self._master
                for name in STROBE_ATTRIBUTES:
                    if name in state:
                        state[name] = min(state[name], self._max_strobe)
                start = fixture.patch.start_address
                channels = per_universe.setdefault(fixture.patch.universe, {})
                for offset, value in enumerate(render_mode(fixture.mode, state)):
                    channels[start + offset] = value
        for universe, channels in sorted(per_universe.items()):
            self._lighting.set_channels(universe, channels)
