"""Effect definitions and deterministic logical-state modulation."""

import colorsys
import math
import re
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .fixtures import MotionRange
from .patch import ResolvedFixture
from .rings import ring_segments

Values = dict[str, float]
RGB = ("red", "green", "blue")
EffectComponent = Literal["beam", "ring"]
_HEX_RGB = re.compile(r"^#[0-9a-fA-F]{6}$")


class EffectType(str, Enum):
    CHASE = "chase"
    RAINBOW = "rainbow"
    TWO_COLOUR = "two_colour"
    CIRCLE = "circle"
    FIGURE8 = "figure8"


# The controller pages are family keys, while definitions retain legacy effect IDs.
EFFECT_TYPES: tuple[EffectType, ...] = (
    EffectType.CHASE,
    EffectType.RAINBOW,
    EffectType.CIRCLE,
)
EFFECT_LABELS = {
    EffectType.CHASE: "Dimmer",
    EffectType.RAINBOW: "Colour",
    EffectType.CIRCLE: "Move",
}
COLOUR_TYPES = frozenset({EffectType.RAINBOW, EffectType.TWO_COLOUR})
MOVEMENT_TYPES = frozenset({EffectType.CIRCLE, EffectType.FIGURE8})


def family(kind: EffectType) -> EffectType:
    """Return the canonical page/family for a legacy effect kind."""
    if kind in COLOUR_TYPES:
        return EffectType.RAINBOW
    if kind in MOVEMENT_TYPES:
        return EffectType.CIRCLE
    return EffectType.CHASE


BEAT_CHOICES: tuple[float, ...] = (32.0, 16.0, 8.0, 4.0, 2.0, 1.0, 0.5, 0.25)
MOVE_RADIUS_DEGREES = 30.0
Shape = Literal["legacy", "sine", "triangle", "square", "saw_up", "saw_down", "one_by_one"]


class EffectParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    beats: float = Field(gt=0, le=64, allow_inf_nan=False)
    size: float = Field(ge=0, le=1, allow_inf_nan=False)
    spread: float = Field(ge=0, le=1, allow_inf_nan=False)
    softness: float = Field(ge=0, le=1, allow_inf_nan=False)
    timing: Literal["beats", "seconds"] = "beats"
    seconds: float = Field(default=4.0, ge=0.25, le=120.0, allow_inf_nan=False)
    shape: Shape = "legacy"
    maximum: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    selection: Literal["all", "odd", "even", "alternating"] = "all"
    order: list[str] = Field(default_factory=list)
    reverse: bool = False
    mirror: Literal["none", "x", "y", "xy"] = "none"
    mirror_alternate: bool = True
    colour_mode: Literal["base", "palette"] = "base"
    palette: list[str] = Field(default_factory=lambda: ["#0000ff", "#ff00ff"])
    transition: Literal["step", "smooth"] = "smooth"
    target_ids: list[str] = Field(default_factory=list)
    travel: float = Field(default=4.0, gt=0, le=120.0, allow_inf_nan=False)
    hold: float = Field(default=4.0, gt=0, le=120.0, allow_inf_nan=False)
    pattern: Literal["current", "none", "circle", "figure8"] = "current"

    @model_validator(mode="after")
    def _validate_lists(self) -> "EffectParams":
        if len(set(self.order)) != len(self.order):
            raise ValueError("Fixture order contains a duplicate id.")
        if len(set(self.target_ids)) != len(self.target_ids):
            raise ValueError("Target list contains a duplicate id.")
        if not 2 <= len(self.palette) <= 8:
            raise ValueError("A fixed palette must contain 2 to 8 RGB colours.")
        if any(not isinstance(colour, str) or not _HEX_RGB.fullmatch(colour) for colour in self.palette):
            raise ValueError("Palette colours must be six-digit hexadecimal RGB values such as #ff00aa.")
        return self


class EffectPreset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1)
    params: EffectParams


class FxDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: str = Field(min_length=1)
    kind: EffectType = Field(strict=False)
    params: EffectParams


def _preset(name: str, beats: float, size: float, spread: float, softness: float) -> EffectPreset:
    return EffectPreset(
        name=name, params=EffectParams(beats=beats, size=size, spread=spread, softness=softness)
    )


# Factory contents retain all original definitions and their exact legacy parameters.
PRESETS: dict[EffectType, tuple[EffectPreset, ...]] = {
    EffectType.CHASE: (
        _preset("Pulse", 1, 1.0, 0.0, 1.0),
        _preset("Chase", 1, 1.0, 1.0, 0.0),
        _preset("Wave", 4, 0.6, 1.0, 1.0),
        _preset("Fast chase", 0.5, 1.0, 1.0, 0.0),
        _preset("Breathe", 8, 0.5, 0.0, 1.0),
    ),
    EffectType.RAINBOW: (
        _preset("Slow rainbow", 16, 1.0, 0.0, 0.0),
        _preset("Rainbow spread", 8, 1.0, 1.0, 0.0),
        _preset("Fast rainbow", 2, 1.0, 0.5, 0.0),
        _preset("Drift", 8, 0.25, 0.0, 0.0),
        _preset("Wobble", 4, 0.1, 0.5, 0.0),
    ),
    EffectType.TWO_COLOUR: (
        _preset("Opposite", 2, 1.0, 0.0, 0.0),
        _preset("Alternate", 2, 1.0, 1.0, 0.0),
        _preset("Fade", 4, 1.0, 0.0, 1.0),
        _preset("Neighbour", 1, 0.33, 0.0, 0.0),
        _preset("Slow cross", 8, 1.0, 1.0, 1.0),
    ),
    EffectType.CIRCLE: (
        _preset("Small", 4, 0.3, 0.0, 0.0),
        _preset("Medium", 4, 0.6, 0.0, 0.0),
        _preset("Big slow", 8, 1.0, 0.0, 0.0),
        _preset("Mirror", 4, 0.6, 0.5, 0.0),
        _preset("Fast small", 1, 0.2, 0.0, 0.0),
    ),
    EffectType.FIGURE8: (
        _preset("Small", 4, 0.3, 0.0, 0.0),
        _preset("Medium", 4, 0.6, 0.0, 0.0),
        _preset("Big slow", 8, 1.0, 0.0, 0.0),
        _preset("Mirror", 4, 0.6, 0.5, 0.0),
        _preset("Fast small", 2, 0.2, 0.0, 0.0),
    ),
}
PRESET_COUNT = 5
PARAM_LABELS = {"speed": "Speed", "size": "Size", "spread": "Spread", "softness": "Softness"}


def factory_choices() -> list[FxDefinition]:
    categories = {
        EffectType.CHASE: "Dimmer",
        EffectType.RAINBOW: "Colour",
        EffectType.TWO_COLOUR: "Colour",
        EffectType.CIRCLE: "Move",
        EffectType.FIGURE8: "Move",
    }
    kind_labels = {
        EffectType.CHASE: "Chase",
        EffectType.RAINBOW: "Rainbow",
        EffectType.TWO_COLOUR: "Two colours",
        EffectType.CIRCLE: "Circle",
        EffectType.FIGURE8: "Figure 8",
    }
    return [
        FxDefinition(
            name=f"{categories[kind]} · {kind_labels[kind]} · {preset.name}", kind=kind,
            params=preset.params.model_copy(deep=True),
        )
        for kind, presets in PRESETS.items()
        for preset in presets
    ]


def family_presets(page: EffectType) -> tuple[FxDefinition, ...]:
    selected = {
        EffectType.CHASE: [(EffectType.CHASE, i) for i in range(5)],
        EffectType.RAINBOW: [
            (EffectType.RAINBOW, 0), (EffectType.RAINBOW, 1), (EffectType.TWO_COLOUR, 0),
            (EffectType.TWO_COLOUR, 2), (EffectType.TWO_COLOUR, 3),
        ],
        EffectType.CIRCLE: [
            (EffectType.CIRCLE, 0), (EffectType.CIRCLE, 1), (EffectType.FIGURE8, 1),
            (EffectType.CIRCLE, 3), (EffectType.FIGURE8, 2),
        ],
    }
    page = family(page)
    names = {
        EffectType.CIRCLE: (
            "Small circle", "Medium circle", "Medium figure 8",
            "Mirror circle", "Big slow figure 8",
        ),
    }
    return tuple(
        FxDefinition(
            name=(names[page][slot] if page in names else PRESETS[kind][index].name),
            kind=kind,
            params=PRESETS[kind][index].params.model_copy(deep=True),
        )
        for slot, (kind, index) in enumerate(selected[page])
    )


def knob_params(effect_type: EffectType) -> dict[int, str]:
    knobs = {1: "speed", 2: "size", 5: "spread"}
    if effect_type in (EffectType.CHASE, EffectType.TWO_COLOUR):
        knobs[6] = "softness"
    return knobs


def speed_level(beats: float) -> float:
    index = min(range(len(BEAT_CHOICES)), key=lambda i: abs(BEAT_CHOICES[i] - beats))
    return index / (len(BEAT_CHOICES) - 1)


def beats_from_level(level: float) -> float:
    return BEAT_CHOICES[round(min(max(level, 0.0), 1.0) * (len(BEAT_CHOICES) - 1))]


def seconds_from_level(level: float) -> float:
    return 120.0 * (0.25 / 120.0) ** min(max(level, 0.0), 1.0)


def seconds_level(seconds: float) -> float:
    return math.log(120.0 / min(max(seconds, 0.25), 120.0)) / math.log(120.0 / 0.25)


def fits(
    effect_type: EffectType, fixture: ResolvedFixture, component: EffectComponent = "beam"
) -> bool:
    attributes = set(fixture.mode.attributes())
    if component == "ring":
        return effect_type not in MOVEMENT_TYPES and bool(ring_segments(fixture))
    if component != "beam":
        raise ValueError("Effect component must be 'beam' or 'ring'.")
    if effect_type in MOVEMENT_TYPES:
        return {"pan", "tilt"} <= attributes and fixture.definition.motion is not None
    if effect_type in COLOUR_TYPES:
        return set(RGB) <= attributes
    return "intensity" in attributes


def fixture_phase(beats: float, params: EffectParams, index: int, count: int) -> float:
    offset = params.spread * index / count if count > 0 else 0.0
    return (beats / params.beats - offset) % 1.0


def waveform(phase: float, softness: float) -> float:
    hard = 1.0 if phase < 0.5 else 0.0
    soft = (math.cos(2 * math.pi * phase) + 1.0) / 2.0
    return softness * soft + (1.0 - softness) * hard


def shape_value(phase: float, shape: Shape, softness: float = 0.0) -> float:
    phase %= 1.0
    if shape == "legacy":
        return waveform(phase, softness)
    if shape == "sine":
        return (math.cos(2.0 * math.pi * phase) + 1.0) / 2.0
    if shape == "triangle":
        return abs(1.0 - 2.0 * phase)
    if shape == "square":
        return 1.0 if phase < 0.5 else 0.0
    if shape == "saw_up":
        return phase
    if shape == "saw_down":
        return 1.0 - phase
    return 1.0 if phase < 0.5 else 0.0


def selected(index: int, params: EffectParams) -> bool:
    if params.selection == "all":
        return True
    if params.selection == "odd":
        return index % 2 == 0
    if params.selection == "even":
        return index % 2 == 1
    return True


def _base_colour(state: Values) -> tuple[float, float, float]:
    hue, saturation, value = colorsys.rgb_to_hsv(*(state[name] for name in RGB))
    return hue, (saturation if saturation > 0.0 else 1.0), (value if value > 0.0 else 1.0)


def _set_rgb(state: Values, rgb: tuple[float, float, float]) -> None:
    for name, component in zip(RGB, rgb):
        state[name] = min(max(component, 0.0), 1.0)


def _palette_rgb(hex_colour: str) -> tuple[float, float, float]:
    return tuple(int(hex_colour[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def _palette_colour(params: EffectParams, phase: float) -> tuple[float, float, float]:
    palette = [_palette_rgb(value) for value in params.palette]
    position = (phase % 1.0) * len(palette)
    first = math.floor(position)
    fraction = position - first
    a = palette[first % len(palette)]
    b = palette[(first + 1) % len(palette)]
    if params.transition == "step":
        return a
    return tuple(x + (y - x) * fraction for x, y in zip(a, b))


def movement_offset(effect_type: EffectType, phase: float) -> tuple[float, float]:
    angle = 2.0 * math.pi * phase
    if effect_type is EffectType.CIRCLE:
        return math.cos(angle), math.sin(angle)
    return math.sin(angle), 0.5 * math.sin(2.0 * angle)


def modulate(
    effect_type: EffectType,
    params: EffectParams,
    phase: float,
    state: Values,
    motion: Optional[MotionRange] = None,
    *,
    index: int = 0,
    count: int = 1,
) -> Values:
    """Return a copy of state modulated by one effect without mutating its base."""
    out = dict(state)
    phase %= 1.0
    if not selected(index, params):
        return out
    if params.selection == "alternating":
        phase = (phase + 0.5 * (index % 2)) % 1.0
    if effect_type is EffectType.CHASE:
        if "intensity" in out:
            if params.shape == "one_by_one":
                normalized = 1.0 if phase < 1.0 / max(count, 1) else 0.0
            else:
                normalized = shape_value(phase, params.shape, params.softness)
            minimum = params.maximum * (1.0 - params.size)
            out["intensity"] *= minimum + (params.maximum - minimum) * normalized
    elif effect_type in COLOUR_TYPES:
        if set(RGB) <= out.keys():
            if params.colour_mode == "palette":
                _set_rgb(out, _palette_colour(params, phase))
                if "white" in out:
                    out["white"] = 0.0
            elif effect_type is EffectType.RAINBOW:
                hue, saturation, value = _base_colour(out)
                if params.size >= 1.0:
                    shift = phase
                else:
                    shift = params.size * ((1.0 - abs(2.0 * phase - 1.0)) - 0.5)
                _set_rgb(out, colorsys.hsv_to_rgb((hue + shift) % 1.0, saturation, value))
            else:
                hue, saturation, value = _base_colour(out)
                first = colorsys.hsv_to_rgb(hue, saturation, value)
                second = colorsys.hsv_to_rgb((hue + params.size * 0.5) % 1.0, saturation, value)
                weight = waveform(phase, params.softness)
                _set_rgb(out, tuple(weight * a + (1.0 - weight) * b for a, b in zip(first, second)))
    elif effect_type in MOVEMENT_TYPES and motion is not None and {"pan", "tilt"} <= out.keys():
        if "speed" in out:
            out["speed"] = 1.0
        dx, dy = movement_offset(effect_type, phase)
        if params.mirror in ("x", "xy"):
            dx = -dx
        if params.mirror in ("y", "xy"):
            dy = -dy
        if params.mirror != "none" and params.mirror_alternate and index % 2:
            dx = -dx
        radius = params.size * MOVE_RADIUS_DEGREES
        out["pan"] = min(max(out["pan"] + radius * dx / motion.pan_degrees, 0.0), 1.0)
        out["tilt"] = min(max(out["tilt"] + radius * dy / motion.tilt_degrees, 0.0), 1.0)
    return out
