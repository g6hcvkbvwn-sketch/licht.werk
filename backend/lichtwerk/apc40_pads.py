"""Scene assignments of the APC40 clip pads, stored in config/apc40.json.

Pads are numbered 0-39 row by row from the top-left (as on screen). Each assigned pad has a scene
and a pad colour (a name from `apc40.SCENE_PAD_COLOURS`), and optionally an accent mode (see
accents.py). A pad whose scene was deleted later is kept and shown as "missing" instead of being
silently dropped.
"""

import json
from pathlib import Path
from threading import Lock
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

from .apc40 import DEFAULT_SCENE_PAD_COLOUR, PAD_COUNT, SCENE_PAD_COLOURS
from .scenes import PersistenceError, write_json_atomic


class PadAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pad: StrictInt = Field(ge=0, lt=PAD_COUNT)
    scene: str = Field(min_length=1)
    colour: str = DEFAULT_SCENE_PAD_COLOUR
    # "off" recalls the look; "hold"/"beats" lay it over the output while held / for N beats.
    accent: Literal["off", "hold", "beats"] = "off"
    accent_beats: float = Field(default=2.0, ge=0.25, le=64.0)

    @model_validator(mode="after")
    def _known_colour(self) -> "PadAssignment":
        if self.colour not in SCENE_PAD_COLOURS:
            raise ValueError(
                f"Unknown pad colour '{self.colour}' (choose from {', '.join(SCENE_PAD_COLOURS)})."
            )
        return self


class Apc40File(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pads: list[PadAssignment] = Field(default_factory=list)
    sequence_pads: list["SequencePadAssignment"] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "Apc40File":
        seen = [p.pad for p in self.pads]
        duplicates = sorted({p for p in seen if seen.count(p) > 1})
        if duplicates:
            raise ValueError(f"Pads assigned twice: {', '.join(str(p + 1) for p in duplicates)}.")
        sequence_seen = [p.pad for p in self.sequence_pads]
        if len(set(sequence_seen)) != len(sequence_seen):
            raise ValueError("A sequence pad is assigned twice.")
        return self


class SequencePadAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pad: StrictInt = Field(ge=0, lt=PAD_COUNT)
    sequence: str = Field(min_length=1)
    colour: str = DEFAULT_SCENE_PAD_COLOUR

    @model_validator(mode="after")
    def _known_colour(self) -> "SequencePadAssignment":
        if self.colour not in SCENE_PAD_COLOURS:
            raise ValueError(f"Unknown sequence pad colour '{self.colour}'.")
        return self


class PadStore:
    """Pad assignments in memory, saved atomically when `path` is set (None = memory only)."""

    def __init__(self, path: Optional[Path] = None, document: Optional[Apc40File] = None) -> None:
        self._path = path
        self._lock = Lock()
        self._pads = {p.pad: p for p in (document or Apc40File()).pads}
        self._sequence_pads = {p.pad: p for p in (document or Apc40File()).sequence_pads}

    @classmethod
    def load(cls, path: Path) -> "PadStore":
        if not path.exists():
            return cls(path)
        try:
            document = Apc40File.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValidationError) as error:
            raise ValueError(
                f"Invalid APC40 pad file {path}: {error}. "
                f"A previous version may exist in {path.name}.bak."
            ) from error
        return cls(path, document)

    def get(self, pad: int) -> Optional[PadAssignment]:
        with self._lock:
            return self._pads.get(pad)

    def all(self) -> list[PadAssignment]:
        with self._lock:
            return [self._pads[p] for p in sorted(self._pads)]

    def assign(self, assignment: PadAssignment) -> None:
        with self._lock:
            pads = {**self._pads, assignment.pad: assignment}
            self._save(pads)

    def clear(self, pad: int) -> None:
        with self._lock:
            pads = {p: a for p, a in self._pads.items() if p != pad}
            self._save(pads)

    def get_sequence(self, pad: int) -> Optional[SequencePadAssignment]:
        with self._lock:
            return self._sequence_pads.get(pad)

    def assign_sequence(self, assignment: SequencePadAssignment) -> None:
        with self._lock:
            self._save(self._pads, {**self._sequence_pads, assignment.pad: assignment})

    def clear_sequence(self, pad: int) -> None:
        with self._lock:
            self._save(self._pads, {p: a for p, a in self._sequence_pads.items() if p != pad})

    def _save(
        self, pads: dict[int, PadAssignment],
        sequence_pads: Optional[dict[int, SequencePadAssignment]] = None,
    ) -> None:
        """Write first, then update memory, so a failed write changes nothing."""
        sequence_pads = self._sequence_pads if sequence_pads is None else sequence_pads
        if self._path is not None:
            data = Apc40File(
                pads=[pads[p] for p in sorted(pads)],
                sequence_pads=[sequence_pads[p] for p in sorted(sequence_pads)],
            ).model_dump(mode="json")
            for entry in data["pads"]:
                if entry["accent"] == "off":  # keep plain look pads as before
                    del entry["accent"], entry["accent_beats"]
                elif entry["accent"] == "hold":
                    del entry["accent_beats"]
            try:
                write_json_atomic(self._path, data)
            except OSError as error:
                raise PersistenceError(f"Could not save {self._path}: {error}") from error
        self._pads = pads
        self._sequence_pads = sequence_pads
