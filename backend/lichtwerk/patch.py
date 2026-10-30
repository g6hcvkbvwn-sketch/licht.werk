"""Patched fixture instances and patch validation (address range, overflow, collisions)."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

from typing import Annotated

from pydantic import (
    BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, field_validator,
)

from .dmx import DMX_SLOT_COUNT
from .fixtures import (
    BLACKOUT_KEEP_ATTRIBUTES, ID_PATTERN, MOTION_ATTRIBUTES, FixtureDefinition, FixtureMode,
)

GroupName = Annotated[str, Field(pattern=ID_PATTERN)]


def validate_display_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("A display name must contain 1–80 characters without control characters.")
    return name


class DisplayNames(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fixtures: dict[str, str] = Field(default_factory=dict)
    groups: dict[str, str] = Field(default_factory=dict)

    @field_validator("fixtures", "groups")
    @classmethod
    def valid_names(cls, names: dict[str, str]) -> dict[str, str]:
        return {key: validate_display_name(name) for key, name in names.items()}


class PatchedFixture(BaseModel):
    """A physical fixture: which definition/mode it uses and where it sits in DMX."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1)
    definition: str
    mode: str
    universe: StrictInt = Field(ge=0, le=7)
    start_address: StrictInt = Field(ge=1, le=DMX_SLOT_COUNT)
    enabled: StrictBool = True
    groups: list[GroupName] = Field(default_factory=list)


class PatchFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fixtures: list[PatchedFixture] = Field(default_factory=list)


@dataclass(frozen=True)
class ResolvedFixture:
    """A patched fixture joined with its definition and mode."""

    patch: PatchedFixture
    definition: FixtureDefinition
    mode: FixtureMode

    @property
    def end_address(self) -> int:
        return self.patch.start_address + self.mode.channel_count - 1


@dataclass(frozen=True)
class Patch:
    fixtures: tuple[ResolvedFixture, ...] = ()
    _group_names: dict[str, str] = field(default_factory=dict, compare=False, repr=False)
    _name_path: Path | None = field(default=None, compare=False, repr=False)
    _name_lock: RLock = field(default_factory=RLock, compare=False, repr=False)

    def configure_names(self, path: Path | None) -> None:
        """Load optional display-name overrides without changing fixture/group IDs."""
        with self._name_lock:
            if path is not None and path.exists():
                try:
                    names = DisplayNames.model_validate_json(path.read_text(encoding="utf-8"))
                except (OSError, ValidationError) as error:
                    raise ValueError(f"Invalid display-name file {path}: {error}") from error
                unknown_fixtures = names.fixtures.keys() - {f.patch.id for f in self.fixtures}
                unknown_groups = names.groups.keys() - self.groups().keys()
                if unknown_fixtures or unknown_groups:
                    raise ValueError(
                        f"Invalid display-name file {path}: unknown fixtures "
                        f"{sorted(unknown_fixtures)} or groups {sorted(unknown_groups)}."
                    )
                for fixture_id, name in names.fixtures.items():
                    self.get(fixture_id).patch.name = name
                self._group_names.update(names.groups)
            object.__setattr__(self, "_name_path", path)

    def group_name(self, group_id: str) -> str:
        self.group(group_id)
        return self._group_names.get(group_id, group_id.replace("-", " ").title())

    def _save_names(self, fixtures: dict[str, str], groups: dict[str, str]) -> None:
        if self._name_path is not None:
            from .scenes import write_json_atomic

            try:
                write_json_atomic(self._name_path, {"fixtures": fixtures, "groups": groups})
            except OSError as error:
                raise ValueError(f"Could not save display names to {self._name_path}: {error}") from error

    def rename_fixture(self, fixture_id: str, name: str) -> None:
        with self._name_lock:
            fixture = self.get(fixture_id)
            name = validate_display_name(name)
            names = {f.patch.id: f.patch.name for f in self.fixtures}
            names[fixture_id] = name
            self._save_names(names, dict(self._group_names))
            fixture.patch.name = name

    def rename_group(self, group_id: str, name: str) -> None:
        with self._name_lock:
            self.group(group_id)
            name = validate_display_name(name)
            groups = {**self._group_names, group_id: name}
            self._save_names({f.patch.id: f.patch.name for f in self.fixtures}, groups)
            self._group_names[group_id] = name

    def get(self, fixture_id: str) -> ResolvedFixture:
        for fixture in self.fixtures:
            if fixture.patch.id == fixture_id:
                return fixture
        raise KeyError(f"Fixture '{fixture_id}' is not patched.")

    def groups(self) -> dict[str, tuple[ResolvedFixture, ...]]:
        """Group name -> member fixtures, from the `groups` lists in the patch."""
        members: dict[str, list[ResolvedFixture]] = {}
        for fixture in self.fixtures:
            for name in dict.fromkeys(fixture.patch.groups):
                members.setdefault(name, []).append(fixture)
        return {name: tuple(fixtures) for name, fixtures in sorted(members.items())}

    def motion_channels(self) -> dict[int, frozenset[int]]:
        """Universe -> DMX channels carrying pan, tilt or movement speed."""
        return self._attribute_channels(MOTION_ATTRIBUTES)

    def blackout_keep_channels(self) -> dict[int, frozenset[int]]:
        """Motion and non-lighting pump/fan channels remain unchanged in blackout."""
        return self._attribute_channels(BLACKOUT_KEEP_ATTRIBUTES)

    def _attribute_channels(self, attributes: tuple[str, ...]) -> dict[int, frozenset[int]]:
        channels: dict[int, set[int]] = {}
        for fixture in self.fixtures:
            for offset, channel in enumerate(fixture.mode.channels):
                if channel.attribute in attributes:
                    channels.setdefault(fixture.patch.universe, set()).add(
                        fixture.patch.start_address + offset
                    )
        return {universe: frozenset(found) for universe, found in channels.items()}

    def group(self, name: str) -> tuple[ResolvedFixture, ...]:
        try:
            return self.groups()[name]
        except KeyError as error:
            raise KeyError(f"Group '{name}' does not exist in the patch.") from error


def build_patch(
    entries: list[PatchedFixture],
    library: dict[str, FixtureDefinition],
    universes: list[int],
) -> Patch:
    """Validate a patch and resolve definitions. Reports every problem found at once.

    Disabled fixtures still occupy their addresses, so they take part in collision checks.
    """
    problems: list[str] = []
    resolved: list[ResolvedFixture] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if entry.id in seen_ids:
            problems.append(f"Duplicate fixture id '{entry.id}'.")
            continue
        seen_ids.add(entry.id)
        definition = library.get(entry.definition)
        if definition is None:
            problems.append(f"Fixture '{entry.id}': unknown fixture definition '{entry.definition}'.")
            continue
        try:
            mode = definition.mode(entry.mode)
        except KeyError as error:
            problems.append(f"Fixture '{entry.id}': {error.args[0]}")
            continue
        if entry.universe not in universes:
            problems.append(
                f"Fixture '{entry.id}': universe {entry.universe} is not a configured universe."
            )
        fixture = ResolvedFixture(entry, definition, mode)
        if fixture.end_address > DMX_SLOT_COUNT:
            problems.append(
                f"Fixture '{entry.id}' needs addresses {entry.start_address}-{fixture.end_address} "
                f"but a universe ends at {DMX_SLOT_COUNT}."
            )
            continue
        resolved.append(fixture)

    by_universe: dict[int, list[ResolvedFixture]] = {}
    for fixture in resolved:
        by_universe.setdefault(fixture.patch.universe, []).append(fixture)
    for universe, fixtures in sorted(by_universe.items()):
        ordered = sorted(fixtures, key=lambda f: f.patch.start_address)
        reach = ordered[0]
        for current in ordered[1:]:
            if current.patch.start_address <= reach.end_address:
                problems.append(
                    f"Address collision in universe {universe}: '{reach.patch.id}' "
                    f"({reach.patch.start_address}-{reach.end_address}) overlaps "
                    f"'{current.patch.id}' ({current.patch.start_address}-{current.end_address})."
                )
            if current.end_address > reach.end_address:
                reach = current

    if problems:
        raise ValueError("Invalid patch:\n- " + "\n- ".join(problems))
    return Patch(tuple(resolved))


def load_patch(
    path: Path, library: dict[str, FixtureDefinition], universes: list[int]
) -> Patch:
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Patch file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in patch file {path} at line {error.lineno}: {error.msg}"
        ) from error
    try:
        patch_file = PatchFile.model_validate(values)
    except ValidationError as error:
        raise ValueError(f"Invalid patch file {path}: {error}") from error
    try:
        return build_patch(patch_file.fixtures, library, universes)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error
