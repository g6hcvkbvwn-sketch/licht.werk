"""Room, named targets, zones, head assignments and per-head setup, with file persistence.

`config/room.json` holds the room size, named targets, zones and which
zones/targets each moving head uses. `config/heads.json` holds
the mounting, calibration, movement limits and rest position of each moving head. Both are
validated against the patch at startup and written atomically (previous file kept as .bak).
Values that have not been measured yet are null and must be filled in at the club.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from threading import RLock, local
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .fixtures import ID_PATTERN
from .patch import Patch, ResolvedFixture
from .positioning import HeadSetup, Point
from .zones import RoomSize, Zone
from .scenes import PersistenceError, write_json_atomic


def is_moving_head(fixture: ResolvedFixture) -> bool:
    attributes = fixture.mode.attributes()
    return "pan" in attributes and "tilt" in attributes


class Room(BaseModel):
    """Room size in meters; null until measured. Origin and axes: see project.md."""

    model_config = ConfigDict(extra="forbid", strict=True)

    size_x: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    size_y: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    size_z: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)


class Target(BaseModel):
    """A named place to aim at. Coordinates are null until measured."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    x: Optional[float] = Field(default=None, allow_inf_nan=False)
    y: Optional[float] = Field(default=None, allow_inf_nan=False)
    z: Optional[float] = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _all_or_none(self) -> "Target":
        if len({self.x is None, self.y is None, self.z is None}) != 1:
            raise ValueError(f"Target '{self.id}': x, y and z are set together or not at all.")
        return self

    @property
    def point(self) -> Optional[Point]:
        if self.x is None or self.y is None or self.z is None:
            return None
        return Point(x=self.x, y=self.y, z=self.z)


class HeadAssignment(BaseModel):
    """Selected base aim and independent included/excluded area sets of a head or strip.

    One aim per head for now: nothing, one target or one include zone.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    aim_target: Optional[str] = None
    aim_zone: Optional[str] = None
    include_zones: list[str] = Field(default_factory=list)
    exclude_zones: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _legacy_include(cls, values):
        if isinstance(values, dict) and "include_zones" not in values and values.get("aim_zone"):
            return {**values, "include_zones": [values["aim_zone"]]}
        return values

    @model_validator(mode="after")
    def _one_aim(self) -> "HeadAssignment":
        if self.aim_target is not None and self.aim_zone is not None:
            raise ValueError("A head aims at one target or one include zone, not both.")
        if len(set(self.exclude_zones)) != len(self.exclude_zones):
            raise ValueError("An exclude zone is listed twice for the same head.")
        if len(set(self.include_zones)) != len(self.include_zones):
            raise ValueError("An include zone is listed twice for the same head.")
        return self


class RoomFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    room: Room = Field(default_factory=Room)
    targets: list[Target] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    # Keyed by moving-head fixture id.
    assignments: dict[str, HeadAssignment] = Field(default_factory=dict)
    strip_assignments: dict[str, HeadAssignment] = Field(default_factory=dict)

    @property
    def size(self) -> Optional[RoomSize]:
        """Room size (x, y, z) once all three are measured, else None."""
        room = self.room
        if room.size_x is None or room.size_y is None or room.size_z is None:
            return None
        return (room.size_x, room.size_y, room.size_z)

    def zone(self, zone_id: str) -> Zone:
        for zone in self.zones:
            if zone.id == zone_id:
                return zone
        raise KeyError(f"Zone '{zone_id}' does not exist.")


class HeadsFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    heads: dict[str, HeadSetup] = Field(default_factory=dict)


def room_problems(document: RoomFile) -> list[str]:
    problems: list[str] = []
    for kind, items in (("target", document.targets), ("zone", document.zones)):
        ids = [item.id for item in items]
        for duplicate in sorted({i for i in ids if ids.count(i) > 1}):
            problems.append(f"duplicate {kind} id '{duplicate}'")
    room = document.room
    for target in document.targets:
        point = target.point
        if point is None:
            continue
        for axis, size in (("x", room.size_x), ("y", room.size_y), ("z", room.size_z)):
            value = getattr(point, axis)
            if value < 0 or (size is not None and value > size):
                problems.append(
                    f"target '{target.id}': {axis} = {value:g} is outside the room "
                    f"(0 to {'?' if size is None else f'{size:g}'} m)"
                )
    sizes = (room.size_x, room.size_y, room.size_z)
    for zone in document.zones:
        problem = zone.fit_problem(sizes)
        if problem:
            problems.append(problem)
    zones = {zone.id: zone for zone in document.zones}
    targets = {target.id for target in document.targets}
    for head_id, assignment in sorted({**document.assignments, **document.strip_assignments}.items()):
        if assignment.aim_target is not None and assignment.aim_target not in targets:
            problems.append(f"'{head_id}' aims at target '{assignment.aim_target}', which does not exist")
        if assignment.aim_zone is not None:
            zone = zones.get(assignment.aim_zone)
            if zone is None:
                problems.append(f"'{head_id}' aims at zone '{assignment.aim_zone}', which does not exist")
        for zone_id in assignment.include_zones + assignment.exclude_zones:
            zone = zones.get(zone_id)
            if zone is None:
                problems.append(f"'{head_id}' uses zone '{zone_id}', which does not exist")
    return problems


def assignment_problems(document: RoomFile, patch: Patch) -> list[str]:
    heads = {f.patch.id for f in patch.fixtures if is_moving_head(f)}
    return [
        f"zone/target assignment for '{head_id}', which is not a patched moving head"
        for head_id in sorted(document.assignments)
        if head_id not in heads
    ]


def head_problems(document: HeadsFile, patch: Patch) -> list[str]:
    problems: list[str] = []
    heads = {f.patch.id for f in patch.fixtures if is_moving_head(f)}
    for fixture_id in document.heads:
        if fixture_id not in heads:
            problems.append(f"'{fixture_id}' is not a patched moving head (pan and tilt)")
    return problems


def _read(path: Path, model: type[BaseModel], what: str):
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in {what} file {path} at line {error.lineno}: {error.msg}. "
            f"A previous version may exist in {path.name}.bak."
        ) from error
    try:
        return model.model_validate(values)
    except ValidationError as error:
        raise ValueError(f"Invalid {what} file {path}: {error}") from error


class RigStore:
    """Room data and head setups in memory, saved after every change."""

    def __init__(
        self,
        patch: Patch,
        room_path: Optional[Path] = None,
        heads_path: Optional[Path] = None,
        room: Optional[RoomFile] = None,
        heads: Optional[HeadsFile] = None,
    ) -> None:
        self._patch = patch
        self._room_path = room_path
        self._heads_path = heads_path
        self._lock = RLock()
        self._room = room or RoomFile()
        self._heads = heads or HeadsFile()
        self._preview: Optional[RoomFile] = None
        self._context = local()
        self._definition_validator: Optional[Callable[[RoomFile], None]] = None

    def set_definition_validator(self, validator: Callable[[RoomFile], None]) -> None:
        """Check scene/sequence references before room definitions are replaced."""
        self._definition_validator = validator

    @contextmanager
    def editing_context(self, preview: bool):
        previous = getattr(self._context, "preview", None)
        self._context.preview = preview
        try:
            yield
        finally:
            self._context.preview = previous

    def editing_preview(self) -> bool:
        selected = getattr(self._context, "preview", None)
        return self._preview is not None if selected is None else selected

    @classmethod
    def load(cls, room_path: Path, heads_path: Path, patch: Patch) -> "RigStore":
        """Load and validate both files. A missing file means nothing configured yet."""
        room = RoomFile()
        heads = HeadsFile()
        if room_path.exists():
            room = _read(room_path, RoomFile, "room")
            problems = room_problems(room) + assignment_problems(room, patch)
            if problems:
                raise ValueError(f"Invalid room file {room_path}:\n- " + "\n- ".join(problems))
        if heads_path.exists():
            heads = _read(heads_path, HeadsFile, "head setup")
            problems = head_problems(heads, patch)
            if problems:
                raise ValueError(
                    f"Invalid head setup file {heads_path} (does it match config/patch.json?):\n- "
                    + "\n- ".join(problems)
                )
        return cls(patch, room_path, heads_path, room, heads)

    def room_file(self, *, live: bool = False) -> RoomFile:
        with self._lock:
            return (self._preview if not live and self.editing_preview() and self._preview is not None
                    else self._room).model_copy(deep=True)

    def start_preview(self) -> None:
        with self._lock:
            if self._preview is None:
                self._preview = self._room.model_copy(deep=True)

    def go(self) -> None:
        with self._lock:
            if self._preview is not None:
                self._write(self._room_path, self._preview)
                self._room = self._preview
                self._preview = None

    def require_live_setup(self) -> None:
        with self._lock:
            if self._preview is not None:
                raise ValueError("Finish preview with Go before editing room definitions or calibration.")

    def set_room_file(self, document: RoomFile) -> RoomFile:
        with self._lock:
            self.require_live_setup()
            for kind, limit in (("targets", 24), ("zones", 16)):
                if len(getattr(document, kind)) > max(limit, len(getattr(self._room, kind))):
                    raise ValueError(f"The {kind} list is full (maximum {limit}); no entries were removed.")
            problems = room_problems(document) + assignment_problems(document, self._patch)
            if problems:
                raise ValueError("Invalid room data: " + "; ".join(problems) + ".")
            if self._definition_validator is not None:
                self._definition_validator(document)
            self._write(self._room_path, document)
            self._room = document.model_copy(deep=True)
            return self.room_file()

    def assignment(self, head_id: str, *, live: bool = False) -> HeadAssignment:
        self.head(head_id)
        with self._lock:
            return self.room_file(live=live).assignments.get(head_id, HeadAssignment()).model_copy(deep=True)

    def set_position_settings(
        self, assignments: dict[str, HeadAssignment],
        strips: Optional[dict[str, HeadAssignment]] = None,
    ) -> None:
        for head_id in assignments:
            self.head(head_id)
        with self._lock:
            document = self.room_file()
            document.assignments.update(assignments)
            document.strip_assignments.update(strips or {})
            problems = room_problems(document)
            if problems:
                raise ValueError("Invalid position settings: " + "; ".join(problems))
            if document == self.room_file():
                return
            if self.editing_preview() and self._preview is not None:
                self._preview = document
            else:
                self._write(self._room_path, document)
                self._room = document

    def set_assignments(self, assignments: dict[str, HeadAssignment]) -> None:
        """Replace the assignments of the given heads (others keep theirs); validated, saved."""
        self.set_position_settings(assignments)

    def save_zone(self, zone: Zone) -> Zone:
        """Create or replace a zone by id."""
        with self._lock:
            zones = [z for z in self._room.zones if z.id != zone.id]
            index = next((i for i, z in enumerate(self._room.zones) if z.id == zone.id), len(zones))
            zones.insert(index, zone)
            self.set_room_file(self._room.model_copy(update={"zones": zones}))
            return zone

    def delete_zone(self, zone_id: str) -> None:
        """Delete a zone and remove it from every head that used it."""
        with self._lock:
            self._room.zone(zone_id)
            def cleaned(settings):
                return {
                    head_id: a.model_copy(
                        update={
                            "aim_zone": None if a.aim_zone == zone_id else a.aim_zone,
                            "include_zones": [z for z in a.include_zones if z != zone_id],
                            "exclude_zones": [z for z in a.exclude_zones if z != zone_id],
                        }
                    )
                    for head_id, a in settings.items()
                }
            self.set_room_file(
                self._room.model_copy(
                    update={
                        "zones": [z for z in self._room.zones if z.id != zone_id],
                        "assignments": cleaned(self._room.assignments),
                        "strip_assignments": cleaned(self._room.strip_assignments),
                    }
                )
            )

    def delete_target(self, target_id: str) -> None:
        """Delete a target and clear it as the aim of every head that used it."""
        with self._lock:
            self.target(target_id)
            assignments = {
                head_id: a.model_copy(update={"aim_target": None}) if a.aim_target == target_id else a
                for head_id, a in self._room.assignments.items()
            }
            self.set_room_file(
                self._room.model_copy(
                    update={
                        "targets": [t for t in self._room.targets if t.id != target_id],
                        "assignments": {k: v for k, v in assignments.items() if v != HeadAssignment()},
                        "strip_assignments": {
                            owner: a.model_copy(update={"aim_target": None}) if a.aim_target == target_id else a
                            for owner, a in self._room.strip_assignments.items()
                        },
                    }
                )
            )

    def target(self, target_id: str) -> Target:
        with self._lock:
            for target in self._room.targets:
                if target.id == target_id:
                    return target
        raise KeyError(f"Target '{target_id}' does not exist.")

    def head_ids(self) -> list[str]:
        return [f.patch.id for f in self._patch.fixtures if is_moving_head(f)]

    def head(self, fixture_id: str) -> HeadSetup:
        """Setup of a moving head; heads without saved data get the unconfigured default."""
        if fixture_id not in self.head_ids():
            raise KeyError(f"'{fixture_id}' is not a patched moving head.")
        with self._lock:
            return self._heads.heads.get(fixture_id, HeadSetup())

    def set_head(self, fixture_id: str, setup: HeadSetup) -> HeadSetup:
        self.head(fixture_id)
        with self._lock:
            self.require_live_setup()
            updated = HeadsFile(heads={**self._heads.heads, fixture_id: setup})
            self._write(self._heads_path, updated)
            self._heads = updated
            return setup

    @staticmethod
    def _write(path: Optional[Path], document: BaseModel) -> None:
        if path is None:
            return
        try:
            write_json_atomic(path, document.model_dump(mode="json"))
        except OSError as error:
            raise PersistenceError(f"Could not save {path}: {error}") from error
