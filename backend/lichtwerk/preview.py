"""Preview mode (project.md section 5.5): prepare changes without sending them, then Go.

While preview is on, fixture commands and scene recalls only change the preview state.
Master, blackout and held buttons (flash, strobe-hold) keep acting live. Effects started,
stopped or changed in preview wait for Go too (the tempo stays live).
Preview fades and sequences animate independently. Go promotes
their current phase and remaining timeline, without restarting, and returns to live mode.
"""

import logging
from threading import RLock
from typing import Optional

from .effect_engine import EffectEngine
from .fixture_service import FixtureController
from .scene_service import SceneService
from .position_service import PositionService

logger = logging.getLogger(__name__)


class PreviewService:
    def __init__(
        self,
        controller: FixtureController,
        scenes: SceneService,
        effects: Optional[EffectEngine] = None,
        positions: Optional[PositionService] = None,
    ) -> None:
        self._controller = controller
        self._scenes = scenes
        self._effects = effects
        self._positions = positions
        self._lock = RLock()

    def active(self) -> bool:
        return self._controller.preview_active()

    def start(self) -> None:
        with self._lock:
            if self.active():
                return
            self._controller.start_preview()
            if self._positions is not None:
                self._positions.start_preview()
            self._scenes.start_preview()
            if self._scenes.sequences is not None:
                self._scenes.sequences.start_preview()
            if self._effects is not None:
                self._effects.start_preview()
            logger.info("Preview mode on: changes are not sent until Go.")

    def go(self) -> None:
        with self._lock:
            if not self.active():
                return
            self._controller.tick()
            if self._positions is not None:
                self._positions.go()
            if self._effects is not None:
                self._effects.go()
            self._controller.go()
            self._scenes.go()
            if self._scenes.sequences is not None:
                self._scenes.sequences.go()
            logger.info("Preview Go: preview state is live.")

    def toggle(self) -> None:
        """The preview button: on in live mode, Go in preview mode."""
        with self._lock:
            if self.active():
                self.go()
            else:
                self.start()
