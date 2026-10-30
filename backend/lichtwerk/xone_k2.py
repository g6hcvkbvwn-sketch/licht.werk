"""Allen & Heath Xone:K2 surface layout, MIDI numbers and LED notes.

All numbers are from the Xone:K2 user guide (Latching Layers OFF): pp. 14-16 ("MIDI
implementation send") and pp. 17-18 ("MIDI implementation return"); encoders send two's
complement relative values (p. 9). The MIDI channel is set on the unit (default 15, p. 11).
Only this module (plus the LED feedback that uses it) knows Xone:K2 specifics.
"""

from dataclasses import dataclass
from typing import Literal, Optional

ControlType = Literal["encoder", "pot", "fader", "button"]
LedColour = Literal["off", "red", "amber", "green"]


@dataclass(frozen=True)
class SurfaceControl:
    id: str
    type: ControlType
    kind: Literal["note", "cc"]
    number: int
    area: Literal["strip", "matrix", "bottom"]
    row: str
    column: int  # 1-4 (bottom row: 1-4 from left to right)
    label: str = ""

    @property
    def relative(self) -> bool:
        return self.type == "encoder"


def _build() -> tuple[SurfaceControl, ...]:
    controls: list[SurfaceControl] = []
    for col in range(1, 5):
        i = col - 1
        controls += [
            SurfaceControl(f"encoder-{col}", "encoder", "cc", i, "strip", "encoder", col),
            SurfaceControl(f"encoder-{col}-push", "button", "note", 52 + i, "strip", "encoder-push", col),
            SurfaceControl(f"pot-1-{col}", "pot", "cc", 4 + i, "strip", "pot-1", col),
            SurfaceControl(f"switch-1-{col}", "button", "note", 48 + i, "strip", "switch-1", col),
            SurfaceControl(f"pot-2-{col}", "pot", "cc", 8 + i, "strip", "pot-2", col),
            SurfaceControl(f"switch-2-{col}", "button", "note", 44 + i, "strip", "switch-2", col),
            SurfaceControl(f"pot-3-{col}", "pot", "cc", 12 + i, "strip", "pot-3", col),
            SurfaceControl(f"switch-3-{col}", "button", "note", 40 + i, "strip", "switch-3", col),
            SurfaceControl(f"fader-{col}", "fader", "cc", 16 + i, "strip", "fader", col),
        ]
    # Switch matrix A-P, top row first (A-D = C2-D#2, E-H = G#1-B1, I-L = E1-G1, M-P = C1-D#1).
    for row, first in enumerate((36, 32, 28, 24)):
        for i in range(4):
            letter = "ABCDEFGHIJKLMNOP"[row * 4 + i]
            controls.append(
                SurfaceControl(f"matrix-{letter}", "button", "note", first + i, "matrix",
                               f"matrix-{row + 1}", i + 1, letter)
            )
    controls += [
        SurfaceControl("layer", "button", "note", 12, "bottom", "bottom", 1, "LAYER"),
        SurfaceControl("encoder-left", "encoder", "cc", 20, "bottom", "bottom", 2),
        SurfaceControl("encoder-left-push", "button", "note", 13, "bottom", "bottom-push", 2),
        SurfaceControl("encoder-right", "encoder", "cc", 21, "bottom", "bottom", 3),
        SurfaceControl("encoder-right-push", "button", "note", 14, "bottom", "bottom-push", 3),
        SurfaceControl("exit-setup", "button", "note", 15, "bottom", "bottom", 4, "EXIT SETUP"),
    ]
    return tuple(controls)


CONTROLS: tuple[SurfaceControl, ...] = _build()
DEFAULT_CHANNEL = 15
RELATIVE_CCS = frozenset(c.number for c in CONTROLS if c.relative)


def is_relative_cc(number: int) -> bool:
    return number in RELATIVE_CCS


def led_note(button_note: int, colour: LedColour) -> Optional[tuple[int, int]]:
    """(note, velocity) that sets a button's LED, or None if the button has no LED.

    Each button has one LED: a note-on selects its colour and a velocity-0 message switches it
    off. So only the wanted colour is ever sent (an "off" for another colour would switch the
    LED off again, as observed on the real unit).
    """
    if 24 <= button_note <= 55:  # matrix, pot switches, encoder push windows
        amber, green = button_note + 36, button_note + 72
    elif button_note in (12, 15):  # LAYER and EXIT SETUP
        amber, green = button_note + 4, button_note + 8
    else:
        return None
    if colour == "off":
        return button_note, 0
    return {"red": button_note, "amber": amber, "green": green}[colour], 127
