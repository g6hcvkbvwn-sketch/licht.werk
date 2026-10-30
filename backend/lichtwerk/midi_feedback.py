"""LED feedback for Xone:K2 buttons.

Rules: a button bound to a scene, blackout, flash or strobe-hold lights amber. The active
scene's button lights green; the blackout button lights red while blackout is on; flash and
strobe-hold buttons light red while held. The preview button lights green in live mode and amber
while preview is on (press again = Go). Unbound buttons stay dark. LED notes come from
`xone_k2.led_note` (user guide pp. 17-18). The state is compared a few times per second and only
changes are sent.
"""

import logging
import threading
from collections.abc import Callable, Iterable
from typing import Optional

from .midi_mapping import MidiBinding, MidiKey, MidiMappingStore
from .scene_service import SceneService
from .service import LightingService
from .xone_k2 import LedColour, led_note

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.25

# (channel, button note) -> colour
LedState = dict[tuple[int, int], LedColour]


def button_colour(
    binding: MidiBinding, active_scene: Optional[str], blackout: bool, held: set[MidiKey],
    preview: bool = False,
) -> Optional[LedColour]:
    """Colour of one bound button, or None if the action has no LED meaning (faders etc.)."""
    action = binding.action
    if binding.key.kind != "note":
        return None
    if action.type == "scene":
        return "green" if action.target == active_scene else "amber"
    if action.type == "blackout":
        return "red" if blackout else "amber"
    if action.type in ("flash", "strobe_hold"):
        return "red" if binding.key in held else "amber"
    if action.type == "preview":
        return "amber" if preview else "green"
    return None


def desired_leds(
    bindings: Iterable[MidiBinding],
    active_scene: Optional[str],
    blackout: bool = False,
    held: Optional[set[MidiKey]] = None,
    preview: bool = False,
) -> LedState:
    state: LedState = {}
    for binding in bindings:
        colour = button_colour(binding, active_scene, blackout, held or set(), preview)
        if colour is not None and led_note(binding.key.number, colour) is not None:
            state[(binding.key.channel, binding.key.number)] = colour
    return state


class LedFeedback:
    def __init__(
        self,
        mapping: MidiMappingStore,
        scenes: SceneService,
        service: LightingService,
        held: Callable[[], set[MidiKey]],
        send: Callable[[int, int, int], bool],
        connection_id: Callable[[], int],
        preview: Callable[[], bool] = lambda: False,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._mapping = mapping
        self._scenes = scenes
        self._service = service
        self._held = held
        self._send = send
        self._connection_id = connection_id
        self._preview = preview
        self._poll = poll_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sent: LedState = {}
        self._sent_connection = -1

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="midi-leds", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def desired(self) -> LedState:
        return desired_leds(
            self._mapping.bindings(), self._scenes.active_scene(),
            self._service.blackout_active(), self._held(), self._preview(),
        )

    def update(self) -> None:
        """Send whatever differs from what the controller last received."""
        connection = self._connection_id()
        if connection != self._sent_connection:
            self._sent = {}  # new connection: the device state is unknown
        desired = self.desired()
        # LEDs of buttons that are no longer bound must go dark.
        for key in self._sent.keys() - desired.keys():
            desired[key] = "off"
        for (channel, button), colour in desired.items():
            if self._sent.get((channel, button)) == colour:
                continue
            message = led_note(button, colour)
            if message is None:
                continue
            if not self._send(channel, *message):
                return  # no output; try again next round
            self._sent[(channel, button)] = colour
        self._sent_connection = connection

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.update()
            except Exception:
                logger.exception("Unexpected failure while updating MIDI LEDs")
            self._stop.wait(self._poll)
