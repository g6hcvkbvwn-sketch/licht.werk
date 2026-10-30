"""Named scene destinations share calibration and never widen permissions implicitly."""

import pytest

from lichtwerk.position_service import PositionService
from lichtwerk.positioning import NotConfiguredError
from lichtwerk.room import HeadAssignment
from test_zones import HEAD, HEAD2, make_controller, make_patch, make_rig


def services():
    patch = make_patch()
    rig = make_rig(patch)
    controller, _ = make_controller(patch, rig)
    positions = PositionService(controller, rig)
    return rig, controller, positions


def test_zone_scene_replaces_permissions_and_updates_active_strip_without_other_heads():
    rig, controller, positions = services()
    positions.initialize_strips({"apc40:2": "moving-heads"}, {"apc40:2": "moving-heads"})
    old_second = rig.assignment(HEAD2)
    positions.apply_scene_positions({HEAD: {
        "zone_id": "floor-area", "spot": {"u": 0.25, "v": 0.75},
        "included_zone_ids": ["floor-area"], "excluded_zone_ids": ["booth"],
    }})
    assert rig.assignment(HEAD).include_zones == ["floor-area"]
    assert rig.assignment(HEAD).exclude_zones == ["booth"]
    assert positions.spot(HEAD) == (0.25, 0.75)
    assert rig.assignment(HEAD2) == old_second
    assert positions.assignment_aim_reached(HEAD)
    assert positions.strip_assignment("apc40:2").aim_zone is None  # mixed head aims


def test_scene_validates_all_destinations_before_mutating_state():
    rig, controller, positions = services()
    before_room = rig.room_file()
    before = controller.state(HEAD)
    with pytest.raises(NotConfiguredError):
        positions.apply_scene_positions({
            HEAD: {"zone_id": "floor-area", "included_zone_ids": ["floor-area"]},
            HEAD2: {"zone_id": "floor-area", "included_zone_ids": ["floor-area"]},
        })
    assert rig.room_file() == before_room
    assert controller.state(HEAD) == before


def test_live_scene_position_changes_do_not_leak_into_preview_or_vice_versa():
    rig, controller, positions = services()
    controller.start_preview()
    positions.start_preview()
    positions.apply_scene_positions({HEAD: {
        "zone_id": "floor-area", "spot": {"u": 0.2, "v": 0.8},
        "included_zone_ids": ["floor-area"],
    }}, preview=True)
    staged = controller.state(HEAD)
    assert rig.assignment(HEAD).aim_zone == "floor-area"
    assert rig.assignment(HEAD, live=True).aim_zone is None
    positions.apply_scene_positions({HEAD: {"target_id": "clear"}}, preview=False)
    assert rig.assignment(HEAD, live=True).aim_target == "clear"
    assert rig.assignment(HEAD).aim_target is None
    assert controller.state(HEAD) == staged
    assert positions.spot(HEAD) == (0.2, 0.8)
    positions.go()
    controller.go()
    assert rig.assignment(HEAD).aim_zone == "floor-area"
    assert positions.spot(HEAD) == (0.2, 0.8)


def test_named_target_keeps_permissions_and_has_no_raw_pan_tilt_capture():
    rig, _, positions = services()
    rig.set_assignments({HEAD: HeadAssignment(include_zones=["floor-area"])})
    positions.apply_scene_positions({HEAD: {"target_id": "clear"}})
    assert rig.assignment(HEAD).include_zones == ["floor-area"]
    capture = positions.capture_scene_positions([HEAD])
    assert capture[HEAD]["target_id"] == "clear"
    assert "pan" not in capture[HEAD]
    with pytest.raises(ValueError, match="missing zone"):
        positions.validate_scene_positions({HEAD: {"zone_id": "missing"}})
