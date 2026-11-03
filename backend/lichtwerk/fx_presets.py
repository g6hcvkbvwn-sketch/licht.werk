"""Persistent personal FX slots, separate from runtime effects and scene state."""

import json
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .effects import (
    EFFECT_TYPES, ComboDefinition, EffectComponent, FxDefinition, EffectType, family, family_presets,
    combo_presets,
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


class ComboSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target: str
    row: int = Field(ge=0, le=4)
    definition: ComboDefinition
    component: EffectComponent = "beam"


class FxFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    slots: list[FxSlot] = Field(default_factory=list)
    combos: list[ComboSlot] = Field(default_factory=list)


class FxPresetStore:
    def __init__(self, patch: Patch, rig: RigStore, path: Path | None = None) -> None:
        self._patch = patch
        self._rig = rig
        self._path = path
        self._lock = RLock()
        self._slots: dict[tuple[str, EffectType, int, str], FxSlot] = {}
        self._combos: dict[tuple[str, int, str], ComboSlot] = {}
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
                for item in document.combos:
                    self.validate_combo(item.target, item.row, item.definition, component=item.component)
                    key = (item.target, item.row, item.component)
                    if key in self._combos:
                        raise ValueError(f"Duplicate Combo slot {key}.")
                    self._combos[key] = item
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
        missing_zones = set(definition.params.zone_ids) - {z.id for z in document.zones}
        if missing_zones:
            raise ValueError(f"Zone route references missing zones: {sorted(missing_zones)}.")
        if definition.params.stops and (component == "ring" or page != EffectType.CIRCLE):
            raise ValueError("Target and zone routes are available only in the Move family.")

    def get(
        self, target: str, page: EffectType, row: int, *,
        component: EffectComponent = "beam",
    ) -> FxDefinition:
        if page not in EFFECT_TYPES or not 0 <= row < 5:
            raise ValueError("Choose an FX family and slot from 1 to 5.")
        with self._lock:
            item = self._slots.get((target, page, row, component))
            return (item.definition if item else family_presets(page)[row]).model_copy(deep=True)

    def _write(
        self, items: dict[tuple[str, EffectType, int, str], FxSlot],
        combos: dict[tuple[str, int, str], ComboSlot] | None = None,
    ) -> None:
        combos = self._combos if combos is None else combos
        if self._path is not None:
            try:
                write_json_atomic(self._path, FxFile(
                    slots=list(items.values()), combos=list(combos.values()),
                ).model_dump(mode="json"))
            except OSError as error:
                raise PersistenceError(f"Could not save FX presets to {self._path}: {error}") from error
        self._slots = items
        self._combos = combos

    def validate_combo(self, target: str, row: int, definition: ComboDefinition, *,
                       component: EffectComponent = "beam") -> None:
        for effect in definition.effects:
            self.validate(target, family(effect.kind), row, effect, component=component)

    def get_combo(self, target: str, row: int, *,
                  component: EffectComponent = "beam") -> ComboDefinition:
        if not 0 <= row < 5:
            raise ValueError("Choose a Combo slot from 1 to 5.")
        if component not in ("beam", "ring"):
            raise ValueError("Effect component must be 'beam' or 'ring'.")
        with self._lock:
            item = self._combos.get((target, row, component))
            if item is not None:
                return item.definition.model_copy(deep=True)
            factory = combo_presets()[row]
            effects = [effect for effect in factory.effects
                       if self.participants(target, component, effect.kind)]
            if not effects:
                raise ValueError(f"'{target}' has no participants compatible with Combo FX.")
            return ComboDefinition(name=factory.name, effects=effects).model_copy(deep=True)

    def save_combo(self, target: str, row: int, definition: ComboDefinition, *,
                   component: EffectComponent = "beam") -> None:
        self.validate_combo(target, row, definition, component=component)
        with self._lock:
            item = ComboSlot(target=target, row=row, definition=definition.model_copy(deep=True),
                             component=component)
            self._write(self._slots, {**self._combos, (target, row, component): item})

    def reset_combo(self, target: str, row: int, *,
                    component: EffectComponent = "beam") -> None:
        self.get_combo(target, row, component=component)
        with self._lock:
            self._write(self._slots, {key: item for key, item in self._combos.items()
                                     if key != (target, row, component)})

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
        zones = {zone.id for zone in document.zones}
        with self._lock:
            for slot in self._slots.values():
                missing = set(slot.definition.params.target_ids) - targets
                if missing:
                    raise ValueError(
                        f"Remove targets {sorted(missing)} from FX preset '{slot.definition.name}' before deleting them."
                    )
                missing = set(slot.definition.params.zone_ids) - zones
                if missing:
                    raise ValueError(
                        f"Remove zones {sorted(missing)} from FX preset '{slot.definition.name}' before deleting them."
                    )
            for slot in self._combos.values():
                missing = {target for effect in slot.definition.effects
                           for target in effect.params.target_ids} - targets
                if missing:
                    raise ValueError(
                        f"Remove targets {sorted(missing)} from Combo '{slot.definition.name}' before deleting them."
                    )
                missing = {zone for effect in slot.definition.effects
                           for zone in effect.params.zone_ids} - zones
                if missing:
                    raise ValueError(
                        f"Remove zones {sorted(missing)} from Combo '{slot.definition.name}' before deleting them."
                    )
