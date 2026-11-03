"""Looks: the FX part of scenes, applied per controller strip.

Scenes are domain data; strips (and their group/single modes) belong to the controller. This
module connects the two, so the scene service never depends on APC specifics. It only calls
existing services: strip mode switching, FX start (with all safety checks) and FX stop.
"""

import logging
from typing import TYPE_CHECKING, Literal

from .apc40_control import EXPECTED_ERRORS, effect_owner
from .effects import EFFECT_LABELS, ComboDefinition, FxDefinition, family
from .scenes import LookFx, LookStripFx, Scene

if TYPE_CHECKING:
    from .apc40_control import Apc40Control
    from .scene_service import SceneService

logger = logging.getLogger(__name__)
COMPONENTS: tuple[Literal["beam", "ring"], ...] = ("beam", "ring")


class LookPlayer:
    def __init__(self, control: "Apc40Control", scenes: "SceneService") -> None:
        self._control = control
        self._scenes = scenes

    def _tracks(self, fixture_ids: set[str]) -> list[int]:
        """Strips with at least one of the fixtures."""
        return [
            strip.track for strip in self._control.strips
            if strip.target is not None
            and {f.patch.id for f in self._control.fixtures(strip.track)} & fixture_ids
        ]

    def _scene_fixtures(self, scene: Scene) -> set[str]:
        return set(self._scenes.scene_attributes(scene))

    @property
    def lock(self):
        """The APC strip lock. Take it before the sequence lock (lock order)."""
        return self._control.lock

    def capture(self, fixture_ids: set[str]) -> LookFx:
        """Strip modes and running FX of the active strips that hold any of the fixtures."""
        control = self._control
        effects = control.effects
        modes: dict[str, Literal["group", "single"]] = {}
        entries: list[LookStripFx] = []
        with control.lock:
            running = effects.all_running(preview=True) if effects is not None else []
            for track in self._tracks(fixture_ids):
                strip = next(s for s in control.strips if s.track == track)
                if strip.family is not None:
                    modes[strip.family] = control.mode(strip.family)  # type: ignore[assignment]
                if not control.is_active(track):
                    continue
                owner = effect_owner(track)
                for component in COMPONENTS:
                    owned = [e for o, _, e in running if o == owner and e.component == component and e.active]
                    combos: dict[str, list] = {}
                    for effect in owned:
                        if effect.combo_id is not None:
                            combos.setdefault(effect.combo_id, []).append(effect)
                            continue
                        entries.append(LookStripFx(
                            strip=strip.target, component=component, preset=effect.preset,
                            effects=[FxDefinition(name=effect.name, kind=effect.kind, params=effect.params)],
                        ))
                    for bundle in combos.values():
                        entries.append(LookStripFx(
                            strip=strip.target, component=component,
                            preset=bundle[0].combo_slot or 0, combo=bundle[0].combo_name or "Combo",
                            effects=[FxDefinition(name=e.name, kind=e.kind, params=e.params) for e in bundle],
                        ))
        return LookFx(modes=modes, strips=entries)

    def prepare(self, scene: Scene) -> None:
        """Switch the strip modes the look was captured in (before its values are applied)."""
        assert scene.fx is not None
        with self._control.lock:
            for family_name, mode in scene.fx.modes.items():
                self._control.set_family_mode(family_name, mode)

    def apply(self, scene: Scene) -> list[str]:
        """Stop the FX of the look's active strips, then start the look's FX.

        Returns what could not run; everything else runs anyway (the show never stops).
        """
        assert scene.fx is not None
        control = self._control
        blocked: list[str] = []
        if control.effects is None or control.fx_service is None:
            return ["FX are not available"] if scene.fx.strips else []
        with control.lock:
            by_target = {s.target: s for s in control.strips if s.target is not None}
            tracks = set(self._tracks(self._scene_fixtures(scene)))
            tracks.update(by_target[e.strip].track for e in scene.fx.strips if e.strip in by_target)
            for track in sorted(tracks):
                if control.is_active(track):
                    control.effects.stop_all(effect_owner(track))
            for entry in scene.fx.strips:
                strip = by_target.get(entry.strip)
                names = " + ".join(EFFECT_LABELS[family(e.kind)] for e in entry.effects)
                where = f"{names} FX on {strip.label if strip else entry.strip}"
                if strip is None:
                    blocked.append(f"{where} – no controller strip for '{entry.strip}'")
                    continue
                if not control.is_active(strip.track):
                    blocked.append(f"{where} – strip is not active")
                    continue
                blocked += self._start(effect_owner(strip.track), entry, where, strip.label)
        return blocked

    def start_overlay(self, scene: Scene, prefix: str) -> tuple[list[str], list[str]]:
        """Start the look's FX in the accent layer (over everything, nothing replaced).

        Strip modes are not switched and running FX are not stopped. Returns the owners
        started (one per strip, `prefix:strip`) and what could not run.
        """
        if scene.fx is None or not scene.fx.strips:
            return [], []
        control = self._control
        if control.effects is None or control.fx_service is None:
            return [], ["FX are not available"]
        owners: list[str] = []
        blocked: list[str] = []
        with control.lock:
            labels = {s.target: s.label for s in control.strips if s.target is not None}
            for entry in scene.fx.strips:
                label = labels.get(entry.strip, entry.strip)
                names = " + ".join(EFFECT_LABELS[family(e.kind)] for e in entry.effects)
                owner = f"{prefix}:{entry.strip}"
                failed = self._start(owner, entry, f"{names} FX on {label}", label, overlay=True)
                blocked += failed
                if control.effects.owner_running(owner, overlay=True):
                    owners.append(owner)
        return owners, blocked

    def _start(self, owner: str, entry: LookStripFx, where: str, label: str, *,
               overlay: bool = False) -> list[str]:
        fx_service = self._control.fx_service
        assert fx_service is not None
        target = entry.strip
        if entry.combo is None:
            effect = entry.effects[0]
            try:
                fx_service.start(owner, target, family(effect.kind), entry.preset, effect,
                                 component=entry.component, overlay=overlay)
            except EXPECTED_ERRORS as error:
                return [f"{where} – {error}"]
            return []
        # A Combo: run the members that are allowed, report the others.
        allowed: list[FxDefinition] = []
        blocked: list[str] = []
        for effect in entry.effects:
            try:
                fx_service.validate_start(target, family(effect.kind), entry.preset, effect, component=entry.component)
                allowed.append(effect)
            except EXPECTED_ERRORS as error:
                blocked.append(f"{EFFECT_LABELS[family(effect.kind)]} FX of Combo '{entry.combo}' on "
                               f"{label} – {error}")
        if allowed:
            try:
                fx_service.start_combo(owner, target, entry.preset,
                                       ComboDefinition(name=entry.combo, effects=allowed),
                                       component=entry.component, overlay=overlay)
            except EXPECTED_ERRORS as error:
                blocked.append(f"{where} – {error}")
        return blocked
