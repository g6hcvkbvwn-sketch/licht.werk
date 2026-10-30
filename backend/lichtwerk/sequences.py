"""Validated, human-readable sequence definitions referencing stable scene IDs."""

import json
from pathlib import Path
from threading import RLock
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .fixtures import ID_PATTERN
from .scenes import BEAT_VALUES, PersistenceError, SceneStore, slugify, write_json_atomic
from .transitions import MAX_FADE_SECONDS

class SequenceStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    scene_id: str = Field(pattern=ID_PATTERN)
    fade: Optional[float] = Field(default=None, ge=0, le=MAX_FADE_SECONDS)
    hold: float = Field(default=1.0, ge=0, le=MAX_FADE_SECONDS)


class SequenceContent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    timing: Literal["seconds", "beats"] = "seconds"
    loop: bool = False
    live_controls: dict[str, list[str]] = Field(default_factory=dict)
    steps: list[SequenceStep] = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def valid_timing(self) -> "SequenceContent":
        if self.timing == "beats":
            for step in self.steps:
                if step.fade is not None and step.fade != 0 and step.fade not in BEAT_VALUES:
                    raise ValueError("Beat fades must be a cut (0) or .5, 1, 2, 4, 8, 16, 32.")
                if step.hold not in BEAT_VALUES:
                    raise ValueError("Beat holds must be .5, 1, 2, 4, 8, 16, 32.")
        return self


class Sequence(SequenceContent):
    id: str = Field(pattern=ID_PATTERN)


class SequenceFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sequences: list[Sequence] = Field(default_factory=list)


class SequenceStore:
    def __init__(self, scenes: SceneStore, path: Optional[Path] = None) -> None:
        self._scenes = scenes
        self._path = path
        self._items: list[Sequence] = []
        self._lock = RLock()

    @classmethod
    def load(cls, path: Path, scenes: SceneStore) -> "SequenceStore":
        store = cls(scenes, path)
        if path.exists():
            try:
                document = SequenceFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
                seen: set[str] = set()
                for item in document.sequences:
                    if item.id in seen:
                        raise ValueError(f"Duplicate sequence id '{item.id}'.")
                    seen.add(item.id)
                    store._check(item)
                store._items = document.sequences
            except (ValueError, KeyError) as error:
                raise ValueError(f"Invalid sequence file {path}: {error}") from error
        return store

    def all(self) -> list[Sequence]:
        with self._lock:
            return [item.model_copy(deep=True) for item in self._items]

    def get(self, sequence_id: str) -> Sequence:
        with self._lock:
            for item in self._items:
                if item.id == sequence_id:
                    return item.model_copy(deep=True)
        raise KeyError(f"Sequence '{sequence_id}' does not exist.")

    def _check(self, content: SequenceContent) -> None:
        for fixture_id, names in content.live_controls.items():
            fixture = self._scenes._patch.get(fixture_id)
            for name in names:
                if name not in fixture.mode.attributes():
                    raise ValueError(f"Fixture '{fixture_id}' has no attribute '{name}'.")
        for step in content.steps:
            try:
                self._scenes.get(step.scene_id)
            except KeyError as error:
                raise ValueError(f"Sequence references missing scene '{step.scene_id}'.") from error

    def create(self, content: SequenceContent) -> Sequence:
        with self._lock:
            self._check(content)
            base = slugify(content.name)
            taken = {item.id for item in self._items}
            candidate, number = base, 2
            while candidate in taken:
                candidate, number = f"{base}-{number}", number + 1
            item = Sequence(id=candidate, **content.model_dump())
            self._commit(self._items + [item])
            return item.model_copy(deep=True)

    def update(self, sequence_id: str, content: SequenceContent) -> Sequence:
        with self._lock:
            self.get(sequence_id)
            self._check(content)
            item = Sequence(id=sequence_id, **content.model_dump())
            self._commit([item if old.id == sequence_id else old for old in self._items])
            return item.model_copy(deep=True)

    def delete(self, sequence_id: str) -> None:
        with self._lock:
            self.get(sequence_id)
            self._commit([item for item in self._items if item.id != sequence_id])

    def scene_references(self, scene_id: str) -> list[str]:
        return [item.name for item in self.all() if any(step.scene_id == scene_id for step in item.steps)]

    def _commit(self, items: list[Sequence]) -> None:
        if self._path is not None:
            try:
                write_json_atomic(self._path, SequenceFile(sequences=items).model_dump(mode="json"))
            except OSError as error:
                raise PersistenceError(f"Could not save sequences to {self._path}: {error}") from error
        self._items = items
