"""AKAI APC40 MKII: protocol constants, pad layout, LED colour palette and device setup.

Source: AKAI "APC40 Mk2 Communication Protocol" v1.2 (January 2015). MIDI channels here are
1-based like `MidiEvent` (the document counts from 0, so its "channel 0 = track 1" is 1 here).

Setup: the unit starts in Generic mode (0x40) after every power-up. The host switches it with
F0 47 7F 29 60 00 04 <mode> <version high> <version low> <bugfix> F7 (pages 8-11). In Alternate
Ableton Live mode (0x42) all buttons are momentary, knobs are not banked and the host controls every
LED; the device answers with its fader positions. The APC40 is USB powered, so every reconnect is a
power-up and the message is sent on every connect.

No lighting logic lives here (see apc40_control.py).
"""

import colorsys
from typing import Optional

from . import __version__

# Port name as seen on the Mac ("APC40 mkII"); matched case-insensitively.
PORT_MATCH = "apc40 mkii"

GENERIC_MODE = 0x40
ABLETON_LIVE_MODE = 0x41
ALTERNATE_MODE = 0x42

TRACKS = 8

# Notes whose MIDI channel is the track (1-8).
NOTE_RECORD_ARM = 0x30
NOTE_SOLO = 0x31
NOTE_ACTIVATOR = 0x32
NOTE_TRACK_SELECT = 0x33
NOTE_CLIP_STOP = 0x34
# Notes that ignore the channel.
NOTE_MASTER = 0x50
NOTE_STOP_ALL_CLIPS = 0x51
NOTE_METRONOME = 0x5A
NOTE_SHIFT = 0x62
NOTE_CROSSFADER_AB = 0x42
NOTE_SCENE_LAUNCH_FIRST = 0x52  # SCENE LAUNCH 1-5: 0x52-0x56, RGB LEDs like the pads
SCENE_LAUNCH_COUNT = 5
NOTE_TAP_TEMPO = 0x63  # no LED
NOTE_SESSION_RECORD = 0x66
NOTE_PLAY = 0x5B
NOTE_RECORD = 0x5D
NOTE_UP = 0x5E
NOTE_DOWN = 0x5F
NOTE_RIGHT = 0x60
NOTE_LEFT = 0x61
PAD_COUNT = 40  # clip launch pads: notes 0x00-0x27

# Controllers.
CC_TRACK_FADER = 0x07  # channel = track
CC_MASTER_FADER = 0x0E
CC_TEMPO = 0x0D  # TEMPO knob, relative
CC_DEVICE_KNOB_FIRST = 0x10  # device knobs 1-8: CC 16-23
CC_DEVICE_RING_TYPE_FIRST = 0x18  # ring type of device knobs 1-8: CC 24-31
CC_CUE_LEVEL = 0x2F  # relative
CC_TRACK_KNOB_FIRST = 0x30  # track knobs 1-8: CC 48-55
CC_TRACK_RING_TYPE_FIRST = 0x38  # ring type of track knobs 1-8: CC 56-63

# LED ring types (page 26).
RING_OFF = 0
RING_SINGLE = 1
RING_VOLUME = 2
RING_PAN = 3

# Single-colour button LEDs: 0 off, 1-127 on. Clip stop: 2 = blink (tempo-synced, not used).
LED_OFF = 0
LED_ON = 1

PAD_ROWS = 5
PAD_COLUMNS = 8
# Which pad row sends notes 0-7 is not stated in the document. Assumption (Ableton's own APC40
# MKII layout): the BOTTOM row sends 0-7, the top row 32-39. Verify on the hardware.
BOTTOM_ROW_FIRST = True

# RGB pad palette (velocity -> colour), pages 18-22. Velocity on MIDI channel 1 sets the colour.
PALETTE: tuple[str, ...] = (
    "000000", "1E1E1E", "7F7F7F", "FFFFFF", "FF4C4C", "FF0000", "590000", "190000",
    "FFBD6C", "FF5400", "591D00", "271B00", "FFFF4C", "FFFF00", "595900", "191900",
    "88FF4C", "54FF00", "1D5900", "142B00", "4CFF4C", "00FF00", "005900", "001900",
    "4CFF5E", "00FF19", "00590D", "001902", "4CFF88", "00FF55", "00591D", "001F12",
    "4CFFB7", "00FF99", "005935", "001912", "4CC3FF", "00A9FF", "004152", "001019",
    "4C88FF", "0055FF", "001D59", "000819", "4C4CFF", "0000FF", "000059", "000019",
    "874CFF", "5400FF", "190064", "0F0030", "FF4CFF", "FF00FF", "590059", "190019",
    "FF4C87", "FF0054", "59001D", "220013", "FF1500", "993500", "795100", "436400",
    "033900", "005735", "00547F", "0000FF", "00454F", "2500CC", "7F7F7F", "202020",
    "FF0000", "BDFF2D", "AFED06", "64FF09", "108B00", "00FF87", "00A9FF", "002AFF",
    "3F00FF", "7A00FF", "B21A7D", "402100", "FF4A00", "88E106", "72FF15", "00FF00",
    "3BFF26", "59FF71", "38FFCC", "5B8AFF", "3151C6", "877FE9", "D31DFF", "FF005D",
    "FF7F00", "B9B000", "90FF00", "835D07", "392B00", "144C10", "0D5038", "15152A",
    "16205A", "693C1C", "A8000A", "DE513D", "D86A1C", "FFE126", "9EE12F", "67B50F",
    "1E1E30", "DCFF6B", "80FFBD", "9A99FF", "8E66FF", "404040", "757575", "E0FFFF",
    "A00000", "350000", "1AD000", "074200", "B9B000", "3F3100", "B35F00", "4B1502",
)

# Colours the owner can give a scene pad: name -> palette velocity.
SCENE_PAD_COLOURS: dict[str, int] = {
    "white": 3, "red": 5, "orange": 9, "yellow": 13, "lime": 17, "green": 21, "mint": 33,
    "cyan": 37, "blue": 45, "purple": 49, "magenta": 53, "pink": 57,
}
DEFAULT_SCENE_PAD_COLOUR = "white"

# Effect types on SCENE LAUNCH 1-5 and the effects page: (bright, dim) palette velocities.
EFFECT_COLOURS: dict[str, tuple[int, int]] = {
    "chase": (13, 14),  # yellow
    "rainbow": (53, 54),  # magenta
    "two_colour": (37, 38),  # cyan
    "circle": (21, 22),  # green
    "figure8": (45, 46),  # blue
}

# Colour pads (a channel is selected): 8 hue columns x 5 rows from saturated (top) to pastel.
COLOUR_COLUMNS: tuple[tuple[str, float], ...] = (
    ("red", 0.0), ("orange", 30 / 360), ("yellow", 60 / 360), ("green", 120 / 360),
    ("cyan", 180 / 360), ("blue", 240 / 360), ("purple", 275 / 360), ("magenta", 300 / 360),
)
COLOUR_ROW_SATURATIONS: tuple[float, ...] = (1.0, 0.75, 0.5, 0.3, 0.15)
# The most pastel magenta pad is replaced by "white" (uses the W LED where the fixture has one).
WHITE_PAD = PAD_COUNT - 1


def _version_bytes() -> list[int]:
    parts = [int(p) if p.isdigit() else 0 for p in __version__.split(".")[:3]]
    parts += [0] * (3 - len(parts))
    return [min(p, 0x7F) for p in parts]


def introduction(mode: int = ALTERNATE_MODE) -> list[int]:
    """SysEx data bytes (without F0/F7) of the introduction message."""
    if mode not in (GENERIC_MODE, ABLETON_LIVE_MODE, ALTERNATE_MODE):
        raise ValueError(f"Unknown APC40 mode 0x{mode:02X}.")
    return [0x47, 0x7F, 0x29, 0x60, 0x00, 0x04, mode, *_version_bytes()]


def pad_note(pad: int) -> int:
    """MIDI note of a pad. Pads are numbered 0-39 row by row from the TOP-left, as on screen."""
    if not 0 <= pad < PAD_COUNT:
        raise ValueError(f"Pad {pad} does not exist (0-{PAD_COUNT - 1}).")
    row, column = divmod(pad, PAD_COLUMNS)
    if BOTTOM_ROW_FIRST:
        row = PAD_ROWS - 1 - row
    return row * PAD_COLUMNS + column


def note_pad(note: int) -> Optional[int]:
    """Pad number (0-39, from the top-left) of a clip launch note, or None."""
    if not 0 <= note < PAD_COUNT:
        return None
    row, column = divmod(note, PAD_COLUMNS)
    if BOTTOM_ROW_FIRST:
        row = PAD_ROWS - 1 - row
    return row * PAD_COLUMNS + column


def colour_pad(pad: int) -> Optional[tuple[float, float]]:
    """(hue, saturation) of a colour pad; None for the white pad."""
    if pad == WHITE_PAD:
        return None
    row, column = divmod(pad, PAD_COLUMNS)
    return COLOUR_COLUMNS[column][1], COLOUR_ROW_SATURATIONS[row]


def hex_rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[0:2], 16), int(colour[2:4], 16), int(colour[4:6], 16)


def nearest_palette(red: float, green: float, blue: float) -> int:
    """Palette velocity closest to an RGB colour (components 0-1). Black is never chosen."""
    target = (red * 255, green * 255, blue * 255)
    best, best_distance = 3, float("inf")
    for velocity, colour in enumerate(PALETTE):
        if velocity == 0:
            continue
        distance = sum((a - b) ** 2 for a, b in zip(target, hex_rgb(colour)))
        if distance < best_distance:
            best, best_distance = velocity, distance
    return best


def colour_pad_velocity(pad: int) -> int:
    hue_saturation = colour_pad(pad)
    if hue_saturation is None:
        return SCENE_PAD_COLOURS["white"]
    hue, saturation = hue_saturation
    # Pad LEDs cannot show pastel tones (they all look white), so the pad shows a compressed
    # saturation that keeps the rows apart. The light itself gets the real saturation.
    return nearest_palette(*colorsys.hsv_to_rgb(hue, 0.4 + 0.6 * saturation, 1.0))
