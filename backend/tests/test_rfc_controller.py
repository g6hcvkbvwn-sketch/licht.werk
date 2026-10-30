"""RFC controller workflow regressions; no MIDI ports or DMX hardware."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from lichtwerk import apc40 as hw
from lichtwerk.api import create_app
from lichtwerk.effects import EffectType
from lichtwerk.config import DEFAULT_FIXTURES_DIR, AppConfig
from lichtwerk.fixtures import load_fixture_library
from lichtwerk.midi_control import MidiEvent
from lichtwerk.patch import PatchedFixture, build_patch
from lichtwerk.room import HeadAssignment
from test_apc40 import Rig, club_patch
from test_zones import make_rig


def test_stop_all_is_per_selected_strip_including_suspended_effects():
    rig = Rig()
    rig.effects.start("apc40:1", EffectType.CHASE, 0, ["led-bar-1"])
    rig.effects.start("apc40:2", EffectType.CHASE, 0, ["moving-head-1"])
    rig.effects.set_active("apc40:2", False)
    rig.press(hw.NOTE_TRACK_SELECT, channel=2)
    rig.press(hw.NOTE_STOP_ALL_CLIPS)
    assert rig.apc.fx_track() == 2
    assert not rig.effects.owner_running("apc40:2")
    assert rig.effects.owner_running("apc40:1")


def test_stop_fx_in_preview_does_not_change_live_until_go():
    rig = Rig()
    rig.controller.set_values("led-bar-1", {"intensity": 1.0, "red": 1.0})
    rig.effects.start("apc40:1", EffectType.CHASE, 1, ["led-bar-1"])
    rig.clock.now += 60 / rig.tempo.bpm() * 0.6
    rig.preview.start()
    rig.press(hw.NOTE_TRACK_SELECT, channel=1)
    rig.press(hw.NOTE_STOP_ALL_CLIPS)
    rig.controller.tick()
    assert rig.service.read_output(5)[0] == 0
    assert not rig.effects.owner_running("apc40:1")  # editing view
    rig.preview.go()
    rig.controller.tick()
    assert rig.service.read_output(5)[0] == 255


def test_fx_led_blinks_outside_menu_and_editing_blinks_faster():
    rig = Rig()
    rig.effects.start("apc40:1", EffectType.CHASE, 0, ["led-bar-1"])
    key = ("note", 1, hw.NOTE_TRACK_SELECT)
    assert rig.apc.leds()[key] == hw.LED_ON
    rig.clock.now += 0.5
    assert rig.apc.leds()[key] == hw.LED_OFF
    rig.press(hw.NOTE_SCENE_LAUNCH_FIRST)
    rig.press(hw.NOTE_TRACK_SELECT, channel=1)
    first = rig.apc.leds()[key]
    rig.clock.now += 0.25
    assert rig.apc.leds()[key] != first


def test_metronome_follows_backend_phase_and_tap():
    rig = Rig()
    assert rig.tempo.bpm() == 128.0
    key = ("note", 1, hw.NOTE_METRONOME)
    assert rig.apc.leds()[key] == hw.LED_ON
    rig.clock.now += 60 / 128 * 0.5
    assert rig.apc.leds()[key] == hw.LED_OFF
    rig.press(hw.NOTE_TAP_TEMPO)
    assert rig.apc.leds()[key] == hw.LED_ON


def test_only_left_device_columns_control_head_parameters():
    rig = Rig()
    rig.press(hw.NOTE_CLIP_STOP, channel=2)
    assert rig.apc.knob_functions(2) == {1: "pan", 2: "tilt", 5: "speed", 6: "white"}
    rig.cc(hw.CC_DEVICE_KNOB_FIRST + 3, 127)  # unused knob 4
    assert rig.state("moving-head-1")["white"] == 0.0
    rig.cc(hw.CC_DEVICE_KNOB_FIRST + 5, 127)  # knob 6, lower-left second column
    assert rig.state("moving-head-1")["white"] == 1.0


def test_shift_track_select_opens_positions_without_stopping_fx():
    rig = Rig()
    rig.effects.start("apc40:2", EffectType.CHASE, 0, ["moving-head-1"])
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_SHIFT, 127))
    rig.press(hw.NOTE_TRACK_SELECT, channel=2)
    assert rig.apc.position_track() == 2
    assert rig.effects.owner_running("apc40:2")
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_SHIFT, 0))
    assert not rig.apc.shift_active()


def test_movement_fx_without_geometry_are_rejected_with_visible_error():
    rig = Rig()
    rig.press(hw.NOTE_SCENE_LAUNCH_FIRST + 2)
    rig.press(hw.pad_note(1))
    assert rig.apc.running_effect(2, EffectType.CIRCLE) is None
    assert "included area" in rig.apc.last_error()


def test_switching_to_single_heads_closes_inactive_group_position_menu():
    rig = Rig()
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_SHIFT, 127))
    rig.press(hw.NOTE_TRACK_SELECT, channel=2)
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_SHIFT, 0))
    assert rig.apc.position_track() == 2
    rig.press(hw.NOTE_ACTIVATOR, channel=3)
    assert rig.apc.position_track() is None


def test_zone_pads_distinguish_included_selected_and_excluded(monkeypatch):
    rig = Rig()
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_SHIFT, 127))
    rig.press(hw.NOTE_TRACK_SELECT, channel=2)
    assignment = HeadAssignment(
        aim_zone="b", include_zones=["a", "b"], exclude_zones=["c"],
    )
    monkeypatch.setattr(rig.apc, "position_assignment", lambda track: assignment)
    monkeypatch.setattr(rig.positions, "assignment_aim_reached", lambda head: True)
    monkeypatch.setattr(rig.positions.rig, "room_file", lambda: SimpleNamespace(
        targets=[], zones=[SimpleNamespace(id=id, name=id) for id in ("a", "b", "c", "d")],
    ))
    pads = rig.apc.position_pads()
    assert not pads[24][2] and pads[25][2]
    colours = rig.apc.pad_colours()
    assert colours[24] == 38  # permitted, not current aim
    assert colours[25] == 37  # current aim
    assert colours[26] == 6  # explicit exclusion, dim red
    assert colours[27] == 1  # not included, dim neutral
    rig.effects.start("apc40:2", EffectType.CIRCLE, 0, ["moving-head-1"])
    colours = rig.apc.pad_colours()
    assert colours[24] == colours[25] == 37
    assert colours[26] == 6
    assert colours[27] == 1
    rig.effects.stop("apc40:2", EffectType.CIRCLE)
    assert rig.apc.pad_colours()[24] == 38
    assert rig.apc.pad_colours()[25] == 37


@pytest.mark.parametrize("virtual", [False, True])
@pytest.mark.parametrize("pad,kind", [(23, "target"), (39, "zone")])
def test_empty_position_pad_requests_matching_dialog_without_assignment_changes(virtual, pad, kind):
    rig = Rig()
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_SHIFT, 127))
    rig.press(hw.NOTE_TRACK_SELECT, channel=2)
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_SHIFT, 0))
    before = rig.positions.rig.room_file()
    for request in (1, 2):
        rig.apc.handle(MidiEvent("note", 1, hw.pad_note(pad), 127), virtual=virtual)
        rig.apc.handle(MidiEvent("note", 1, hw.pad_note(pad), 0), virtual=virtual)
        assert rig.apc.position_dialog_request() == (request, kind)
        assert rig.apc.last_error() is None
    assert rig.positions.rig.room_file() == before
    rig.preview.start()
    rig.apc.handle(MidiEvent("note", 1, hw.pad_note(pad), 127), virtual=virtual)
    assert "Finish preview" in rig.apc.last_error()
    assert rig.apc.position_dialog_request() == (2, kind)


@pytest.mark.parametrize("preview", [False, True])
def test_zone_pad_presses_accumulate_areas_and_report_all_included(tmp_path, preview):
    patch = club_patch()
    rig = make_rig(patch, tmp_path)
    rig.set_head("moving-head-2", rig.head("moving-head-1"))
    app = create_app(AppConfig(universes=[5]), patch=patch, rig_store=rig)
    with TestClient(app) as client:
        def note(number, value=127, channel=1):
            response = client.post("/api/apc40/event", json={
                "kind": "note", "channel": channel, "number": number, "value": value,
            })
            assert response.status_code == 200
            return response.json()

        if preview:
            assert client.post("/api/preview", json={"active": True}).status_code == 200
        note(hw.NOTE_SHIFT)
        note(hw.NOTE_TRACK_SELECT, channel=2)
        note(hw.NOTE_SHIFT, 0)
        view = note(hw.NOTE_DEVICE_LOCK)
        assert view["position_mode"] == "permissions"
        controller = app.state.fixture_controller
        before = controller.state("moving-head-1")
        note(hw.pad_note(24))
        view = note(hw.pad_note(25))
        assert [p["included"] for p in view["pads"][24:26]] == [True, True]
        assert controller.state("moving-head-1") == before
        note(hw.NOTE_DEVICE_ON_OFF)
        view = note(hw.pad_note(25))
        assert view["pads"][25]["active"]
        saved = rig.room_file().strip_assignments["apc40:2"]
        assert saved.include_zones == ["booth", "floor-area"]
        assert saved.aim_zone == "floor-area"
        for head in rig.head_ids():
            assert rig.assignment(head).include_zones == ["booth", "floor-area"]
        if preview:
            assert rig.room_file(live=True).strip_assignments["apc40:2"].include_zones == []
            assert client.post("/api/preview", json={"active": False}).status_code == 200
        assert rig.room_file(live=True).strip_assignments["apc40:2"].include_zones == ["booth", "floor-area"]
        note(hw.NOTE_DEVICE_LOCK)
        view = note(hw.pad_note(24))
        assert [p["included"] for p in view["pads"][24:26]] == [False, True]
        assert view["pads"][24]["explicitly_excluded"]
        assert rig.room_file().strip_assignments["apc40:2"].include_zones == ["floor-area"]
        view = note(hw.pad_note(24))
        assert not view["pads"][24]["explicitly_excluded"]
        assert not view["pads"][24]["included"]
        note(hw.NOTE_SHIFT)
        view = note(hw.pad_note(25))
        note(hw.NOTE_SHIFT, 0)
        if preview:
            assert "Finish preview" not in (view["error"] or "")  # Go above committed preview.
        assert view["position_dialog_item"] == "floor-area"


def test_stopping_without_selected_strip_reports_error():
    rig = Rig()
    rig.press(hw.NOTE_STOP_ALL_CLIPS)
    assert "Select an FX strip" in rig.apc.last_error()


def test_haze_controls_preview_and_live_flash_restore_independent_of_blackout():
    patch = build_patch(
        [fixture.patch for fixture in club_patch().fixtures] + [
            PatchedFixture(
                id="hazer-1", name="Hazer", definition="look-unique-2-1", mode="2ch",
                universe=5, start_address=99,
            ),
        ],
        load_fixture_library(DEFAULT_FIXTURES_DIR), [5],
    )
    rig = Rig(patch=patch)
    rig.fader(8, 32)
    rig.cc(hw.CC_TRACK_KNOB_FIRST + 7, 64)
    rig.cc(hw.CC_MASTER_FADER, 0)
    rig.service.set_blackout(True)
    rig.controller.tick()
    assert rig.service.read_output(5)[98:100] == (64, 129)
    assert rig.apc.knob_functions(8) == {}
    rig.preview.start()
    rig.fader(8, 96)
    rig.cc(hw.CC_TRACK_KNOB_FIRST + 7, 100)
    rig.controller.tick()
    assert rig.service.read_output(5)[98:100] == (64, 129)
    rig.apc.handle(MidiEvent("note", 8, hw.NOTE_RECORD_ARM, 127))
    rig.controller.tick()
    assert rig.service.read_output(5)[98:100] == (255, 255)
    rig.apc.handle(MidiEvent("note", 8, hw.NOTE_RECORD_ARM, 0))
    rig.controller.tick()
    assert rig.service.read_output(5)[98:100] == (64, 129)
    rig.preview.go()
    rig.controller.tick()
    assert rig.service.read_output(5)[98:100] == (193, 201)
