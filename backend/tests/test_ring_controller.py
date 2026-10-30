"""Ring controls share the APC40 command path without changing beam controls."""

import pytest
from fastapi.testclient import TestClient

from lichtwerk import apc40 as hw
from lichtwerk.api import create_app
from lichtwerk.config import AppConfig
from lichtwerk.effects import EffectType
from test_apc40 import DEFAULT_FIXTURES_DIR, Rig, build_patch, club_patch, load_fixture_library
from test_zones import make_rig


def test_ring_switch_restores_independent_knob_values_without_output_changes():
    rig = Rig()
    rig.cc(hw.CC_TRACK_KNOB_FIRST + 1, 38)
    before = rig.controller.snapshot()
    rig.press(hw.NOTE_CROSSFADER_AB, channel=2)
    assert rig.apc.ring_selected(2)
    assert rig.controller.snapshot() == before
    rig.cc(hw.CC_TRACK_KNOB_FIRST + 1, 89)
    assert rig.apc.saturation(2) == pytest.approx(89 / 127)
    assert rig.state("moving-head-1")["ring_intensity"] == pytest.approx(89 / 127)
    assert rig.state("moving-head-1")["intensity"] == before["moving-head-1"]["intensity"]
    ring_state = rig.controller.snapshot()
    rig.press(hw.NOTE_CROSSFADER_AB, channel=2)
    assert rig.apc.saturation(2) == pytest.approx(38 / 127)
    assert rig.controller.snapshot() == ring_state
    rig.press(hw.NOTE_CROSSFADER_AB, channel=2)
    assert rig.apc.saturation(2) == pytest.approx(89 / 127)
    assert rig.apc.leds()[("cc", 1, hw.CC_TRACK_KNOB_FIRST + 1)] == 89
    assert rig.apc.leds()[("note", 2, hw.NOTE_CROSSFADER_AB)] == hw.LED_ON
    rig.press(hw.NOTE_CROSSFADER_AB, channel=1)
    assert not rig.apc.ring_selected(1)


def test_ring_all_and_individual_colour_pads_strobe_and_flash():
    rig = Rig()
    rig.press(hw.NOTE_CROSSFADER_AB, channel=2)
    rig.press(hw.NOTE_CLIP_STOP, channel=2)
    rig.press(hw.pad_note(9))  # Red palette entry, all sections by default.
    for head in ("moving-head-1", "moving-head-2"):
        assert all(rig.state(head)[f"ring_{i}_red"] == 1 for i in range(1, 7))
        assert rig.state(head)["red"] == 0
    rig.press(hw.pad_note(2))
    rig.press(hw.pad_note(16))  # Blue palette entry, section 2 only.
    for head in ("moving-head-1", "moving-head-2"):
        assert rig.state(head)["ring_2_blue"] == 1
        assert rig.state(head)["ring_2_red"] == 0
        assert rig.state(head)["ring_1_red"] == 1
    rig.press(hw.NOTE_SOLO, channel=2)
    assert rig.state("moving-head-1")["ring_strobe"] > 0
    assert rig.state("moving-head-1")["strobe"] == 0
    base = rig.state("moving-head-1")
    rig.press(hw.NOTE_RECORD_ARM, channel=2)
    assert rig.state("moving-head-1") == base
    rig.fader(2, 64)
    assert rig.state("moving-head-1")["intensity"] == pytest.approx(64 / 127)
    assert rig.state("moving-head-1")["ring_intensity"] == 0


def test_stop_fx_stops_selected_component_only_and_preserves_group_single_memory():
    rig = Rig()
    rig.press(hw.NOTE_SCENE_LAUNCH_FIRST)
    rig.press(hw.pad_note(1))
    rig.press(hw.NOTE_CROSSFADER_AB, channel=2)
    rig.press(hw.pad_note(1))
    assert rig.effects.running("apc40:2", EffectType.CHASE) is not None
    assert rig.effects.running("apc40:2", EffectType.CHASE, component="ring") is not None
    assert 5 in rig.apc.device_knobs()  # Spread operates on the twelve ring sections.
    rig.press(hw.NOTE_ACTIVATOR, channel=3)
    assert not rig.effects.running("apc40:2", EffectType.CHASE).active
    assert not rig.effects.running("apc40:2", EffectType.CHASE, component="ring").active
    rig.press(hw.NOTE_ACTIVATOR, channel=2)
    assert rig.effects.running("apc40:2", EffectType.CHASE, component="ring").active
    rig.press(hw.NOTE_STOP_ALL_CLIPS)
    assert rig.effects.running("apc40:2", EffectType.CHASE, component="ring") is None
    assert rig.effects.running("apc40:2", EffectType.CHASE) is not None
    rig.press(hw.NOTE_CROSSFADER_AB, channel=2)
    rig.press(hw.NOTE_STOP_ALL_CLIPS)
    assert rig.effects.running("apc40:2", EffectType.CHASE) is None


def test_individual_segment_does_not_change_uniform_only_head_in_mixed_group():
    original = club_patch()
    entries = [
        f.patch.model_copy(update={"mode": "16ch"}) if f.patch.id == "moving-head-2" else f.patch
        for f in original.fixtures
    ]
    rig = Rig(patch=build_patch(entries, load_fixture_library(DEFAULT_FIXTURES_DIR), [5]))
    rig.press(hw.NOTE_CROSSFADER_AB, channel=2)
    rig.press(hw.NOTE_CLIP_STOP, channel=2)
    rig.press(hw.pad_note(9))
    before = rig.state("moving-head-2")
    rig.press(hw.pad_note(2))
    rig.press(hw.pad_note(16))
    assert rig.state("moving-head-1")["ring_2_blue"] == 1
    assert rig.state("moving-head-2") == before
    rig.press(hw.NOTE_ACTIVATOR, channel=3)
    rig.press(hw.NOTE_ACTIVATOR, channel=2)
    assert rig.state("moving-head-2") == before


def test_ring_fx_api_has_segment_roster_independent_slots_and_preview(tmp_path):
    patch = club_patch()
    app = create_app(AppConfig(universes=[5]), patch=patch, rig_store=make_rig(patch),
                     fx_path=tmp_path / "fx-presets.json")
    with TestClient(app) as client:
        beam = client.get("/api/fx/2/chase/slots").json()
        endpoint = "/api/fx/2/chase/slots"
        ring = client.get(endpoint + "?component=ring")
        assert ring.status_code == 200, ring.text
        data = ring.json()
        assert len(data["fixtures"]) == 12
        assert data["fixtures"][0]["id"] == "moving-head-1:ring:1"
        assert data["fixtures"][-1]["id"] == "moving-head-2:ring:6"
        definition = data["slots"][0]
        definition["name"] = "Ring chase"
        definition["params"].update(shape="one_by_one", spread=1.0,
                                    order=[item["id"] for item in reversed(data["fixtures"])])
        assert client.put(endpoint + "/0?component=ring", json=definition).status_code == 200
        assert client.get(endpoint).json()["slots"] == beam["slots"]
        assert client.post("/api/preview", json={"active": True}).status_code == 200
        result = client.post(endpoint + "/0/start?component=ring")
        assert result.status_code == 200, result.text
        assert result.json()["preview"]
        assert result.json()["running"]["definition"]["name"] == "Ring chase"
        assert client.get(endpoint).json()["running"] is None
        assert client.post("/api/preview", json={"active": False}).status_code == 200
        assert not client.get(endpoint + "?component=ring").json()["preview"]
        assert client.get("/api/fx/2/circle/slots?component=ring").status_code == 422
        view = client.post("/api/apc40/event", json={
            "kind": "note", "channel": 2, "number": hw.NOTE_CROSSFADER_AB, "value": 127,
        }).json()
        strip = view["strips"][1]
        assert strip["ring_selected"] and strip["ring_available"]
        assert len(strip["ring_colours"]) == 12
    restarted = create_app(AppConfig(universes=[5]), patch=patch, rig_store=make_rig(patch),
                           fx_path=tmp_path / "fx-presets.json")
    with TestClient(restarted) as client:
        ring = client.get("/api/fx/2/chase/slots?component=ring").json()
        assert ring["slots"][0]["name"] == "Ring chase"
        assert ring["running"] is None
        assert client.get("/api/fx/2/chase/slots").json()["slots"] == beam["slots"]
        assert not client.get("/api/apc40").json()["strips"][1]["ring_selected"]


def test_go_with_live_ring_dimmer_and_staged_ring_colour_keeps_render_ids_physical():
    patch = club_patch()
    app = create_app(AppConfig(universes=[5]), patch=patch, rig_store=make_rig(patch))
    with TestClient(app) as client:
        assert client.post("/api/fx/2/chase/slots/0/start?component=ring").status_code == 200
        app.state.fixture_controller.tick()
        assert client.post("/api/preview", json={"active": True}).status_code == 200
        assert client.post("/api/fx/2/rainbow/slots/0/start?component=ring").status_code == 200
        assert client.post("/api/preview", json={"active": False}).status_code == 200
        app.state.fixture_controller.tick()
        assert client.get("/api/fx/2/chase/slots?component=ring").json()["running"] is not None
        assert client.get("/api/fx/2/rainbow/slots?component=ring").json()["running"] is not None
