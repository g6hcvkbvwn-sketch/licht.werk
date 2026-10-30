"""Turns MIDI events into application commands, keeps the monitor and handles learn mode.

Pure application logic: no MIDI library, so it is fully testable without a controller. The
on-screen controller sends the same events ("virtual"), so screen and hardware behave alike.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Optional

from .colour import ColourControl, has_rgb
from .fixture_service import FixtureController, FixtureDisabledError
from .midi_mapping import (
    REQUIRED_ATTRIBUTES,
    MidiAction,
    MidiBinding,
    MidiKey,
    MidiMappingStore,
    target_fixtures,
)
from .patch import ResolvedFixture
from .preview import PreviewService
from .scene_service import SceneService
from .service import LightingService

logger = logging.getLogger(__name__)

MONITOR_SIZE = 40
# One encoder detent: 1/48 of the colour circle (7.5 degrees) or 1/64 of a level.
HUE_STEP = 1 / 48
LEVEL_STEP = 1 / 64
# Continuous actions that set one fixture attribute directly.
ATTRIBUTE_ACTIONS = {"intensity": "intensity", "white": "white", "strobe": "strobe"}
# Buttons that act only while held: attribute and the value set while held.
HOLD_ACTIONS = {"flash": ("intensity", 1.0), "strobe_hold": ("strobe", 1.0)}


@dataclass(frozen=True)
class MidiEvent:
    kind: str  # "note" (button press) or "cc" (fader/pot/encoder)
    channel: int  # 1-16
    number: int
    value: int  # CC value, or note velocity (0 = release)

    @property
    def key(self) -> MidiKey:
        return MidiKey(kind=self.kind, channel=self.channel, number=self.number)  # type: ignore[arg-type]


def relative_steps(value: int) -> int:
    """Two's complement relative encoder value: 1..63 = clockwise, 65..127 = anticlockwise."""
    return value if value < 64 else value - 128


class MidiControl:
    def __init__(
        self,
        mapping: MidiMappingStore,
        controller: FixtureController,
        scenes: SceneService,
        service: LightingService,
        is_relative: Callable[[MidiKey], bool] = lambda key: False,
        preview: Optional[PreviewService] = None,
    ) -> None:
        self._mapping = mapping
        self._controller = controller
        self._scenes = scenes
        self._service = service
        self._colour = ColourControl(controller)
        self._is_relative = is_relative
        self._preview = preview
        self._lock = Lock()
        # Latest event per control, newest last, so one busy fader cannot hide the others.
        self._monitor: dict[tuple, dict] = {}
        self._learning: Optional[MidiAction] = None
        # Held flash/strobe buttons. Their values are render-time holds in the controller,
        # so they act live (also in preview) and never change the logical state.
        self._held: set[MidiKey] = set()

    def monitor(self) -> list[dict]:
        with self._lock:
            return list(reversed(self._monitor.values()))

    def learning(self) -> Optional[MidiAction]:
        with self._lock:
            return self._learning

    def start_learn(self, action: MidiAction) -> None:
        with self._lock:
            self._learning = action

    def cancel_learn(self) -> None:
        with self._lock:
            self._learning = None

    def held(self) -> set[MidiKey]:
        with self._lock:
            return set(self._held)

    def handle(self, event: MidiEvent, virtual: bool = False) -> None:
        """Process one event. Expected problems are logged; they never stop the lighting engine.

        Virtual events (on-screen controller) skip the monitor and learn mode.
        """
        learning = None
        if not virtual:
            with self._lock:
                control = (event.kind, event.channel, event.number)
                self._monitor.pop(control, None)
                self._monitor[control] = {
                    "kind": event.kind, "channel": event.channel, "number": event.number,
                    "value": event.value,
                }
                while len(self._monitor) > MONITOR_SIZE:
                    self._monitor.pop(next(iter(self._monitor)))
                learning = self._learning
        try:
            if learning is not None and self._try_learn(event, learning):
                return
            binding = self._mapping.lookup_binding(event.key)
            if binding is not None:
                self._execute(binding, event)
        except (KeyError, ValueError, FixtureDisabledError) as error:
            logger.warning("MIDI control ignored (%s %d): %s", event.kind, event.number, error)

    def _try_learn(self, event: MidiEvent, action: MidiAction) -> bool:
        if event.kind == "note" and event.value == 0:
            return False  # a release is not a press
        if (event.kind == "cc") != action.continuous:
            return False  # wrong kind of control for this action; keep waiting
        self._mapping.bind(event.key, action, relative=self._is_relative(event.key))
        self.cancel_learn()
        logger.info("MIDI learn: %s %d ch %d -> %s", event.kind, event.number, event.channel, action.type)
        return True

    # --- execution -------------------------------------------------------------------------

    def _targets(self, action: MidiAction) -> list[ResolvedFixture]:
        fixtures = target_fixtures(self._controller.patch, action.target or "")
        if not fixtures:
            raise KeyError(f"'{action.target}' is not a fixture or group")
        needed = set(REQUIRED_ATTRIBUTES[action.type])
        usable = [f for f in fixtures if f.patch.enabled and needed <= set(f.mode.attributes())]
        if not usable:
            raise FixtureDisabledError(f"No enabled fixture in '{action.target}' for {action.type}.")
        return usable

    def _execute(self, binding: MidiBinding, event: MidiEvent) -> None:
        action = binding.action
        if action.continuous:
            self._continuous(binding, event.value)
        elif action.type in HOLD_ACTIONS:
            self._hold(binding.key, action, pressed=event.value > 0)
        elif event.value > 0:  # button presses only
            if action.type == "scene":
                # Pressing the active scene's button again switches it off.
                self._scenes.recall(action.target or "")
            elif action.type == "blackout":
                self._service.set_blackout(not self._service.blackout_active())
            elif action.type == "preview":
                if self._preview is None:
                    raise ValueError("Preview mode is not available")
                self._preview.toggle()

    def _continuous(self, binding: MidiBinding, value: int) -> None:
        action = binding.action
        steps = relative_steps(value) if binding.relative else 0
        if action.type == "hue":
            fixtures = self._targets(action)
            if binding.relative:
                self._colour.set(fixtures, hue_delta=steps * HUE_STEP)
            else:
                self._colour.set(fixtures, hue=value / 127.0)
            return
        level = value / 127.0
        if binding.relative:
            current = self.value(action)
            level = min(max((current or 0.0) + steps * LEVEL_STEP, 0.0), 1.0)
        if action.type == "master":
            self._controller.set_master(level)
        elif action.type == "saturation":
            self._colour.set(self._targets(action), saturation=level)
        else:
            name = ATTRIBUTE_ACTIONS[action.type]
            changes = {f.patch.id: {name: level} for f in self._targets(action)}
            self._controller.apply(changes, fade_seconds=0.0)

    def _hold(self, key: MidiKey, action: MidiAction, pressed: bool) -> None:
        name, held_value = HOLD_ACTIONS[action.type]
        owner = f"midi:{key.kind}:{key.channel}:{key.number}"
        if pressed:
            with self._lock:
                if key in self._held:
                    return  # already held
            fixtures = self._targets(action)
            self._controller.hold(owner, {f.patch.id: {name: held_value} for f in fixtures})
            with self._lock:
                self._held.add(key)
        else:
            with self._lock:
                was_held = key in self._held
                self._held.discard(key)
            if was_held:
                self._controller.release(owner)

    # --- display ---------------------------------------------------------------------------

    def value(self, action: MidiAction) -> Optional[float]:
        """Current level (0-1) behind an action, for display; None if not applicable."""
        try:
            if action.type == "master":
                return self._controller.master()
            if action.type == "scene":
                return 1.0 if self._scenes.active_scene() == action.target else 0.0
            if action.type == "blackout":
                return 1.0 if self._service.blackout_active() else 0.0
            if action.type == "preview":
                return 1.0 if self._preview is not None and self._preview.active() else 0.0
            fixtures = self._targets(action)
            if action.type in ("hue", "saturation"):
                hue, saturation = self._colour.hue_saturation(fixtures[0].patch.id)
                return hue if action.type == "hue" else saturation
            if action.type in ATTRIBUTE_ACTIONS:
                name = ATTRIBUTE_ACTIONS[action.type]
                levels = [self._controller.state(f.patch.id)[name] for f in fixtures]
                return sum(levels) / len(levels)
        except (KeyError, FixtureDisabledError):
            return None
        return None

    def colour(self, target: str) -> Optional[tuple[float, float, float]]:
        """Live RGB (0-1) of the first RGB fixture of a target, for the on-screen swatch."""
        for fixture in target_fixtures(self._controller.patch, target):
            if fixture.patch.enabled and has_rgb(fixture):
                state = self._controller.state(fixture.patch.id)
                return state["red"], state["green"], state["blue"]
        return None
