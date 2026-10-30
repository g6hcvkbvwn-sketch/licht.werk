"""2D room map (project.md section 7): where each fixture sits, seen from above.

Positions are metres in room coordinates (project.md 5.4: x to the DJ's right, y away from the
booth). They are only for the on-screen map and are placed by the user; nothing is guessed.
Stored in config/map.json. A fixture without a position is shown in a tray under the room.
"""

import json
from pathlib import Path
from threading import Lock
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .patch import ResolvedFixture
from .rings import ring_segments, segment_colour_attributes
from .scenes import PersistenceError, write_json_atomic

# Drawing size when the room is not measured yet (shown as a placeholder, never used for aiming).
PLACEHOLDER_ROOM = (5.0, 4.0)

RGB = tuple[float, float, float]


class MapPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    x: float = Field(ge=0, allow_inf_nan=False)
    y: float = Field(ge=0, allow_inf_nan=False)


class MapFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # Keyed by fixture id.
    fixtures: dict[str, MapPosition] = Field(default_factory=dict)


class MapStore:
    """Map positions in memory, saved atomically when `path` is set (None = memory only)."""

    def __init__(self, path: Optional[Path] = None, document: Optional[MapFile] = None) -> None:
        self._path = path
        self._lock = Lock()
        self._positions = dict((document or MapFile()).fixtures)

    @classmethod
    def load(cls, path: Path) -> "MapStore":
        if not path.exists():
            return cls(path)
        try:
            document = MapFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValidationError) as error:
            raise ValueError(
                f"Invalid map file {path}: {error}. A previous version may exist in {path.name}.bak."
            ) from error
        return cls(path, document)

    def get(self, fixture_id: str) -> Optional[MapPosition]:
        with self._lock:
            return self._positions.get(fixture_id)

    def all(self) -> dict[str, MapPosition]:
        with self._lock:
            return dict(self._positions)

    def place(self, fixture_id: str, position: MapPosition) -> None:
        with self._lock:
            self._save({**self._positions, fixture_id: position})

    def remove(self, fixture_id: str) -> None:
        with self._lock:
            self._save({k: v for k, v in self._positions.items() if k != fixture_id})

    def _save(self, positions: dict[str, MapPosition]) -> None:
        """Write first, then update memory, so a failed write changes nothing."""
        if self._path is not None:
            data = MapFile(fixtures=dict(sorted(positions.items()))).model_dump(mode="json")
            try:
                write_json_atomic(self._path, data)
            except OSError as error:
                raise PersistenceError(f"Could not save {self._path}: {error}") from error
        self._positions = positions


def light_colour(values: dict[str, float], master: float = 1.0) -> Optional[RGB]:
    """Approximate emitted colour (0-1 RGB) of a fixture for the map; None if it has no colour.

    White adds to all three channels; the result is scaled by intensity (when the fixture has a
    dimmer) and master. This is a display hint, not a colour-accurate model.
    """
    if not {"red", "green", "blue"} <= values.keys():
        level = values.get("intensity")
        return None if level is None else (level * master,) * 3  # type: ignore[return-value]
    white = values.get("white", 0.0)
    scale = values.get("intensity", 1.0) * master
    return tuple(min(values[c] + white, 1.0) * scale for c in ("red", "green", "blue"))  # type: ignore[return-value]


def ring_colours(
    fixture: ResolvedFixture, values: dict[str, float], master: float = 1.0,
) -> list[RGB]:
    """Approximate each section's emitted RGB from rendered ring values, not beam intensity."""
    scale = values.get("ring_intensity", 1.0) * master if fixture.patch.enabled else 0.0
    colours = []
    for section in ring_segments(fixture):
        red, green, blue = segment_colour_attributes(fixture, section)
        colours.append((values[red] * scale, values[green] * scale, values[blue] * scale))
    return colours
