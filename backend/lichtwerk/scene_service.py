"""Scene recall and capture on top of the live fixture state."""

import logging
from collections.abc import Callable
from threading import Lock, RLock
from typing import Literal, Optional, Protocol, TYPE_CHECKING

from .fixture_service import FixtureController
from .rings import (
    expand_legacy_ring_attributes,
    expand_legacy_ring_values,
    ring_segments,
)
from .scenes import LookFx, Scene, SceneContent, SceneStore, look_route_references, resolve_scene
from .tempo import TempoClock

if TYPE_CHECKING:
    from .position_service import PositionService
    from .sequence_service import SequenceService

logger = logging.getLogger(__name__)


class LookHandler(Protocol):
    """Applies the FX part of looks; it knows the controller strips (see looks.py)."""

    def capture(self, fixture_ids: set[str]) -> LookFx: ...

    def prepare(self, scene: Scene) -> None: ...

    def apply(self, scene: Scene) -> list[str]: ...

    def start_overlay(self, scene: Scene, prefix: str) -> tuple[list[str], list[str]]: ...


class SceneService:
    def __init__(self, controller: FixtureController, store: SceneStore) -> None:
        self._controller = controller
        self._store = store
        self._lock = RLock()
        self._active: Optional[str] = None
        # While preview is on, recalls change the preview's scene; Go makes it the live one.
        self._previewing = False
        self._preview_scene: Optional[str] = None
        self._positions: Optional["PositionService"] = None
        self.sequences: Optional["SequenceService"] = None
        self.looks: Optional[LookHandler] = None
        # Called with the target values of every applied scene snapshot (e.g. fader pickup).
        self.snapshot_listeners: list[Callable[[dict[str, dict[str, float]]], None]] = []
        self._look_notice: Optional[str] = None
        self._notice_lock = Lock()  # leaf lock: also used by sequence steps under their lock
        self._owners: dict[str, dict[str, str]] = {}
        self._preview_owners: Optional[dict[str, dict[str, str]]] = None
        self._globals: dict[str, set[str]] = {}
        self._preview_globals: Optional[dict[str, set[str]]] = None
        self._tempo = TempoClock(controller._clock)
        controller.set_manual_filter(self.check_manual)

    def set_positions(self, positions: "PositionService") -> None:
        self._positions = positions
        self._store.position_validator = positions.validate_scene_positions
        self._store.route_validator = self._validate_look_routes
        for scene in self._store.all():
            self._store._check(scene)

    def _validate_look_routes(self, scene: SceneContent) -> None:
        if self._positions is None:
            return
        room = self._positions.rig.room_file()
        targets, zones = look_route_references(scene)
        missing = sorted(targets - {t.id for t in room.targets}) + sorted(zones - {z.id for z in room.zones})
        if missing:
            raise ValueError(
                f"Look '{scene.name}': its Move FX route uses missing targets/zones ({', '.join(missing)}). "
                "Restore them in the room or re-capture the look."
            )

    def _editing_owners(self) -> dict[str, dict[str, str]]:
        return self._preview_owners if self._controller.editing_preview() and self._preview_owners is not None else self._owners

    def locks(self) -> dict[str, list[str]]:
        # Ownership is bookkeeping, not a prohibition on normal operator controls.
        return {}

    def global_mask(self) -> dict[str, set[str]]:
        return self._preview_globals if self._controller.editing_preview() and self._preview_globals is not None else self._globals

    def globals(self) -> dict[str, list[str]]:
        return {key: sorted(value) for key, value in self.global_mask().items()}

    def set_globals(self, fixtures: dict[str, list[str]]) -> None:
        mask: dict[str, set[str]] = {}
        for fixture_id, names in fixtures.items():
            fixture = self._controller.patch.get(fixture_id)
            for name in names:
                if name not in fixture.mode.attributes():
                    raise ValueError(f"Fixture '{fixture_id}' has no attribute '{name}'.")
            mask[fixture_id] = expand_legacy_ring_attributes(fixture, set(names))
        with self._lock:
            self._controller.cancel_fades(mask)
            if self._controller.editing_preview():
                self._preview_globals = mask
            else:
                self._globals = mask

    def check_manual(self, changes: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
        overridden = {
            key: expand_legacy_ring_attributes(self._controller.patch.get(key), set(values))
            for key, values in changes.items()
        }
        if self.sequences is not None:
            self.sequences.override_attributes(overridden)
        owners = self._editing_owners()
        for fixture_id, values in changes.items():
            for name in values:
                owners.get(fixture_id, {}).pop(name, None)
        return changes

    def filtered(self, changes: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
        filtered: dict[str, dict[str, float]] = {}
        for fixture_id, values in changes.items():
            fixture = self._controller.patch.get(fixture_id)
            expanded = expand_legacy_ring_values(fixture, values)
            mask = self.global_mask().get(fixture_id, set())
            if len(ring_segments(fixture)) == 6:
                # Expanded segment values are authoritative during filtering; keeping
                # a uniform alias could reintroduce a masked section in controller.apply.
                for colour in ("red", "green", "blue"):
                    expanded.pop(f"ring_{colour}", None)
            filtered[fixture_id] = {
                name: value for name, value in expanded.items() if name not in mask
            }
        return filtered

    def claim(self, attributes: dict[str, set[str]], owner: str) -> None:
        owners = self._editing_owners()
        for fixture_id, names in attributes.items():
            owners.setdefault(fixture_id, {}).update({name: owner for name in names})

    def release_control(self, fixture_ids: Optional[list[str]] = None, attributes: Optional[list[str]] = None, *, owner: Optional[str] = None) -> None:
        with self._lock:
            owners = self._editing_owners()
            released: dict[str, set[str]] = {}
            for fixture_id, names in owners.items():
                if fixture_ids is not None and fixture_id not in fixture_ids:
                    continue
                selected = {name for name, value in names.items() if (attributes is None or name in attributes) and (owner is None or value == owner)}
                if attributes is not None:
                    expanded_attributes = expand_legacy_ring_attributes(
                        self._controller.patch.get(fixture_id), set(attributes)
                    )
                    selected.update(
                        name for name, value in names.items()
                        if name in expanded_attributes and (owner is None or value == owner)
                    )
                released[fixture_id] = selected
                for name in selected:
                    del names[name]
            self._controller.cancel_fades(released)
            if self.sequences is not None:
                self.sequences.release_attributes(released)

    def position_references(self, kind: str, item_id: str) -> list[str]:
        names = []
        for scene in self._referenced_scenes():
            targets, zones = look_route_references(scene)
            if item_id in (targets if kind in ("target", "targets") else zones):
                names.append(scene.name)
                continue
            for value in scene.positions.values():
                referenced = value.target_id == item_id if kind in ("target", "targets") else (
                    value.zone_id == item_id or item_id in value.included_zone_ids or item_id in value.excluded_zone_ids
                )
                if referenced:
                    names.append(scene.name)
                    break
        return names

    def _referenced_scenes(self) -> list[Scene]:
        scenes = self._store.all()
        if self.sequences is not None:
            for run in (self.sequences._live, self.sequences._preview):
                if run is not None and run.status != "stopped":
                    scenes.extend(run.scenes)
        return scenes

    def validate_room(self, document) -> None:
        targets = {target.id for target in document.targets}
        zones = {zone.id for zone in document.zones}
        for scene in self._referenced_scenes():
            route_targets, route_zones = look_route_references(scene)
            missing = sorted(route_targets - targets) + sorted(route_zones - zones)
            if missing:
                raise ValueError(f"'{missing[0]}' is used by a Move FX route in look '{scene.name}'.")
            for value in scene.positions.values():
                if value.target_id is not None and value.target_id not in targets:
                    raise ValueError(f"Target '{value.target_id}' is referenced by look '{scene.name}'.")
                for zone in [value.zone_id, *value.included_zone_ids, *value.excluded_zone_ids]:
                    if zone is not None and zone not in zones:
                        raise ValueError(f"Zone '{zone}' is referenced by look '{scene.name}'.")
        if self.sequences is None or self._positions is None:
            return
        current = self._positions.rig.room_file(live=True)
        current_targets = {target.id: target for target in current.targets}
        current_zones = {zone.id: zone for zone in current.zones}
        next_targets = {target.id: target for target in document.targets}
        next_zones = {zone.id: zone for zone in document.zones}
        for run in (self.sequences._live, self.sequences._preview):
            if run is None or run.status == "stopped":
                continue
            destinations = [position for scene in run.scenes for position in scene.positions.values()]
            if not destinations:
                continue
            reason = f"Stop sequence '{run.definition.name}' before editing its destination geometry."
            if current.room != document.room:
                raise ValueError(reason)
            for position in destinations:
                if position.target_id is not None:
                    old = current_targets.get(position.target_id)
                    new = next_targets.get(position.target_id)
                    if old is None or new is None or old.point != new.point:
                        raise ValueError(reason)
                if position.zone_id is not None:
                    old = current_zones.get(position.zone_id)
                    new = next_zones.get(position.zone_id)
                    if old is None or new is None or old.model_dump(exclude={"name", "mode"}) != new.model_dump(exclude={"name", "mode"}):
                        raise ValueError(reason)

    def scene_attributes(self, scene: Scene) -> dict[str, set[str]]:
        attributes = {key: set(value) for key, value in resolve_scene(scene, self._controller.patch).items()}
        attributes = {
            fixture_id: expand_legacy_ring_attributes(
                self._controller.patch.get(fixture_id), names
            )
            for fixture_id, names in attributes.items()
        }
        for fixture_id, names in attributes.items():
            fixture = self._controller.patch.get(fixture_id)
            if len(ring_segments(fixture)) == 6:
                names.difference_update(
                    {"ring_red", "ring_green", "ring_blue"}
                )
        for fixture_id in scene.positions:
            if self._controller.patch.get(fixture_id).patch.enabled:
                attributes.setdefault(fixture_id, set()).update(("pan", "tilt"))
        return attributes

    def apply_snapshot(self, scene: Scene, fade: float, *, owner: str, claim: bool = True) -> None:
        changes = self.filtered(resolve_scene(scene, self._controller.patch))
        positions = {
            key: value.model_dump() for key, value in scene.positions.items()
            if self._controller.patch.get(key).patch.enabled
            and not self.global_mask().get(key, set()) & {"pan", "tilt"}
        }
        if positions and self._positions is None:
            raise ValueError("Scene positioning is not configured.")
        if positions:
            self._positions.validate_scene_positions(positions)
        with self._controller.controlled_writes(), self._controller.batch():
            if positions:
                self._positions.apply_scene_positions(positions, fade_seconds=fade, preview=self._controller.editing_preview())
                for fixture_id in positions:
                    changes.get(fixture_id, {}).pop("pan", None)
                    changes.get(fixture_id, {}).pop("tilt", None)
            self._controller.apply(changes, fade)
        if claim:
            self.claim(self.scene_attributes(scene), owner)
        for listener in self.snapshot_listeners:
            listener(changes)

    @property
    def store(self) -> SceneStore:
        return self._store

    def active_scene(self) -> Optional[str]:
        """Id of the most recently recalled scene (None at startup or after deletion).

        While preview is on, this is the scene recalled into the preview.
        """
        with self._lock:
            return self._preview_scene if self._previewing else self._active

    def live_scene(self) -> Optional[str]:
        """The scene that is live on the fixtures, also while preview is on."""
        with self._lock:
            return self._active

    def start_preview(self) -> None:
        with self._lock:
            if not self._previewing:
                self._previewing = True
                self._preview_scene = self._active
                self._preview_owners = {key: dict(value) for key, value in self._owners.items()}
                self._preview_globals = {key: set(value) for key, value in self._globals.items()}

    def go(self) -> None:
        with self._lock:
            if self._previewing:
                self._active = self._preview_scene
                self._previewing = False
                self._preview_scene = None
                self._owners = self._preview_owners or {}
                self._preview_owners = None
                self._globals = self._preview_globals or {}
                self._preview_globals = None

    def _set_active(self, scene_id: Optional[str]) -> None:
        with self._lock:
            if self._previewing:
                self._preview_scene = scene_id
            else:
                self._active = scene_id

    def recall(self, scene_id: str, fade_seconds: Optional[float] = None) -> Scene:
        """Fade to the scene's values. The stored scene itself is never modified.

        A look (scene with an FX set) also switches strip modes before and starts its FX after
        the values. Parts that cannot run are reported in look_notice(); the rest still runs.
        """
        scene = self._store.get(scene_id)
        looks = self.looks if scene.fx is not None else None
        # Strip modes switch first, so the new strips' stored set points cannot override the look.
        # This happens outside the sequence lock (lock order: controller strips before sequences).
        if looks is not None:
            looks.prepare(scene)
        beat_fade = fade_seconds is None and scene.fade_timing == "beats"
        fade = (scene.fade_beats * 60 / self._tempo.bpm() if beat_fade else scene.fade_seconds) if fade_seconds is None else fade_seconds
        if self.sequences is not None:
            with self.sequences._lock:
                self.sequences.tick()
                self.apply_snapshot(scene, fade, owner=f"scene:{scene.id}")
                self.sequences.stop_if_overlap(self.scene_attributes(scene), advance=False)
        else:
            self.apply_snapshot(scene, fade, owner=f"scene:{scene.id}")
        if beat_fade and scene.fade_beats:
            self._controller.time_fades_in_beats(self.scene_attributes(scene), scene.fade_beats, self._tempo.beats)
        if looks is not None:
            self.report_look(scene, looks.apply(scene))
        else:
            self.report_look(scene, [])
        self._set_active(scene.id)
        logger.info("Scene '%s' recalled (fade %.1f s).", scene.id, fade)
        return scene

    def report_look(self, scene: Scene, blocked: list[str]) -> None:
        """Remember what the latest look recall or sequence step could not run."""
        notice = (f"Look '{scene.name}' partly blocked: " + "; ".join(blocked)) if blocked else None
        with self._notice_lock:
            self._look_notice = notice
        if notice:
            logger.warning("%s", notice)

    def look_notice(self) -> Optional[str]:
        """What the most recent recall could not run (None = everything ran)."""
        with self._notice_lock:
            return self._look_notice

    def deactivate(self, fade_seconds: Optional[float] = None) -> None:
        """Fade every dimmable fixture to intensity 0 and clear the active scene.

        Colours and positions stay as they are, and no stored scene is touched.
        """
        active = self.active_scene()
        if fade_seconds is None:
            fade_seconds = self._store.get(active).fade_seconds if active else 0.0
        changes = {
            f.patch.id: {"intensity": 0.0}
            for f in self._controller.fixtures()
            if f.patch.enabled and "intensity" in f.mode.attributes()
        }
        self.release_control()
        self._controller.apply(changes, fade_seconds)
        self._set_active(None)
        logger.info("Scene deactivated (fade %.1f s).", fade_seconds)

    def capture(
        self, name: str, fade_seconds: float, fixture_ids: Optional[list[str]] = None,
        group_ids: Optional[list[str]] = None, attributes: Optional[list[str]] = None,
        include_haze: bool = False, include_position: bool = False,
        fade_timing: Literal["seconds", "beats"] = "seconds", fade_beats: float = 1.0,
        include_fx: bool = False,
    ) -> Scene:
        """Store the current values of every enabled fixture (the preview's while preview is on)."""
        content = self.live_content(
            name, fade_seconds, fixture_ids, group_ids, attributes, include_haze,
            include_position, fade_timing, fade_beats, include_fx,
        )
        scene = self._store.create(content)
        logger.info("Scene '%s' captured from live state.", scene.id)
        return scene

    def live_content(
        self, name: str, fade_seconds: float, fixture_ids: Optional[list[str]] = None,
        group_ids: Optional[list[str]] = None, attributes: Optional[list[str]] = None,
        include_haze: bool = False, include_position: bool = False,
        fade_timing: Literal["seconds", "beats"] = "seconds", fade_beats: float = 1.0,
        include_fx: bool = False,
    ) -> SceneContent:
        """The current state as an unsaved scene; with include_fx it is a look with the running FX.

        Logical values only: master, blackout, FLASH and strobe holds are render-time and never captured.
        """
        selected = set(fixture_ids) if fixture_ids is not None else None
        if group_ids is not None:
            if selected is None:
                selected = set()
            for group_id in group_ids:
                selected.update(member.patch.id for member in self._controller.patch.group(group_id))
        for fixture_id in selected or ():
            self._controller.patch.get(fixture_id)
        snapshot = self._controller.snapshot()
        if attributes is not None:
            supported = {
                name for fixture_id, state in snapshot.items()
                if selected is None or fixture_id in selected
                for name in state
            }
            unknown = set(attributes) - supported
            if unknown:
                raise ValueError(f"No selected fixture supports attributes: {', '.join(sorted(unknown))}.")
        values = {}
        for fixture_id, state in snapshot.items():
            if selected is not None and fixture_id not in selected:
                continue
            is_hazer = "pump" in state or "fan" in state
            if is_hazer and not include_haze:
                continue
            values[fixture_id] = {key: value for key, value in state.items() if attributes is None or key in attributes}
        values = {key: value for key, value in values.items() if value}
        positions = {}
        if include_position and self._positions is not None:
            positions = self._positions.capture_scene_positions(list(values))
            for fixture_id in positions:
                values[fixture_id].pop("pan", None)
                values[fixture_id].pop("tilt", None)
        fx = None
        if include_fx:
            if self.looks is None:
                raise ValueError("Capturing FX needs the controller strips; it is not available here.")
            fx = self.looks.capture(set(values))
        return SceneContent(
            name=name, fade_seconds=fade_seconds, fade_timing=fade_timing,
            fade_beats=fade_beats, fixtures=values, positions=positions, fx=fx,
        )

    def delete(self, scene_id: str) -> None:
        self._store.delete(scene_id)
        with self._lock:
            if self._active == scene_id:
                self._active = None
            if self._preview_scene == scene_id:
                self._preview_scene = None
        logger.info("Scene '%s' deleted.", scene_id)
