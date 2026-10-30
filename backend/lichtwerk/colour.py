"""Hue/saturation control on top of the fixtures' red/green/blue attributes.

Colour is always set at full brightness (the dimmer handles intensity). Hue and saturation are
remembered per fixture so that turning saturation to 0 (white) and back keeps the hue; the memory
is ignored as soon as something else (a scene, a fade, a slider) changes the colour.
"""

import colorsys
from collections.abc import Iterable
from threading import Lock
from typing import Optional

from .fixture_service import FixtureController
from .patch import ResolvedFixture

RGB = ("red", "green", "blue")


def has_rgb(fixture: ResolvedFixture) -> bool:
    return set(RGB) <= set(fixture.mode.attributes())


class ColourControl:
    def __init__(self, controller: FixtureController) -> None:
        self._controller = controller
        self._lock = Lock()
        # fixture id -> (hue, saturation, rgb that was set)
        self._memory: dict[str, tuple[float, float, tuple[float, float, float]]] = {}

    def hue_saturation(self, fixture_id: str) -> tuple[float, float]:
        state = self._controller.state(fixture_id)
        rgb = (state["red"], state["green"], state["blue"])
        with self._lock:
            remembered = self._memory.get(fixture_id)
        if remembered is not None and remembered[2] == rgb:
            return remembered[0], remembered[1]
        hue, saturation, value = colorsys.rgb_to_hsv(*rgb)
        if value == 0.0:  # dark: no colour information, start from saturated red
            return (remembered[0] if remembered else 0.0), 1.0
        return hue, saturation

    def set(
        self,
        fixtures: Iterable[ResolvedFixture],
        hue: Optional[float] = None,
        saturation: Optional[float] = None,
        hue_delta: float = 0.0,
    ) -> None:
        """Change hue (0-1, wraps), saturation (0-1) or both, for every enabled RGB fixture."""
        changes: dict[str, dict[str, float]] = {}
        memory: dict[str, tuple[float, float, tuple[float, float, float]]] = {}
        for fixture in fixtures:
            if not fixture.patch.enabled or not has_rgb(fixture):
                continue
            current_hue, current_saturation = self.hue_saturation(fixture.patch.id)
            new_hue = ((current_hue if hue is None else hue) + hue_delta) % 1.0
            new_saturation = current_saturation if saturation is None else saturation
            new_saturation = min(max(new_saturation, 0.0), 1.0)
            rgb = colorsys.hsv_to_rgb(new_hue, new_saturation, 1.0)
            rgb = tuple(min(max(c, 0.0), 1.0) for c in rgb)
            changes[fixture.patch.id] = dict(zip(RGB, rgb))
            memory[fixture.patch.id] = (new_hue, new_saturation, rgb)  # type: ignore[assignment]
        if not changes:
            return
        self._controller.apply(changes, fade_seconds=0.0)
        with self._lock:
            self._memory.update(memory)
