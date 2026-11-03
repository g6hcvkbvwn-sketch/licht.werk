"""RFC 8/11: fail-closed areas, static-target exceptions and staged strip settings."""

import json

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from lichtwerk.api import create_app
from lichtwerk.config import AppConfig
from lichtwerk.effect_engine import EffectEngine
from lichtwerk.fixture_service import FixtureController
from lichtwerk.fixtures import load_fixture_library
from lichtwerk.config import DEFAULT_FIXTURES_DIR
from lichtwerk.output import SimulatedDmxOutput
from lichtwerk.patch import PatchedFixture, build_patch
from lichtwerk.rig_api import build_router
from lichtwerk.service import LightingService
from lichtwerk.effects import MOVEMENT_TYPES
from lichtwerk.position_service import PositionService
from lichtwerk.preview import PreviewService
from lichtwerk.room import HeadAssignment, RigStore, RoomFile, Target
from lichtwerk.scene_service import SceneService
from lichtwerk.scenes import SceneStore
from lichtwerk.tempo import TempoClock
from lichtwerk.zones import Zone

from test_zones import (
    ALL_FLOOR, EXCLUDE, HEAD, HEAD2, INCLUDE, INTENSITY,
    calibrated, make_controller, make_patch, make_rig,
)


def system(path=None, clock=None):
    patch = make_patch()
    rig = make_rig(patch, path)
    controller, lighting = make_controller(patch, rig, clock)
    positions = PositionService(controller, rig)
    return rig, controller, lighting, positions


def test_union_subtracts_exclusion_and_undefined_areas_are_dark():
    rig, controller, lighting, _ = system()
    right = Zone(
        id="right", name="Right", surface="floor", corner={"x": 4.0, "y": 2.0},
        length=2.0, width=2.0,
    )
    rig.save_zone(right)
    rig.set_assignments({HEAD: HeadAssignment(
        include_zones=[INCLUDE.id, right.id], exclude_zones=[EXCLUDE.id],
    )})
    for tilt, expected in ((0.5, 255), (0.6, 0), (0.7, 255), (0.9, 0)):
        controller.apply({HEAD: {"pan": 0.0, "tilt": tilt, "intensity": 1.0}}, 0)
        assert lighting.read_output(5)[INTENSITY] == expected
        assert controller.output_state(HEAD)["intensity"] == expected / 255
        assert controller.state(HEAD)["intensity"] == 1.0
    rig.set_assignments({HEAD: HeadAssignment()})
    controller.apply({HEAD: {"tilt": 0.5}}, 0)
    assert lighting.read_output(5)[INTENSITY] == 0


def test_new_zone_is_excluded_for_active_and_saved_strips_and_overlap_stays_dark(tmp_path):
    rig, controller, lighting, positions = system(tmp_path)
    positions.initialize_strips({"group": "moving-heads", "single": HEAD}, {"group": "moving-heads"})
    included = HeadAssignment(aim_zone=INCLUDE.id)
    positions.set_strip_assignment("group", "moving-heads", included)
    positions.set_strip_assignment("single", HEAD, included)
    controller.apply({HEAD: {"intensity": 1.0}}, 0)
    assert lighting.read_output(5)[INTENSITY] == 255
    before = controller.snapshot()
    overlap = INCLUDE.model_copy(update={"id": "new-area", "name": "New area"})
    rig.save_zone(overlap)
    controller.refresh_all()
    assert controller.snapshot() == before
    assert lighting.read_output(5)[INTENSITY] == 0
    assert "new-area" in rig.assignment(HEAD).exclude_zones
    assert all("new-area" in positions.strip_assignment(owner).exclude_zones for owner in ("group", "single"))
    restored = RigStore.load(tmp_path / "room.json", tmp_path / "heads.json", controller.patch)
    assert restored.room_file().strip_assignments == rig.room_file().strip_assignments
    positions.set_strip_assignment("group", "moving-heads", HeadAssignment(
        aim_zone=INCLUDE.id, include_zones=[INCLUDE.id, "new-area"],
    ), recall=False)
    assert lighting.read_output(5)[INTENSITY] == 255


def test_legacy_neutral_zones_load_as_excluded_without_rewriting_file(tmp_path):
    patch = make_patch()
    rig = make_rig(patch, tmp_path, assignments={HEAD: HeadAssignment(include_zones=[INCLUDE.id])})
    document = rig.room_file()
    # An old file can omit zones from both permission lists.
    document.assignments[HEAD].exclude_zones = []
    path = tmp_path / "room.json"
    original = json.dumps(document.model_dump(mode="json"))
    path.write_text(original)
    loaded = RigStore.load(path, tmp_path / "heads.json", patch)
    assert loaded.assignment(HEAD).exclude_zones == ["booth"]
    assert path.read_text() == original


def test_missing_geometry_and_flash_fail_closed_but_override_is_explicit():
    rig, controller, lighting, _ = system()
    controller.apply({HEAD: {"pan": 0.0, "tilt": 0.5, "intensity": 1.0}}, 0)
    controller.hold("flash", {HEAD: {"intensity": 1.0}})
    assert lighting.read_output(5)[INTENSITY] == 0
    controller.guard.set_setup_override(HEAD, True)
    controller.refresh(HEAD)
    assert lighting.read_output(5)[INTENSITY] == 255
    assert "OVERRIDE" in controller.guard.reason(HEAD, 0.0, 0.5)
    controller.guard.set_setup_override(HEAD, False)
    controller.refresh(HEAD)
    assert lighting.read_output(5)[INTENSITY] == 0
    controller.apply({HEAD2: {"intensity": 1.0}}, 0)
    assert controller.output_state(HEAD2)["intensity"] == 0
    assert controller.guard.reason(HEAD2, 0.0, 0.5) == "head position not measured"


def test_static_target_exception_only_at_endpoint_not_fade_or_manual_or_scene(clock):
    rig, controller, lighting, positions = system(clock=clock)
    rig.set_assignments({HEAD: HeadAssignment(exclude_zones=["booth"])})
    controller.apply({HEAD: {"pan": 0.0, "tilt": 0.5, "intensity": 1.0}}, 0)
    positions.aim_target(HEAD, "in-zone", 2)
    assert not positions.movement_allowed(HEAD)
    clock.now += 1
    controller.tick()
    assert lighting.read_output(5)[INTENSITY] == 0
    clock.now += 1
    controller.tick()
    assert lighting.read_output(5)[INTENSITY] == 255
    controller.hold("flash", {HEAD: {"intensity": 1.0, "tilt": 0.5}})
    assert lighting.read_output(5)[INTENSITY] == 0
    controller.release("flash")
    assert lighting.read_output(5)[INTENSITY] == 255
    controller.set_values(HEAD, {"pan": controller.state(HEAD)["pan"]})
    assert lighting.read_output(5)[INTENSITY] == 0
    assert rig.assignment(HEAD).aim_target is None
    positions.aim_target(HEAD, "in-zone", 0)
    controller.apply({HEAD: {"tilt": controller.state(HEAD)["tilt"]}}, 0)  # scene path
    assert lighting.read_output(5)[INTENSITY] == 0


def test_movement_effect_cannot_borrow_static_target_exception_even_at_zero_displacement(clock):
    _, controller, lighting, positions = system(clock=clock)
    controller.apply({HEAD: {"intensity": 1.0}}, 0)
    positions.aim_target(HEAD, "in-zone", 0)
    effects = EffectEngine(controller.patch, TempoClock(clock=clock))
    controller.set_effects(effects)
    effect_type = next(iter(MOVEMENT_TYPES))
    effects.start("head", effect_type, 0, [HEAD])
    effects.set_param("head", effect_type, "size", 0.0)
    controller.refresh(HEAD)
    assert lighting.read_output(5)[INTENSITY] == 0
    effects.stop("head", effect_type)
    controller.refresh(HEAD)
    assert lighting.read_output(5)[INTENSITY] == 255


def test_selecting_forbidden_target_stops_previously_started_movement(clock):
    rig, controller, _, positions = system(clock=clock)
    rig.save_zone(ALL_FLOOR)
    rig.set_assignments({HEAD: HeadAssignment(include_zones=["all-floor"], exclude_zones=["booth"])})
    positions.aim_target(HEAD, "clear", 0)
    effects = EffectEngine(controller.patch, TempoClock(clock=clock))
    controller.set_effects(effects)
    effect_type = next(iter(MOVEMENT_TYPES))
    effects.start("head", effect_type, 0, [HEAD])
    assert effects.movement_active(HEAD)
    positions.aim_target(HEAD, "in-zone", 0)
    assert effects.running("head", effect_type) is None


def test_first_strip_edit_is_immediately_effective_without_prior_mode_switch():
    rig, _, _, positions = system()
    chosen = rig.room_file().permissions(HeadAssignment(aim_zone=INCLUDE.id))
    positions.set_strip_assignment("initial-group", "moving-heads", chosen)
    assert rig.assignment(HEAD) == rig.assignment(HEAD2) == chosen
    assert positions.strip_assignment("initial-group") == chosen


def test_strip_api_returns_independent_stored_and_effective_assignments():
    rig, controller, _, positions = system()
    positions.initialize_strips(
        {"apc40:2": "moving-heads", "apc40:3": HEAD, "apc40:4": HEAD2},
        {"apc40:2": "moving-heads"},
    )
    app = FastAPI()
    app.include_router(build_router(positions, rig, controller))
    with TestClient(app) as client:
        group = client.put("/api/positions/strips/apc40:2", json={
            "target": "moving-heads", "aim_zone": INCLUDE.id, "include_zones": [INCLUDE.id],
        })
        assert group.status_code == 200 and group.json()["active"]
        assert group.json()["assignment"]["include_zones"] == [INCLUDE.id]
        assert all(h["assignment"]["include_zones"] == [INCLUDE.id] for h in group.json()["heads"])
        single = client.put("/api/positions/strips/apc40:3", json={
            "target": HEAD, "aim_target": "in-zone", "exclude_zones": ["booth"],
        })
        assert single.status_code == 200 and not single.json()["active"]
        assert single.json()["assignment"]["aim_target"] == "in-zone"
        assert single.json()["heads"][0]["assignment"]["aim_zone"] == INCLUDE.id
        read = client.get("/api/positions/strips/apc40:2", params={"target": "moving-heads"})
        assert read.json()["assignment"] == group.json()["assignment"]
        positions.activate_strips({"apc40:3": HEAD, "apc40:4": HEAD2})
        read = client.get("/api/positions/strips/apc40:3", params={"target": HEAD})
        assert read.json()["active"]
        assert read.json()["heads"][0]["assignment"] == read.json()["assignment"]
        assert client.get("/api/positions/strips/apc40:3", params={"target": "missing"}).status_code == 404


def test_direct_controller_preserves_haze_and_motion_during_blackout():
    patch = build_patch([
        PatchedFixture(
            id=HEAD, name="Head", definition="equinox-fusion-orbit-mkii",
            mode="16ch", universe=5, start_address=1,
        ),
        PatchedFixture(
            id="hazer-1", name="Hazer", definition="look-unique-2-1",
            mode="2ch", universe=5, start_address=17,
        ),
    ], load_fixture_library(DEFAULT_FIXTURES_DIR), [5])
    lighting = LightingService(SimulatedDmxOutput(), (5,), blackout_active=True)
    controller = FixtureController(patch, lighting)
    controller.apply({HEAD: {"pan": 0.5, "tilt": 0.4, "intensity": 1.0},
                      "hazer-1": {"pump": 0.5, "fan": 1.0}}, 0)
    output = lighting.read_output(5)
    assert output[:2] == (128, 102)
    assert output[3] == 0
    assert output[16:18] == (128, 255)


def test_preview_stages_permissions_aims_and_disk_until_go(tmp_path):
    rig, controller, lighting, positions = system(tmp_path)
    rig.set_head(HEAD2, calibrated())
    positions.activate_strips({"single": HEAD})
    positions.set_strip_assignment("single", HEAD, HeadAssignment(aim_zone=INCLUDE.id))
    controller.apply({HEAD: {"intensity": 1.0}}, 0)
    before = lighting.read_output(5)
    committed = (tmp_path / "room.json").read_text()
    preview = PreviewService(controller, SceneService(controller, SceneStore(controller.patch)), positions=positions)
    preview.start()
    assignment = HeadAssignment(aim_target="in-zone", exclude_zones=["booth"])
    positions.set_strip_assignment("single", HEAD, assignment)
    assert positions.strip_assignment("single") == rig.room_file().permissions(assignment)
    assert rig.assignment(HEAD, live=True).aim_zone == INCLUDE.id
    assert rig.assignment(HEAD).aim_target == "in-zone"
    assert (tmp_path / "room.json").read_text() == committed
    assert lighting.read_output(5) == before
    assert controller.preview_output_state(HEAD)["intensity"] == 1.0
    with pytest.raises(ValueError, match="Finish preview"):
        rig.save_zone(ALL_FLOOR)
    with pytest.raises(ValueError, match="Finish preview"):
        positions.save_head(HEAD, calibrated())
    preview.go()
    saved = json.loads((tmp_path / "room.json").read_text())
    assert saved["strip_assignments"]["single"]["aim_target"] == "in-zone"
    assert rig.assignment(HEAD, live=True) == rig.room_file().permissions(assignment)
    assert lighting.read_output(5)[INTENSITY] == 255


def test_group_and_single_settings_remain_independent_and_survive_reload(tmp_path):
    rig, controller, _, positions = system(tmp_path)
    rig.set_head(HEAD2, calibrated())
    positions.activate_strips({"one": HEAD, "two": HEAD2})
    first = rig.room_file().permissions(HeadAssignment(aim_target="clear", include_zones=[INCLUDE.id]))
    second = rig.room_file().permissions(HeadAssignment(aim_target="in-zone", exclude_zones=["booth"]))
    positions.set_strip_assignment("one", HEAD, first)
    positions.set_strip_assignment("two", HEAD2, second)
    positions.activate_strips({"group": "moving-heads"})
    group = rig.room_file().permissions(HeadAssignment(aim_zone=INCLUDE.id))
    positions.set_strip_assignment("group", "moving-heads", group)
    assert rig.assignment(HEAD) == rig.assignment(HEAD2) == group
    assert positions.strip_assignment("one") == first
    assert positions.strip_assignment("two") == second
    positions.activate_strips({"one": HEAD, "two": HEAD2})
    assert rig.assignment(HEAD) == first
    assert rig.assignment(HEAD2) == second
    restored = RigStore.load(tmp_path / "room.json", tmp_path / "heads.json", controller.patch)
    assert restored.room_file().strip_assignments == rig.room_file().strip_assignments


def test_capacity_stable_order_and_legacy_load_never_truncate_or_rewrite(tmp_path):
    patch = make_patch()
    targets = [Target(id=f"target-{index}", name=f"Target {index}") for index in range(25)]
    document = RoomFile(targets=targets)
    room_path = tmp_path / "room.json"
    text = json.dumps(document.model_dump(mode="json"))
    room_path.write_text(text)
    rig = RigStore.load(room_path, tmp_path / "heads.json", patch)
    assert [t.id for t in rig.room_file().targets] == [t.id for t in targets]
    assert room_path.read_text() == text
    with pytest.raises(ValueError, match="full"):
        rig.set_room_file(document.model_copy(update={"targets": targets + [Target(id="new", name="New")]}))
    assert room_path.read_text() == text
    rig.save_zone(INCLUDE)
    rig.save_zone(ALL_FLOOR)
    rig.save_zone(INCLUDE.model_copy(update={"name": "Renamed"}))
    assert [zone.id for zone in rig.room_file().zones] == [INCLUDE.id, ALL_FLOOR.id]
    legacy = HeadAssignment.model_validate({"aim_zone": INCLUDE.id, "exclude_zones": ["booth"]})
    assert legacy.include_zones == [INCLUDE.id]
    assert HeadAssignment(aim_zone=INCLUDE.id, include_zones=[]).include_zones == []
    for index in range(14):
        rig.save_zone(INCLUDE.model_copy(update={"id": f"zone-{index}"}))
    with pytest.raises(ValueError, match="maximum 16"):
        rig.save_zone(INCLUDE.model_copy(update={"id": "overflow"}))


def test_initialization_seeds_group_and_singles_without_recalling_saved_aims(tmp_path):
    rig, controller, _, positions = system(tmp_path)
    original = {
        HEAD: HeadAssignment(aim_target="clear", include_zones=[INCLUDE.id]),
        HEAD2: HeadAssignment(aim_target="in-zone", exclude_zones=["booth"]),
    }
    original = {head: rig.room_file().permissions(value) for head, value in original.items()}
    rig.set_assignments(original)
    before = controller.snapshot()
    positions.initialize_strips(
        {"group": "moving-heads", "one": HEAD, "two": HEAD2},
        {"group": "moving-heads"},
    )
    assert controller.snapshot() == before
    assert positions.strip_assignment("one") == original[HEAD]
    assert positions.strip_assignment("two") == original[HEAD2]
    assert positions.strip_assignment("group").aim_target is None
    positions.set_strip_assignment("group", "moving-heads", HeadAssignment(aim_target="clear"))
    assert controller.state(HEAD) != before[HEAD]
    assert positions.strip_assignment("one") == original[HEAD]
    positions.activate_strips({"one": HEAD, "two": HEAD2})
    controller.set_values(HEAD, {"tilt": controller.state(HEAD)["tilt"]})
    assert positions.strip_assignment("one").aim_target is None
    assert positions.strip_assignment("two").aim_target == "in-zone"


def test_api_override_is_visible_and_map_returns_projected_beam(tmp_path):
    patch = make_patch()
    rig = make_rig(patch, tmp_path)
    app = create_app(AppConfig(universes=[5]), patch=patch, rig_store=rig)
    with TestClient(app) as client:
        info = client.put(f"/api/heads/{HEAD}/setup-override", json={"enabled": True})
        assert info.status_code == 200
        assert info.json()["setup_override"] is True
        assert "OVERRIDE" in info.json()["guard_reason"]
        client.post(f"/api/fixtures/{HEAD}/state", json={"values": {"pan": 0.0, "tilt": 0.5}})
        entry = next(f for f in client.get("/api/map").json()["fixtures"] if f["id"] == HEAD)
        assert entry["live_beam"] == pytest.approx([2.0, 3.0])
        assert entry["preview_beam"] == pytest.approx([2.0, 3.0])
        assert entry["setup_override"] is True
        assert client.post("/api/preview", json={"active": True}).status_code == 200
        response = client.put("/api/zones/all-floor", json=ALL_FLOOR.model_dump(mode="json"))
        assert response.status_code == 422 and "Finish preview" in response.json()["detail"]
        response = client.put(f"/api/heads/{HEAD}/setup-override", json={"enabled": False})
        assert response.status_code == 200 and response.json()["setup_override"] is False
        response = client.put(f"/api/heads/{HEAD}/setup-override", json={"enabled": True})
        assert response.status_code == 422 and "Finish preview" in response.json()["detail"]
