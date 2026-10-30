"""Rest-on-startup must not masquerade as restored aiming or prevent FX launch."""

import pytest
from fastapi.testclient import TestClient

from lichtwerk import apc40 as hw
from lichtwerk.api import create_app
from lichtwerk.config import AppConfig
from lichtwerk.position_service import PositionService
from lichtwerk.positioning import HeadSetup
from lichtwerk.room import HeadAssignment, RigStore
from test_apc40 import club_patch
from test_zones import HEAD, HEAD2, calibrated, make_controller, make_rig


@pytest.mark.parametrize("included", [["floor-area"], ["floor-area", "booth"]])
@pytest.mark.parametrize("preview", [False, True])
def test_restart_rest_retains_permissions_without_false_aim_and_can_launch_fx(tmp_path, included, preview):
    patch = club_patch()
    rig = make_rig(patch, tmp_path)
    for head in (HEAD, HEAD2):
        setup = calibrated().model_dump()
        setup["rest"] = {"pan": 0.5, "tilt": 0.9}
        rig.set_head(head, HeadSetup.model_validate(setup))
    assignment = HeadAssignment(aim_zone="floor-area", include_zones=included)
    rig.set_position_settings(
        {HEAD: assignment, HEAD2: assignment}, {"apc40:2": assignment},
    )
    # Reload exactly the committed room/head files as a real restart does.
    rig = RigStore.load(tmp_path / "room.json", tmp_path / "heads.json", patch)
    app = create_app(AppConfig(universes=[5]), patch=patch, rig_store=rig)
    with TestClient(app) as client:
        def note(number, value=127, channel=1):
            response = client.post("/api/apc40/event", json={
                "kind": "note", "channel": channel, "number": number, "value": value,
            })
            assert response.status_code == 200
            return response.json()
        controller = app.state.fixture_controller
        assert controller.state(HEAD)["tilt"] == 0.9
        note(hw.NOTE_SHIFT)
        note(hw.NOTE_TRACK_SELECT, channel=2)
        view = note(hw.NOTE_SHIFT, 0)
        assert view["pads"][25]["included"]
        assert not view["pads"][25]["active"]
        assert controller.state(HEAD)["tilt"] == 0.9  # opening menu must not move
        if preview:
            assert client.post("/api/preview", json={"active": True}).status_code == 200
        note(hw.NOTE_SCENE_LAUNCH_FIRST + 2)
        view = note(hw.pad_note(1))
        assert view["error"] is None
        assert view["strips"][1]["fx_running"]
        assert rig.room_file().strip_assignments["apc40:2"] == assignment
        if preview:
            assert controller.live_state(HEAD)["tilt"] == 0.9
            assert client.post("/api/preview", json={"active": False}).status_code == 200
        note(hw.NOTE_SHIFT)
        note(hw.NOTE_TRACK_SELECT, channel=2)
        view = note(hw.NOTE_SHIFT, 0)
        assert view["pads"][25]["active"]


def test_included_zones_do_not_admit_movement_around_forbidden_target(tmp_path):
    patch = club_patch()
    rig = make_rig(patch, tmp_path)
    controller, _ = make_controller(patch, rig)
    positions = PositionService(controller, rig)
    rig.set_assignments({HEAD: HeadAssignment(
        aim_target="clear", include_zones=["floor-area"],
    )})
    assert not positions.movement_allowed(HEAD)
    rig.set_assignments({HEAD: HeadAssignment(include_zones=["floor-area"])})
    assert positions.movement_allowed(HEAD)
    assert not positions.movement_allowed(HEAD2)  # missing measurement/calibration
