"""Stored scenes: validation against the patch, resolution to fixture values, persistence.

A scene is a partial definition: it lists values only for the attributes it controls.
Within a scene, group entries are applied in their listed order and fixture entries are
applied last, so a fixture entry wins over a group entry and a later group wins over an
earlier one. Group entries apply only to members that have the attribute. Disabled
fixtures are skipped on recall.
"""

import json
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .effects import FxDefinition, family
from .fixtures import ID_PATTERN
from .patch import Patch
from .transitions import MAX_FADE_SECONDS

BEAT_VALUES = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
Level = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
Values = dict[str, Level]


class SceneSpot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    u: float = Field(ge=0, le=1)
    v: float = Field(ge=0, le=1)


class ScenePosition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_id: Optional[str] = None
    zone_id: Optional[str] = None
    spot: Optional[SceneSpot] = None
    included_zone_ids: list[str] = Field(default_factory=list)
    excluded_zone_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_aim(self) -> "ScenePosition":
        if self.target_id and (self.zone_id or self.spot):
            raise ValueError("A look position selects a named target OR a zone/spot.")
        if self.spot is not None and not self.zone_id:
            raise ValueError("A look spot requires a zone.")
        if not self.target_id and not self.zone_id:
            raise ValueError("A look position requires a target or zone.")
        if self.zone_id:
            self.included_zone_ids = list(dict.fromkeys([self.zone_id, *self.included_zone_ids]))
            self.excluded_zone_ids = [zone for zone in self.excluded_zone_ids if zone != self.zone_id]
        if set(self.included_zone_ids) & set(self.excluded_zone_ids):
            raise ValueError("A zone cannot be both included and excluded.")
        return self


class LookStripFx(BaseModel):
    """FX that ran on one controller strip when a look was captured (a snapshot, not a preset link)."""

    model_config = ConfigDict(extra="forbid", strict=True)
    strip: str = Field(min_length=1)  # the strip's target: group or fixture id
    component: Literal["beam", "ring"] = "beam"
    preset: int = Field(ge=0)  # FX / Combo row it was launched from
    combo: Optional[str] = None  # Combo name when the effects ran as one Combo
    effects: list[FxDefinition] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def valid_bundle(self) -> "LookStripFx":
        if self.combo is None and len(self.effects) != 1:
            raise ValueError("A single look FX entry holds exactly one effect.")
        pages = [family(effect.kind) for effect in self.effects]
        if len(set(pages)) != len(pages):
            raise ValueError("A look FX entry can include only one effect per family.")
        return self


class LookFx(BaseModel):
    """The FX set of a look: strip modes plus the FX of the strips whose fixtures are in it."""

    model_config = ConfigDict(extra="forbid", strict=True)
    modes: dict[str, Literal["group", "single"]] = Field(default_factory=dict)
    strips: list[LookStripFx] = Field(default_factory=list)


class SceneContent(BaseModel):
    """Editable part of a scene (what the API accepts)."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    name: str = Field(min_length=1, max_length=80)
    fade_seconds: float = Field(default=0.0, ge=0.0, le=MAX_FADE_SECONDS)
    fade_timing: Literal["seconds", "beats"] = "seconds"
    fade_beats: float = 1.0
    groups: dict[str, Values] = Field(default_factory=dict)
    fixtures: dict[str, Values] = Field(default_factory=dict)
    positions: dict[str, ScenePosition] = Field(default_factory=dict)
    # None = a plain scene: recall leaves running FX untouched.
    fx: Optional[LookFx] = None

    @model_validator(mode="after")
    def valid_fade_beats(self) -> "SceneContent":
        if self.fade_beats != 0 and self.fade_beats not in BEAT_VALUES:
            raise ValueError("Beat fades must be a cut (0) or .5, 1, 2, 4, 8, 16, 32.")
        return self


class Scene(SceneContent):
    id: str = Field(pattern=ID_PATTERN)


class SceneFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scenes: list[Scene] = Field(default_factory=list)


class PersistenceError(RuntimeError):
    """Scenes could not be written to disk; nothing was changed."""


def scene_problems(scene: SceneContent, patch: Patch) -> list[str]:
    """Every reference in the scene that does not match the current patch."""
    problems: list[str] = []
    patched = {fixture.patch.id: fixture for fixture in patch.fixtures}
    groups = patch.groups()
    for group, values in scene.groups.items():
        members = groups.get(group)
        if members is None:
            problems.append(f"unknown group '{group}'")
            continue
        for name in values:
            if not any(name in member.mode.attributes() for member in members):
                problems.append(f"no fixture in group '{group}' has attribute '{name}'")
    for fixture_id, values in scene.fixtures.items():
        fixture = patched.get(fixture_id)
        if fixture is None:
            problems.append(f"unknown fixture '{fixture_id}'")
            continue
        for name in values:
            if name not in fixture.mode.attributes():
                problems.append(f"fixture '{fixture_id}' has no attribute '{name}'")
    for fixture_id in scene.positions:
        fixture = patched.get(fixture_id)
        if fixture is None:
            problems.append(f"unknown fixture '{fixture_id}'")
        elif not {"pan", "tilt"} <= set(fixture.mode.attributes()):
            problems.append(f"fixture '{fixture_id}' does not support positioning")
    if scene.fx is not None:
        for entry in scene.fx.strips:
            if entry.strip not in groups and entry.strip not in patched:
                problems.append(f"look FX for unknown strip '{entry.strip}'")
    if not scene.groups and not scene.fixtures and not scene.positions:
        problems.append("the scene contains no values")
    return problems


def look_route_references(scene: SceneContent) -> tuple[set[str], set[str]]:
    """Named targets and zones used by the Move routes of a look's FX set."""
    targets: set[str] = set()
    zones: set[str] = set()
    for entry in scene.fx.strips if scene.fx is not None else ():
        for effect in entry.effects:
            targets.update(effect.params.target_ids)
            zones.update(effect.params.zone_ids)
    return targets, zones


def resolve_scene(scene: SceneContent, patch: Patch) -> dict[str, dict[str, float]]:
    """Per-fixture target values of a (validated) scene, for enabled fixtures only."""
    result: dict[str, dict[str, float]] = {}
    groups = patch.groups()
    for group, values in scene.groups.items():
        for member in groups[group]:
            if not member.patch.enabled:
                continue
            attributes = member.mode.attributes()
            applicable = {name: v for name, v in values.items() if name in attributes}
            if applicable:
                result.setdefault(member.patch.id, {}).update(applicable)
    for fixture_id, values in scene.fixtures.items():
        if patch.get(fixture_id).patch.enabled:
            result.setdefault(fixture_id, {}).update(values)
    return result


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug or "scene"


class SceneStore:
    """Scenes in memory, saved as JSON after every change.

    Writes are atomic (temporary file + rename) and the previous file is kept as
    `<name>.bak`. With `path=None` scenes live in memory only (used by tests).
    """

    def __init__(self, patch: Patch, path: Optional[Path] = None, scenes: tuple = ()) -> None:
        self._patch = patch
        self._path = path
        self._lock = RLock()
        self._scenes: list[Scene] = [scene.model_copy(deep=True) for scene in scenes]
        self.position_validator = None
        self.route_validator = None
        self.delete_guard = None

    @classmethod
    def load(cls, path: Path, patch: Patch) -> "SceneStore":
        """Load and validate scenes. A missing file means no scenes yet."""
        if not path.exists():
            return cls(patch, path)
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid JSON in scene file {path} at line {error.lineno}: {error.msg}. "
                f"A previous version may exist in {path.name}.bak."
            ) from error
        try:
            scene_file = SceneFile.model_validate(values)
        except ValidationError as error:
            raise ValueError(f"Invalid scene file {path}: {error}") from error
        problems: list[str] = []
        seen: set[str] = set()
        for scene in scene_file.scenes:
            if scene.id in seen:
                problems.append(f"duplicate scene id '{scene.id}'")
            seen.add(scene.id)
            problems += [f"scene '{scene.id}': {p}" for p in scene_problems(scene, patch)]
        if problems:
            raise ValueError(
                f"Invalid scene file {path} (does it still match config/patch.json?):\n- "
                + "\n- ".join(problems)
            )
        return cls(patch, path, tuple(scene_file.scenes))

    def all(self) -> list[Scene]:
        with self._lock:
            return [scene.model_copy(deep=True) for scene in self._scenes]

    def get(self, scene_id: str) -> Scene:
        with self._lock:
            for scene in self._scenes:
                if scene.id == scene_id:
                    return scene.model_copy(deep=True)
        raise KeyError(f"Look '{scene_id}' does not exist.")

    def create(self, content: SceneContent) -> Scene:
        self._check(content)
        with self._lock:
            scene = Scene(id=self._unique_id(slugify(content.name)), **content.model_dump())
            self._commit(self._scenes + [scene])
            return scene.model_copy(deep=True)

    def update(self, scene_id: str, content: SceneContent) -> Scene:
        self._check(content)
        with self._lock:
            self.get(scene_id)
            scene = Scene(id=scene_id, **content.model_dump())
            self._commit([scene if s.id == scene_id else s for s in self._scenes])
            return scene.model_copy(deep=True)

    def duplicate(self, scene_id: str, name: Optional[str] = None) -> Scene:
        with self._lock:
            source = self.get(scene_id)
            content = SceneContent(**source.model_dump(exclude={"id"})).model_copy(
                update={"name": name or f"{source.name} copy"[:80]}
            )
            return self.create(content)

    def delete(self, scene_id: str) -> None:
        with self._lock:
            self.get(scene_id)
            if self.delete_guard is not None:
                self.delete_guard(scene_id)
            self._commit([s for s in self._scenes if s.id != scene_id])

    def _check(self, content: SceneContent) -> None:
        problems = scene_problems(content, self._patch)
        if problems:
            raise ValueError("Invalid look: " + "; ".join(problems) + ".")
        if content.positions and self.position_validator is not None:
            self.position_validator({key: value.model_dump() for key, value in content.positions.items()})
        if content.fx is not None and self.route_validator is not None:
            self.route_validator(content)

    def _unique_id(self, base: str) -> str:
        taken = {scene.id for scene in self._scenes}
        candidate, number = base, 2
        while candidate in taken:
            candidate, number = f"{base}-{number}", number + 1
        return candidate

    def _commit(self, scenes: list[Scene]) -> None:
        """Write first, then update memory, so a failed write changes nothing."""
        if self._path is not None:
            data = SceneFile(scenes=scenes).model_dump(mode="json")
            try:
                write_json_atomic(self._path, data)
            except OSError as error:
                raise PersistenceError(f"Could not save scenes to {self._path}: {error}") from error
        self._scenes = scenes


def write_json_atomic(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temp_file:
            json.dump(data, temp_file, indent=2, ensure_ascii=False)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        if path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
