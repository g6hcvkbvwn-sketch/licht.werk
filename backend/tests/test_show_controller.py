"""Revised show-control gestures, using only simulated output."""

import pytest
from fastapi.testclient import TestClient

from lichtwerk import apc40 as hw
from lichtwerk.api import create_app
from lichtwerk.apc40_pads import PadAssignment, PadStore, SequencePadAssignment
from lichtwerk.config import AppConfig
from test_apc40 import club_patch
from test_zones import HEAD, HEAD2, make_rig


def test_scene_and_sequence_pad_assignments_persist_independently(tmp_path):
    path = tmp_path / "apc40.json"
    pads = PadStore(path)
    pads.assign(PadAssignment(pad=0, scene="look", colour="green"))
    pads.assign_sequence(SequencePadAssignment(pad=0, sequence="tour", colour="blue"))
    restored = PadStore.load(path)
    assert restored.get(0).scene == "look"
    assert restored.get_sequence(0).sequence == "tour"
    restored.clear(0)
    assert PadStore.load(path).get_sequence(0).sequence == "tour"
    restored.clear_sequence(0)
    assert PadStore.load(path).get_sequence(0) is None


def test_sequence_controller_page_launch_pause_resume_restart_stop_and_edit():
    app = create_app(AppConfig(universes=[5]), patch=club_patch())
    with TestClient(app) as client:
        scene = client.post("/api/scenes", json={
            "name": "Pad look", "fixtures": {"led-bar-1": {"intensity": 0.3}},
        }).json()
        response = client.post("/api/sequences", json={
            "name": "Pad tour", "loop": True,
            "steps": [{"scene_id": scene["id"], "hold": 20.0}],
        })
        assert response.status_code == 201, response.text
        sequence = response.json()
        assert client.put("/api/apc40/sequence-pads/0", json={
            "sequence": sequence["id"], "colour": "blue",
        }).status_code == 200

        def note(number, value=127):
            view = client.post("/api/apc40/event", json={
                "kind": "note", "channel": 1, "number": number, "value": value,
            }).json()
            assert view["error"] is None, view["error"]
            return view

        view = note(hw.NOTE_DEVICE_RIGHT)
        assert view["pad_mode"] == "sequences"
        assert view["pads"][0]["sequence"] == sequence["id"]
        view = note(hw.pad_note(0))
        assert view["pads"][0]["active"]
        assert client.get("/api/show").json()["sequence"]["status"] == "running"
        note(hw.pad_note(0))
        assert client.get("/api/show").json()["sequence"]["status"] == "paused"
        note(hw.NOTE_PLAY)
        assert client.get("/api/show").json()["sequence"]["status"] == "running"
        note(hw.NOTE_SHIFT)
        view = note(hw.pad_note(0))
        assert view["show_dialog"]["item"] == sequence["id"]
        assert view["show_dialog"]["kind"] == "sequence"
        note(hw.NOTE_PLAY)
        note(hw.NOTE_SHIFT, 0)
        assert client.get("/api/show").json()["sequence"]["step_index"] == 0
        note(hw.NOTE_RECORD)
        stopped = client.get("/api/show").json()
        assert stopped["sequence"]["status"] == "stopped"
        assert stopped["locks"] == {}
        view = note(hw.pad_note(1))
        assert view["show_dialog"]["kind"] == "sequence"
        assert view["show_dialog"]["item"] is None
        assert note(hw.NOTE_DEVICE_LEFT)["pad_mode"] == "scenes"


@pytest.mark.parametrize("preview", [False, True])
def test_permission_pads_never_move_and_aim_preserves_permissions(tmp_path, preview):
    patch = club_patch()
    rig = make_rig(patch, tmp_path)
    rig.set_head(HEAD2, rig.head(HEAD))
    app = create_app(AppConfig(universes=[5]), patch=patch, rig_store=rig)
    with TestClient(app) as client:
        def note(number, value=127, channel=1):
            response = client.post("/api/apc40/event", json={
                "kind": "note", "channel": channel, "number": number, "value": value,
            })
            assert response.status_code == 200
            return response.json()

        if preview:
            client.post("/api/preview", json={"active": True}).raise_for_status()
        note(hw.NOTE_SHIFT)
        view = note(hw.NOTE_TRACK_SELECT, channel=2)
        note(hw.NOTE_SHIFT, 0)
        assert view["position_mode"] == "aim"
        controller = app.state.fixture_controller
        before = controller.state(HEAD)
        note(hw.NOTE_DEVICE_LOCK)
        for expected in ("included", "excluded", "neither"):
            view = note(hw.pad_note(25))
            assert view["error"] is None
            assert view["pads"][25]["included"] == (expected == "included")
            assert view["pads"][25]["explicitly_excluded"] == (expected == "excluded")
            assert controller.state(HEAD) == before
        note(hw.NOTE_DEVICE_ON_OFF)
        view = note(hw.pad_note(25))
        assert view["error"] is None
        assert rig.assignment(HEAD).aim_zone == "floor-area"
        assert rig.assignment(HEAD).include_zones == []
        assert rig.assignment(HEAD).exclude_zones == []
        assert not view["pads"][25]["included"]
        after = controller.state(HEAD)
        note(hw.NOTE_SHIFT)
        view = note(hw.pad_note(25))
        note(hw.NOTE_SHIFT, 0)
        assert controller.state(HEAD) == after
        if preview:
            assert "Finish preview" in view["error"]
        else:
            assert view["position_dialog_kind"] == "zone"
            assert view["position_dialog_item"] == "floor-area"
        note(hw.NOTE_DEVICE_LOCK)
        note(hw.NOTE_SHIFT)
        note(hw.NOTE_TRACK_SELECT, channel=2)  # close
        view = note(hw.NOTE_TRACK_SELECT, channel=2)  # reopen defaults to Aim
        assert view["position_mode"] == "aim"
