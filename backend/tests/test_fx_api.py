"""Family FX authoring and launches use simulation and temporary persistence."""

import json

import pytest
from fastapi.testclient import TestClient

from lichtwerk.api import create_app
from lichtwerk import apc40 as hw
from lichtwerk.config import AppConfig
from lichtwerk.effects import EffectType
from test_apc40 import club_patch
from test_zones import make_rig


@pytest.fixture
def fx(tmp_path):
    patch = club_patch()
    rig = make_rig(patch)
    path = tmp_path / "fx-presets.json"
    app = create_app(AppConfig(universes=[5]), patch=patch, rig_store=rig, fx_path=path)
    with TestClient(app) as client:
        yield client, app, path, rig


def slot(client, track=1, page="chase", row=0):
    response = client.get(f"/api/fx/{track}/{page}/slots")
    assert response.status_code == 200, response.text
    return response.json()["slots"][row]


def test_three_families_five_slots_and_all_factory_choices(fx):
    client, _, _, _ = fx
    view = client.get("/api/apc40").json()
    assert [item["label"] for item in view["effect_types"]] == ["Dimmer", "Colour", "Move"]
    for page, factories in (("chase", 5), ("rainbow", 10), ("circle", 10)):
        data = client.get(f"/api/fx/2/{page}/slots").json()
        assert len(data["slots"]) == 5
        assert len(data["factory"]) == factories
    # The spare physical SCENE LAUNCH buttons must not index beyond the three pages.
    for number in (85, 86):
        response = client.post("/api/apc40/event", json={"kind": "note", "channel": 1, "number": number, "value": 127})
        assert response.status_code == 200


@pytest.mark.parametrize("family_index", [0, 1, 2])
@pytest.mark.parametrize("individuals", [False, True])
def test_fx_grid_has_five_presets_only_on_active_compatible_tracks(fx, family_index, individuals):
    client, _, _, _ = fx
    if individuals:
        for track in (3, 6):
            response = client.post("/api/apc40/event", json={
                "kind": "note", "channel": track, "number": 50, "value": 127,
            })
            assert response.status_code == 200
    response = client.post("/api/apc40/event", json={
        "kind": "note", "channel": 1, "number": 82 + family_index, "value": 127,
    })
    assert response.status_code == 200
    view = response.json()
    assert view["pad_mode"] == "effects"
    assert len(view["pads"]) == 40
    assert len(view["effect_types"]) == 3
    active_tracks = {1, 3, 4, 6, 7} if individuals else {1, 2, 5}
    if family_index == 2:
        active_tracks &= {2, 3, 4}
    for track in range(1, 9):
        pads = [pad for pad in view["pads"] if pad["pad"] % 8 == track - 1]
        assert len(pads) == 5
        assert all(bool(pad["label"]) == (track in active_tracks) for pad in pads)
        assert all(bool(pad["colour"]) == (track in active_tracks) for pad in pads)


def test_slot_save_snapshot_store_reset_and_restart(fx):
    client, _, path, _ = fx
    definition = slot(client)
    definition["name"] = "Personal triangle"
    definition["params"].update(shape="triangle", size=0.6)
    endpoint = "/api/fx/1/chase/slots/0"
    assert client.put(endpoint, json=definition).status_code == 200
    assert client.post(endpoint + "/start").json()["running"]["definition"]["name"] == "Personal triangle"
    changed = {**definition, "name": "Next run"}
    assert client.put(endpoint, json=changed).json()["running"]["definition"]["name"] == "Personal triangle"
    assert client.post(endpoint + "/store").json()["slots"][0]["name"] == "Personal triangle"
    assert path.exists()
    patch = club_patch()
    fresh = create_app(AppConfig(universes=[5]), patch=patch, rig_store=make_rig(patch), fx_path=path)
    with TestClient(fresh) as restarted:
        data = restarted.get("/api/fx/1/chase/slots").json()
        assert data["slots"][0]["name"] == "Personal triangle"
        assert data["running"] is None
    assert client.delete(endpoint).json()["slots"][0]["name"] == "Pulse"
    assert client.get("/api/fx/1/chase/slots").json()["running"] is not None


def test_colour_pad_edits_persist_only_when_stored(fx):
    client, _, path, _ = fx
    endpoint = "/api/fx/1/rainbow/slots/0"
    original = slot(client, page="rainbow")
    assert client.post(endpoint + "/start").status_code == 200
    for number in (hw.NOTE_CLIP_STOP, hw.pad_note(0)):
        response = client.post("/api/apc40/event", json={
            "kind": "note", "channel": 1, "number": number, "value": 127,
        })
        assert response.status_code == 200
    data = client.get("/api/fx/1/rainbow/slots").json()
    assert data["running"]["definition"]["params"]["palette"][-1] == "#ff0000"
    assert data["slots"][0] == original
    assert not path.exists()
    stored = client.post(endpoint + "/store")
    assert stored.status_code == 200
    assert stored.json()["slots"][0]["params"]["palette"][-1] == "#ff0000"
    assert json.loads(path.read_text())["slots"][0]["definition"]["params"]["colour_mode"] == "palette"


def test_preview_apply_isolated_and_stop_reveals_latest_base(fx):
    client, app, _, _ = fx
    controller = app.state.fixture_controller
    controller.set_values("led-bar-1", {"intensity": 0.8})
    assert client.post("/api/preview", json={"active": True}).status_code == 200
    definition = slot(client)
    definition["params"].update(timing="seconds", seconds=4.0, shape="triangle")
    response = client.post("/api/fx/1/chase/slots/0/apply", json=definition)
    assert response.status_code == 200, response.text
    assert response.json()["preview"]
    assert controller.output_state("led-bar-1")["intensity"] == 0.8
    controller.set_values("led-bar-1", {"intensity": 0.3})
    assert client.post("/api/preview", json={"active": False}).status_code == 200
    client.post("/api/fx/1/chase/stop")
    controller.tick()
    assert controller.output_state("led-bar-1")["intensity"] == pytest.approx(0.3)


def test_invalid_or_inactive_launch_preserves_running_and_saved_definition(fx):
    client, _, path, _ = fx
    client.post("/api/fx/1/chase/slots/0/start")
    before = slot(client)
    invalid = json.loads(json.dumps(before))
    invalid["params"]["order"] = ["unknown-fixture"]
    response = client.post("/api/fx/1/chase/slots/0/apply", json=invalid)
    assert response.status_code == 422
    assert not path.exists()
    assert slot(client) == before
    assert client.get("/api/fx/1/chase/slots").json()["running"] is not None
    # Individual-head strip is standby while its group is active.
    assert client.post("/api/fx/3/circle/slots/0/start").status_code == 422


def test_tour_references_protect_targets_and_reject_wrong_families(fx):
    client, _, _, rig = fx
    definition = slot(client, 2, "circle")
    target_id = rig.room_file().targets[0].id
    definition["params"].update(target_ids=[target_id], pattern="none")
    endpoint = "/api/fx/2/circle/slots/0"
    assert client.put(endpoint, json=definition).status_code == 200
    with pytest.raises(ValueError, match="FX preset"):
        rig.delete_target(target_id)
    wrong = client.put("/api/fx/1/chase/slots/0", json=definition)
    assert wrong.status_code == 422
    definition["params"]["target_ids"] = ["missing"]
    assert client.put(endpoint, json=definition).status_code == 422


def test_failed_persistence_does_not_change_runtime_or_slot(fx, monkeypatch):
    client, _, _, _ = fx
    definition = slot(client)
    client.post("/api/fx/1/chase/slots/0/start")
    before = client.get("/api/fx/1/chase/slots").json()
    definition["name"] = "Not saved"

    def fail(*_):
        raise OSError("disk unavailable")

    monkeypatch.setattr("lichtwerk.fx_presets.write_json_atomic", fail)
    response = client.post("/api/fx/1/chase/slots/0/apply", json=definition)
    assert response.status_code == 500
    assert "disk unavailable" in response.text
    after = client.get("/api/fx/1/chase/slots").json()
    assert before["running"] == after["running"]
    assert before["slots"] == after["slots"]


def test_fidelity_warning_uses_four_frame_boundary_and_timing_units(fx):
    client, _, _, _ = fx
    definition = slot(client)
    definition["params"]["beats"] = 0.25
    endpoint = "/api/fx/1/chase/slots/0/apply"
    assert client.put("/api/apc40/tempo", json={"bpm": 150.0}).status_code == 200
    data = client.post(endpoint, json=definition).json()
    assert not any("four" in warning for warning in data["warnings"])
    assert client.put("/api/apc40/tempo", json={"bpm": 151.0}).status_code == 200
    data = client.get("/api/fx/1/chase/slots").json()
    assert any("four" in warning for warning in data["warnings"])
    definition["params"].update(timing="seconds", seconds=0.25)
    data = client.post(endpoint, json=definition).json()
    assert not any("four" in warning for warning in data["warnings"])


def test_whole_instance_replacement_notice_reaches_controller_and_editor(fx):
    client, app, _, _ = fx
    assert client.post("/api/fx/1/chase/slots/0/start").status_code == 200
    effects = app.state.fixture_controller._effects
    effects.start("another-owner", EffectType.CHASE, 0, ["led-bar-1"])
    notice = client.get("/api/apc40").json()["fx_notice"]
    assert notice is not None and "led-bar-1" in notice
    data = client.get("/api/fx/1/chase/slots").json()
    assert data["running"] is None
    assert notice in data["warnings"]
