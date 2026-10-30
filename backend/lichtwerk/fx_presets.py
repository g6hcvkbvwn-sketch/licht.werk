"""Persistent personal FX slots, separate from runtime effects and scene state."""

import json
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .effects import (
    EFFECT_TYPES, EffectComponent, FxDefinition, EffectType, family, family_presets,
)
from .effects import fits
from .patch import Patch
from .rings import ring_segments, segment_participant
from .room import RigStore, RoomFile
from .scenes import PersistenceError, write_json_atomic


class FxSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target: str
    page: EffectType = Field(strict=False)
    row: int = Field(ge=0, le=4)
    definition: FxDefinition
    component: EffectComponent = "beam"


class FxFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    slots: list[FxSlot] = Field(default_factory=list)


class FxPresetStore:
    def __init__(self, patch: Patch, rig: RigStore, path: Path | None = None) -> None:
        self._patch = patch
        self._rig = rig
        self._path = path
        self._lock = RLock()
        self._slots: dict[tuple[str, EffectType, int, str], FxSlot] = {}
        if path is not None and path.exists():
            try:
                document = FxFile.model_validate_json(path.read_text(encoding="utf-8"))
                for item in document.slots:
                    self.validate(
                        item.target, item.page, item.row, item.definition,
                        component=item.component,
                    )
                    key = (item.target, item.page, item.row, item.component)
                    if key in self._slots:
                        raise ValueError(f"Duplicate FX slot {key}.")
                    self._slots[key] = item
            except (OSError, ValueError, KeyError) as error:
                raise ValueError(f"Invalid FX preset file {path}: {error}") from error

    def fixtures(self, target: str):
        try:
            members = (self._patch.get(target),)
        except KeyError:
            members = self._patch.group(target)
        return members

    def participants(
        self, target: str, component: EffectComponent = "beam",
        kind: EffectType = EffectType.CHASE,
    ) -> list[str]:
        """Return ordered compatible FX participant IDs for a fixture or group."""
        members = self.fixtures(target)
        if component == "beam":
            return [
                fixture.patch.id for fixture in members
                if fits(kind, fixture, component)
            ]
        if component != "ring":
            raise ValueError("Effect component must be 'beam' or 'ring'.")
        if kind in (EffectType.CIRCLE, EffectType.FIGURE8):
            return []
        return [
            segment_participant(fixture.patch.id, segment)
            for fixture in members
            for segment in ring_segments(fixture)
        ]

    def validate(
        self, target: str, page: EffectType, row: int, definition: FxDefinition, *,
        component: EffectComponent = "beam",
    ) -> None:
        if page not in EFFECT_TYPES or not 0 <= row < 5:
            raise ValueError("Choose one of the three FX families and a slot from 1 to 5.")
        if family(definition.kind) != page:
            raise ValueError("The chosen factory/generator belongs to a different FX family.")
        if component not in ("beam", "ring"):
            raise ValueError("Effect component must be 'beam' or 'ring'.")
        if component == "ring" and definition.kind in (
            EffectType.CIRCLE, EffectType.FIGURE8,
        ):
            raise ValueError("Move effects are not available for ring sections.")
        members = set(self.participants(target, component, definition.kind))
        if not members:
            raise ValueError(f"'{target}' has no participants compatible with this FX family.")
        unknown = set(definition.params.order) - members
        if unknown:
            raise ValueError(f"FX order contains participants outside its target: {sorted(unknown)}.")
        document = self._rig.room_file()
        targets = {t.id for t in document.targets}
        missing = set(definition.params.target_ids) - targets
        if missing:
            raise ValueError(f"Target Tour references missing targets: {sorted(missing)}.")
        if definition.params.target_ids and (component == "ring" or page != EffectType.CIRCLE):
            raise ValueError("Target Tour is available only in the Move family.")

    def get(
        self, target: str, page: EffectType, row: int, *,
        component: EffectComponent = "beam",
    ) -> FxDefinition:
        if page not in EFFECT_TYPES or not 0 <= row < 5:
            raise ValueError("Choose an FX family and slot from 1 to 5.")
        with self._lock:
            item = self._slots.get((target, page, row, component))
            return (item.definition if item else family_presets(page)[row]).model_copy(deep=True)

    def _write(self, items: dict[tuple[str, EffectType, int], FxSlot]) -> None:
        if self._path is not None:
            try:
                write_json_atomic(self._path, FxFile(slots=list(items.values())).model_dump(mode="json"))
            except OSError as error:
                raise PersistenceError(f"Could not save FX presets to {self._path}: {error}") from error
        self._slots = items

    def save(
        self, target: str, page: EffectType, row: int, definition: FxDefinition, *,
        component: EffectComponent = "beam",
    ) -> None:
        self.validate(target, page, row, definition, component=component)
        with self._lock:
            item = FxSlot(
                target=target, page=page, row=row, component=component,
                definition=definition.model_copy(deep=True),
            )
            self._write({**self._slots, (target, page, row, component): item})

    def reset(
        self, target: str, page: EffectType, row: int, *,
        component: EffectComponent = "beam",
    ) -> None:
        self.get(target, page, row, component=component)
        with self._lock:
            self._write({
                key: value for key, value in self._slots.items()
                if key != (target, page, row, component)
            })

    def validate_room(self, document: RoomFile) -> None:
        targets = {target.id for target in document.targets}
        with self._lock:
            for slot in self._slots.values():
                missing = set(slot.definition.params.target_ids) - targets
                if missing:
                    raise ValueError(
                        f"Remove targets {sorted(missing)} from FX preset '{slot.definition.name}' before deleting them."
                    )
