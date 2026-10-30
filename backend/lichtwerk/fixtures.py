"""Reusable fixture definitions and logical-value to DMX translation.

A fixture definition describes one fixture model and its DMX modes. It knows nothing
about where a fixture is patched. Logical values are normalized floats (0.0 to 1.0);
translation into DMX bytes happens only here.
"""

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

from .dmx import DMX_SLOT_COUNT

Attribute = Literal[
    "intensity",
    "red",
    "green",
    "blue",
    "white",
    "pan",
    "tilt",
    "speed",
    "strobe",
    "ring_intensity",
    "ring_red",
    "ring_green",
    "ring_blue",
    "ring_1_red",
    "ring_1_green",
    "ring_1_blue",
    "ring_2_red",
    "ring_2_green",
    "ring_2_blue",
    "ring_3_red",
    "ring_3_green",
    "ring_3_blue",
    "ring_4_red",
    "ring_4_green",
    "ring_4_blue",
    "ring_5_red",
    "ring_5_green",
    "ring_5_blue",
    "ring_6_red",
    "ring_6_green",
    "ring_6_blue",
    "ring_strobe",
    "pump",
    "fan",
]
COLOR_ATTRIBUTES: tuple[str, ...] = ("red", "green", "blue", "white")
STROBE_ATTRIBUTES: tuple[str, ...] = ("strobe", "ring_strobe")
# Attributes scaled by the master fader. Strobe rate and position are never scaled.
INTENSITY_ATTRIBUTES: tuple[str, ...] = ("intensity", "ring_intensity")
# Attributes that move a fixture. Blackout keeps them, so heads stay where they point.
MOTION_ATTRIBUTES: tuple[str, ...] = ("pan", "tilt", "speed")
BLACKOUT_KEEP_ATTRIBUTES: tuple[str, ...] = (*MOTION_ATTRIBUTES, "pump", "fan")

ID_PATTERN = r"^[a-z0-9][a-z0-9-]*$"


class ChannelDefinition(BaseModel):
    """One DMX channel of a mode.

    Either a controllable `attribute` (a 16-bit attribute uses a coarse and a fine channel)
    or a `fixed_dmx` value that is always sent (for functions LichtWerk does not control,
    such as built-in shows).
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    attribute: Optional[Attribute] = None
    resolution: Literal["coarse", "fine"] = "coarse"
    fixed_dmx: Optional[StrictInt] = Field(default=None, ge=0, le=255)
    label: Optional[str] = Field(default=None, min_length=1)
    # Strobe only: DMX range of the fixture's native strobe. Logical 0 sends DMX 0 (off);
    # logical values above 0 map onto active_min..active_max (slow to fast).
    active_min: Optional[StrictInt] = Field(default=None, ge=1, le=255)
    active_max: Optional[StrictInt] = Field(default=None, ge=1, le=255)
    # Strobe only, optional: flash rate (Hz) at active_min and active_max, treated as linear in
    # between. Used to convert one shared strobe rate into each fixture's own range.
    min_hz: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    max_hz: Optional[float] = Field(default=None, gt=0.0, le=100.0)
    # Initial logical value (0.0-1.0) instead of 0.0.
    default: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # This channel repeats an attribute already defined earlier in the mode (for example the
    # same colour sent to every segment of an LED ring).
    mirror: bool = False
    # The fixture's DMX direction is the reverse of the logical one (e.g. a speed channel
    # that is fastest at DMX 0), so logical 1.0 sends DMX 0.
    invert: bool = False

    @model_validator(mode="after")
    def _check_kind(self) -> "ChannelDefinition":
        if (self.attribute is None) == (self.fixed_dmx is None):
            raise ValueError("A channel needs exactly one of 'attribute' or 'fixed_dmx'.")
        if self.fixed_dmx is not None:
            if self.label is None:
                raise ValueError("A fixed channel needs a 'label' saying what it is.")
            if self.default is not None or self.active_min is not None:
                raise ValueError("A fixed channel cannot have 'default' or a strobe range.")
        if self.invert and (self.attribute is None or self.attribute in STROBE_ATTRIBUTES):
            raise ValueError("'invert' applies to attribute channels other than strobe.")
        if self.mirror and (self.attribute is None or self.default is not None):
            raise ValueError("'mirror' applies to attribute channels without a default.")
        strobe = self.attribute in STROBE_ATTRIBUTES
        has_range = self.active_min is not None or self.active_max is not None
        if strobe != has_range:
            raise ValueError(
                "Strobe channels need active_min and active_max; other channels must not."
            )
        if strobe and (
            self.active_min is None
            or self.active_max is None
            or self.active_min > self.active_max
            or self.resolution != "coarse"
        ):
            raise ValueError("Invalid strobe range (active_min <= active_max, coarse only).")
        if (self.min_hz is None) != (self.max_hz is None):
            raise ValueError("Give both min_hz and max_hz, or neither.")
        if self.min_hz is not None:
            if not strobe:
                raise ValueError("min_hz/max_hz are only allowed on strobe channels.")
            if self.min_hz >= (self.max_hz or 0.0):
                raise ValueError("min_hz must be below max_hz.")
        return self


class FixtureMode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1)
    channel_count: StrictInt = Field(ge=1, le=DMX_SLOT_COUNT)
    channels: list[ChannelDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_channels(self) -> "FixtureMode":
        if len(self.channels) != self.channel_count:
            raise ValueError(
                f"Mode '{self.name}' declares {self.channel_count} channels "
                f"but lists {len(self.channels)}."
            )
        seen: list[tuple] = []
        for channel in self.channels:
            if channel.attribute is None:
                continue
            key = (channel.attribute, channel.resolution)
            if channel.mirror != (key in seen):
                raise ValueError(
                    f"Mode '{self.name}': '{channel.attribute}' is listed twice without "
                    "'mirror', or marked 'mirror' without an earlier channel."
                )
            if not channel.mirror:
                seen.append(key)
        for attribute, resolution in seen:
            if resolution == "fine" and (attribute, "coarse") not in seen:
                raise ValueError(
                    f"Mode '{self.name}': fine channel for '{attribute}' has no coarse channel."
                )
        return self

    def attributes(self) -> list[str]:
        """Logical attributes; lighting modes also expose a real or virtual intensity."""
        is_hazer = any(channel.attribute in ("pump", "fan") for channel in self.channels)
        names = [] if is_hazer else ["intensity"]
        for channel in self.channels:
            if channel.attribute is not None and channel.attribute not in names:
                names.append(channel.attribute)
        # Keep the long-standing uniform ring API available on segmented modes. The
        # controller expands writes through these aliases before scene ownership checks.
        if any(name.startswith("ring_1_") for name in names):
            names.extend(
                name for name in ("ring_red", "ring_green", "ring_blue")
                if name not in names
            )
        return names

    def defaults(self) -> dict[str, float]:
        """Initial logical values for attributes that must not start at 0.0."""
        return {
            channel.attribute: channel.default
            for channel in self.channels
            if channel.attribute is not None and channel.default is not None
        }


class MotionRange(BaseModel):
    """Mechanical range that logical pan/tilt 0.0-1.0 spans, from the manufacturer manual."""

    model_config = ConfigDict(extra="forbid", strict=True)

    pan_degrees: float = Field(gt=0, le=720)
    tilt_degrees: float = Field(gt=0, le=360)


class FixtureDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=ID_PATTERN)
    manufacturer: str = Field(min_length=1)
    model: str = Field(min_length=1)
    fixture_type: Literal["led_bar", "wash", "spot", "moving_head", "hazer", "other"]
    # Where the channel layout comes from (manual reference or user confirmation).
    source: str = Field(min_length=1)
    # Required for fixtures with pan/tilt.
    motion: Optional[MotionRange] = None
    modes: list[FixtureMode] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_modes(self) -> "FixtureDefinition":
        names = [mode.name for mode in self.modes]
        if len(set(names)) != len(names):
            raise ValueError(f"Fixture definition '{self.id}' has duplicate mode names.")
        moves = any(
            attribute in ("pan", "tilt") for mode in self.modes for attribute in mode.attributes()
        )
        if moves and self.motion is None:
            raise ValueError(
                f"Fixture definition '{self.id}' has pan/tilt channels but no 'motion' ranges."
            )
        return self

    def mode(self, name: str) -> FixtureMode:
        for mode in self.modes:
            if mode.name == name:
                return mode
        raise KeyError(f"Fixture definition '{self.id}' has no mode '{name}'.")


def to_8bit(value: float) -> int:
    """Map 0.0-1.0 to 0-255, clamping out-of-range input."""
    clamped = min(max(float(value), 0.0), 1.0)
    return int(clamped * 255 + 0.5)


def to_16bit(value: float) -> tuple[int, int]:
    """Map 0.0-1.0 to a (coarse, fine) pair of a 16-bit DMX value."""
    clamped = min(max(float(value), 0.0), 1.0)
    combined = int(clamped * 65535 + 0.5)
    return combined >> 8, combined & 0xFF


def render_mode(mode: FixtureMode, state: dict[str, float]) -> list[int]:
    """Translate logical state into one DMX value per channel of the mode.

    Missing attributes count as 0.0. If the mode has no intensity channel, intensity
    scales the color channels (virtual dimmer).
    """
    state = dict(state)
    attributes = {channel.attribute for channel in mode.channels}
    for colour in ("red", "green", "blue"):
        legacy = f"ring_{colour}"
        if legacy not in state:
            continue
        for index in range(1, 7):
            name = f"ring_{index}_{colour}"
            if name in attributes:
                state.setdefault(name, state[legacy])
    has_dimmer = any(channel.attribute == "intensity" for channel in mode.channels)
    sixteen_bit = {c.attribute for c in mode.channels if c.resolution == "fine"}
    intensity = min(max(float(state.get("intensity", 0.0)), 0.0), 1.0)
    values: list[int] = []
    for channel in mode.channels:
        if channel.attribute is None:
            values.append(int(channel.fixed_dmx or 0))
            continue
        level = float(state.get(channel.attribute, 0.0))
        if channel.attribute in STROBE_ATTRIBUTES:
            level = min(max(level, 0.0), 1.0)
            if level == 0.0:
                values.append(0)
            else:
                low, high = int(channel.active_min or 1), int(channel.active_max or 255)
                values.append(int(low + level * (high - low) + 0.5))
            continue
        if not has_dimmer and channel.attribute in COLOR_ATTRIBUTES:
            level = min(max(level, 0.0), 1.0) * intensity
        if channel.invert:
            level = 1.0 - min(max(level, 0.0), 1.0)
        if channel.attribute in sixteen_bit:
            coarse, fine = to_16bit(level)
            values.append(coarse if channel.resolution == "coarse" else fine)
        else:
            values.append(to_8bit(level))
    return values


def load_fixture_library(directory: Path) -> dict[str, FixtureDefinition]:
    """Load every *.json fixture definition in a directory, reporting file-specific errors."""
    if not directory.is_dir():
        raise ValueError(f"Fixture definition folder not found: {directory}")
    library: dict[str, FixtureDefinition] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            definition = FixtureDefinition.model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid JSON in fixture definition {path} at line {error.lineno}: {error.msg}"
            ) from error
        except ValidationError as error:
            raise ValueError(f"Invalid fixture definition {path}: {error}") from error
        if definition.id in library:
            raise ValueError(f"Duplicate fixture definition id '{definition.id}' in {path}.")
        library[definition.id] = definition
    return library
