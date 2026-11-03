"""Reversible live-only performance modifiers, independent of stored looks and presets."""

from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .patch import ResolvedFixture
from .rings import ring_segments, segment_colour_attributes

if TYPE_CHECKING:
    from .effect_engine import EffectEngine
    from .fixture_service import FixtureController
    from .sequence_service import SequenceService


class ModifierSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    speed: Literal[0.25, 0.5, 1.0, 2.0, 4.0] = 1.0
    sequence_speed: Literal[0.25, 0.5, 1.0, 2.0, 4.0] = 1.0
    tint: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    excluded_strips: list[StrictInt] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _numeric_speed(cls, values):
        if isinstance(values, dict) and any(
            isinstance(values.get(key), bool) for key in ("speed", "sequence_speed")
        ):
            raise ValueError("Speeds must be 0.25, 0.5, 1, 2 or 4, not a boolean.")
        return values


@dataclass(frozen=True)
class Tint:
    rgb: tuple[float, float, float]
    fixtures: frozenset[str]

    def apply(self, fixture: ResolvedFixture, values: dict[str, float]) -> dict[str, float]:
        if fixture.patch.id not in self.fixtures:
            return values
        out = dict(values)
        groups = [("red", "green", "blue")] if {"red", "green", "blue"} <= values.keys() else []
        groups.extend(segment_colour_attributes(fixture, segment) for segment in ring_segments(fixture))
        for attributes in groups:
            white = out.get("white", 0.0) if attributes == ("red", "green", "blue") else 0.0
            level = max(white, *(out.get(name, 0.0) for name in attributes))
            out.update({name: component * level for name, component in zip(attributes, self.rgb)})
        if "white" in out and groups:
            out["white"] = 0.0
        return out


class LiveModifiers:
    def __init__(
        self, controller: "FixtureController", effects: "EffectEngine",
        strips: dict[int, tuple[str, list[str]]],
        sequences: "SequenceService",
    ) -> None:
        self._controller = controller
        self._effects = effects
        self._strips = strips
        self._sequences = sequences
        self._settings = ModifierSettings()
        self._lock = RLock()

    def state(self) -> dict:
        with self._lock:
            return {
                **self._settings.model_dump(),
                "choices": [{"track": track, "label": label} for track, (label, _) in self._strips.items()],
            }

    def set(self, settings: ModifierSettings) -> None:
        unknown = set(settings.excluded_strips) - self._strips.keys()
        if unknown:
            raise ValueError(f"Unknown colour strips: {', '.join(map(str, sorted(unknown)))}.")
        if len(set(settings.excluded_strips)) != len(settings.excluded_strips):
            raise ValueError("An excluded tint strip is listed twice.")
        excluded = {
            fixture for track in settings.excluded_strips for fixture in self._strips[track][1]
        }
        fixtures = frozenset(
            fixture.patch.id for fixture in self._controller.patch.fixtures
            if fixture.patch.enabled and fixture.patch.id not in excluded
            and ({"red", "green", "blue"} <= set(fixture.mode.attributes()) or ring_segments(fixture))
        )
        tint = None
        if settings.tint is not None:
            red, green, blue = (int(settings.tint[index:index + 2], 16) / 255 for index in (1, 3, 5))
            tint = Tint((red, green, blue), fixtures)
        with self._lock:
            self._sequences.set_speed(settings.sequence_speed)
            self._effects.set_speed(settings.speed)
            self._controller.set_tint(tint)
            self._settings = settings.model_copy(deep=True)
