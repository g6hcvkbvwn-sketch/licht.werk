"""MIDI mapping model and persistence. No MIDI library and no hardware access here."""

import json
import logging
from pathlib import Path
from threading import RLock
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .patch import Patch, ResolvedFixture
from .scenes import PersistenceError, write_json_atomic

logger = logging.getLogger(__name__)

# Continuous controls (faders, pots, encoders) are CC messages; buttons are note messages.
CONTINUOUS_ACTIONS = ("master", "intensity", "hue", "saturation", "white", "strobe")
TRIGGER_ACTIONS = ("scene", "blackout", "flash", "strobe_hold", "preview")
ActionType = Literal[
    "master", "intensity", "hue", "saturation", "white", "strobe",
    "scene", "blackout", "flash", "strobe_hold", "preview",
]
# Actions on fixtures: target is a fixture id, a group name or ALL_TARGET.
FIXTURE_ACTIONS = (
    "intensity", "hue", "saturation", "white", "strobe", "flash", "strobe_hold",
)
ALL_TARGET = "all"
# Attributes a target needs (at least one fixture) for the action to make sense.
REQUIRED_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "intensity": ("intensity",),
    "flash": ("intensity",),
    "hue": ("red", "green", "blue"),
    "saturation": ("red", "green", "blue"),
    "white": ("white",),
    "strobe": ("strobe",),
    "strobe_hold": ("strobe",),
}


class MidiKey(BaseModel):
    """One physical control as seen on the wire. Channel is 1-16 as shown in the Xone:K2 manual."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    kind: Literal["note", "cc"]
    channel: int = Field(ge=1, le=16)
    number: int = Field(ge=0, le=127)


class MidiAction(BaseModel):
    """An application command.

    `target` is a scene id (scene) or a fixture id, group name or "all" (fixture actions).
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    type: ActionType
    target: Optional[str] = None

    @model_validator(mode="after")
    def _target_rules(self) -> "MidiAction":
        needs_target = self.type == "scene" or self.type in FIXTURE_ACTIONS
        if needs_target and not self.target:
            raise ValueError(f"Action '{self.type}' needs a target.")
        if not needs_target and self.target is not None:
            raise ValueError(f"Action '{self.type}' takes no target.")
        return self

    @property
    def continuous(self) -> bool:
        return self.type in CONTINUOUS_ACTIONS


class MidiBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    key: MidiKey
    action: MidiAction
    # Endless encoders send relative steps (two's complement: 1 = +1, 127 = -1).
    relative: bool = False

    @model_validator(mode="after")
    def _kind_matches_action(self) -> "MidiBinding":
        if self.relative and self.key.kind != "cc":
            raise ValueError("Only CC controls (encoders) can be relative.")
        if self.action.continuous and self.key.kind != "cc":
            raise ValueError(f"'{self.action.type}' needs a fader/pot (CC), not a button (note).")
        if not self.action.continuous and self.key.kind != "note":
            raise ValueError(f"'{self.action.type}' needs a button (note), not a fader/pot (CC).")
        return self


class MidiMappingFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    bindings: list[MidiBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> "MidiMappingFile":
        keys = [b.key for b in self.bindings]
        if len(set(keys)) != len(keys):
            raise ValueError("Two bindings use the same physical control.")
        actions = [b.action for b in self.bindings]
        if len(set(actions)) != len(actions):
            raise ValueError("Two controls are bound to the same action.")
        return self


def target_fixtures(patch: Patch, target: str) -> tuple[ResolvedFixture, ...]:
    """Fixtures of a fixture id, group name or "all" (empty if unknown)."""
    if target == ALL_TARGET:
        return patch.fixtures
    try:
        return (patch.get(target),)
    except KeyError:
        return patch.groups().get(target, ())


def mapping_problems(document: MidiMappingFile, patch: Patch) -> list[str]:
    """Targets that can be checked now. Scene ids are checked when the control is used."""
    problems: list[str] = []
    for binding in document.bindings:
        action = binding.action
        if action.type not in FIXTURE_ACTIONS:
            continue
        fixtures = target_fixtures(patch, action.target or "")
        needed = REQUIRED_ATTRIBUTES[action.type]
        if not fixtures:
            problems.append(f"{action.type} target '{action.target}' is not a fixture or group")
        elif not any(set(needed) <= set(f.mode.attributes()) for f in fixtures):
            problems.append(
                f"'{action.target}' has no fixture with {', '.join(needed)} (for {action.type})"
            )
    return problems


class MidiMappingStore:
    """Bindings in memory, saved atomically after every change."""

    def __init__(
        self,
        patch: Patch,
        path: Optional[Path] = None,
        document: Optional[MidiMappingFile] = None,
    ) -> None:
        self._patch = patch
        self._path = path
        self._lock = RLock()
        self._document = document or MidiMappingFile()

    @classmethod
    def load(cls, path: Path, patch: Patch) -> "MidiMappingStore":
        """A missing file means no bindings yet. Invalid content is reported, never discarded."""
        document = MidiMappingFile()
        if path.exists():
            try:
                document = MidiMappingFile.model_validate(json.loads(path.read_text("utf-8")))
            except (OSError, json.JSONDecodeError, ValidationError) as error:
                raise ValueError(f"Invalid MIDI mapping file {path}: {error}") from error
            problems = mapping_problems(document, patch)
            if problems:
                raise ValueError(
                    f"Invalid MIDI mapping file {path} (does it match config/patch.json?):\n- "
                    + "\n- ".join(problems)
                )
        return cls(patch, path, document)

    def bindings(self) -> list[MidiBinding]:
        with self._lock:
            return list(self._document.bindings)

    def lookup(self, key: MidiKey) -> Optional[MidiAction]:
        binding = self.lookup_binding(key)
        return binding.action if binding is not None else None

    def lookup_binding(self, key: MidiKey) -> Optional[MidiBinding]:
        with self._lock:
            for binding in self._document.bindings:
                if binding.key == key:
                    return binding
        return None

    def replace(self, bindings: list[MidiBinding]) -> list[MidiBinding]:
        document = MidiMappingFile(bindings=bindings)
        problems = mapping_problems(document, self._patch)
        if problems:
            raise ValueError("; ".join(problems))
        with self._lock:
            if self._path is not None:
                try:
                    write_json_atomic(self._path, document.model_dump(mode="json"))
                except OSError as error:
                    raise PersistenceError(
                        f"Could not save MIDI mapping to {self._path}: {error}"
                    ) from error
            self._document = document
            return list(document.bindings)

    def bind(self, key: MidiKey, action: MidiAction, relative: bool = False) -> list[MidiBinding]:
        """Bind a control to an action, replacing earlier uses of either."""
        with self._lock:
            kept = [b for b in self._document.bindings if b.key != key and b.action != action]
            return self.replace(kept + [MidiBinding(key=key, action=action, relative=relative)])
