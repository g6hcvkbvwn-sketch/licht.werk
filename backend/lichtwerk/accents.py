"""Accent pads (Performance milestone 3): a look laid over the live output for a moment.

An accent plays the complete look: its values, positions and FX. It is drawn over the output
at render time, like FLASH, so nothing underneath changes: live values, sequences, running FX,
strip modes, zone permissions and faders keep going. When the accent ends, the output is
exactly what runs underneath at that moment. Accents act live, also during preview.

Safety stays as for any output: limits, the beam guard and the current zone permissions
apply. A named-target position gets the same beam-guard exception as a recalled one.
"""

import logging
from threading import Lock
from typing import TYPE_CHECKING, Callable, Optional

from .fixture_service import FixtureController, FixtureDisabledError
from .positioning import PositioningError
from .scenes import resolve_scene

if TYPE_CHECKING:
    from .effect_engine import EffectEngine
    from .position_service import PositionService
    from .scene_service import SceneService

logger = logging.getLogger(__name__)


def accent_owner(pad: int) -> str:
    return f"accent:{pad}"


class AccentPlayer:
    """Running accents per pad: None = while held, else the tempo beat at which it ends."""

    def __init__(
        self, controller: FixtureController, scenes: "SceneService",
        positions: Optional["PositionService"] = None,
        effects: Optional["EffectEngine"] = None,
        beats: Optional[Callable[[], float]] = None,
    ) -> None:
        self._controller = controller
        self._scenes = scenes
        self._positions = positions
        self._effects = effects
        self._beats = beats
        self._lock = Lock()
        self._active: dict[int, Optional[float]] = {}
        self._fx_owners: dict[int, list[str]] = {}

    def values(self, scene_id: str) -> tuple[dict[str, dict[str, float]], dict[str, tuple[float, float]], list[str]]:
        """Output values of the look (positions resolved to pan/tilt), named-target aims, and
        what could not be resolved."""
        scene = self._scenes.store.get(scene_id)
        values = resolve_scene(scene, self._controller.patch)
        aims: dict[str, tuple[float, float]] = {}
        blocked: list[str] = []
        if scene.positions:
            if self._positions is None:
                blocked.append("positions – room setup not available")
            else:
                try:
                    resolved = self._positions.scene_aims(
                        {head: entry.model_dump() for head, entry in scene.positions.items()})
                except (KeyError, ValueError, PositioningError, FixtureDisabledError) as error:
                    blocked.append(f"positions – {error}")
                    resolved = {}
                for head, (pan, tilt, named) in resolved.items():
                    values.setdefault(head, {}).update(pan=pan, tilt=tilt)
                    if named:
                        aims[head] = (pan, tilt)
        return values, aims, blocked

    def start(self, pad: int, scene_id: str, beats: Optional[float] = None) -> list[str]:
        """Start (or retrigger) an accent: while held if `beats` is None, else for that many beats.

        Returns what could not run (also shown like a blocked look); everything else runs.
        """
        if beats is not None and self._beats is None:
            raise ValueError("Timed accents need the tempo clock.")
        scene = self._scenes.store.get(scene_id)
        values, aims, blocked = self.values(scene_id)
        has_fx = scene.fx is not None and bool(scene.fx.strips)
        if not values and not has_fx:
            raise ValueError(f"Look '{scene.name}' is empty: nothing to accent.")
        self._stop_fx(pad)
        owners: list[str] = []
        if has_fx:
            looks = self._scenes.looks
            if looks is None:
                blocked.append("FX are not available")
            else:
                owners, failed = looks.start_overlay(scene, accent_owner(pad))
                blocked += failed
        with self._lock:
            self._active[pad] = None if beats is None else self._beats() + beats
            self._fx_owners[pad] = owners
            self._controller.hold(accent_owner(pad), values, aims)
        self._scenes.report_look(scene, blocked)
        return blocked

    def _stop_fx(self, pad: int) -> None:
        with self._lock:
            owners = self._fx_owners.pop(pad, [])
        if self._effects is not None:
            for owner in owners:
                self._effects.stop_all(owner, overlay=True)

    def _end(self, pad: int) -> None:
        """End one accent (accent lock held)."""
        del self._active[pad]
        owners = self._fx_owners.pop(pad, [])
        if self._effects is not None:
            for owner in owners:
                self._effects.stop_all(owner, overlay=True)
        self._controller.release(accent_owner(pad))

    def release(self, pad: int) -> None:
        """Pad released: ends a while-held accent; timed accents run out by themselves."""
        with self._lock:
            if pad in self._active and self._active[pad] is None:
                self._end(pad)

    def release_held(self) -> None:
        with self._lock:
            for pad in [p for p, end in self._active.items() if end is None]:
                self._end(pad)

    def active(self) -> set[int]:
        with self._lock:
            return set(self._active)

    def tick(self) -> None:
        if self._beats is None:
            return
        with self._lock:
            if not any(end is not None for end in self._active.values()):
                return
            now = self._beats()
            for pad, end in list(self._active.items()):
                if end is not None and now >= end:
                    self._end(pad)
