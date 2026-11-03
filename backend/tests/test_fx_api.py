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
    assert [item["label"] for item in view["effect_types"]] == ["Dimmer", "Colour", "Move", "Combo"]
    for page, factories in (("chase", 5), ("rainbow", 10), ("circle", 10)):
        data = client.get(f"/api/fx/2/{page}/slots").json()
        assert len(data["slots"]) == 5
        assert len(data["factory"]) == factories
    # Combo and the remaining spare physical button must both be accepted.
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
    assert len(view["effect_types"]) == 4
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


def combos(client, track=1, component="beam"):
    response = client.get(f"/api/fx/combo/{track}/slots?component={component}")
    assert response.status_code == 200, response.text
    return response.json()


def event(client, number, channel=1, value=127):
    response = client.post("/api/apc40/event", json={
        "kind": "note", "channel": channel, "number": number, "value": value,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_combo_launch_replace_detach_and_stop(fx):
    client, app, _, _ = fx
    data = combos(client)
    assert len(data["slots"]) == 5
    assert [effect["kind"] for effect in data["slots"][0]["effects"]] == ["chase", "rainbow"]
    endpoint = "/api/fx/combo/1/slots/0"
    assert client.post("/api/fx/1/chase/slots/2/start").status_code == 200
    launched = client.post(endpoint + "/start").json()
    assert launched["running"]["slot"] == 0
    assert not launched["running"]["modified"]
    engine = app.state.fixture_controller._effects
    bundle = engine.combo_effects("apc40:1")
    assert len(bundle) == 2
    assert len({effect.launched_at for effect in bundle}) == 1
    assert len({effect.launch_beat for effect in bundle}) == 1
    colour = client.get("/api/fx/1/rainbow/slots").json()["running"]
    assert colour["combo_name"] == "Slow Atmosphere"
    assert client.post("/api/fx/1/rainbow/slots/1/start").status_code == 200
    assert combos(client)["running"]["modified"]
    assert client.post("/api/fx/combo/1/stop").status_code == 200
    assert combos(client)["running"] is None
    assert client.get("/api/fx/1/chase/slots").json()["running"] is None
    assert client.get("/api/fx/1/rainbow/slots").json()["running"]["slot"] == 1
    event(client, hw.NOTE_STOP_ALL_CLIPS)
    assert client.get("/api/fx/1/rainbow/slots").json()["running"] is None


def test_combo_palette_and_knobs_keep_membership_store_runtime_only(fx):
    client, app, path, _ = fx
    endpoint = "/api/fx/combo/1/slots/0"
    original = combos(client)["slots"][0]
    assert client.post(endpoint + "/start").status_code == 200
    engine = app.state.fixture_controller._effects
    before = engine.combo_effects("apc40:1")
    event(client, hw.NOTE_CLIP_STOP)
    event(client, hw.pad_note(4))
    event(client, hw.NOTE_SCENE_LAUNCH_FIRST + 3)
    assert client.post("/api/fx/combo/1/knobs/chase").status_code == 200
    response = client.post("/api/apc40/event", json={
        "kind": "cc", "channel": 1, "number": hw.CC_DEVICE_KNOB_FIRST + 1, "value": 70,
    })
    assert response.status_code == 200
    current = combos(client)
    assert not current["running"]["modified"]
    assert current["slots"][0] == original
    assert not path.exists()
    assert [effect.launched_at for effect in engine.combo_effects("apc40:1")] == [
        effect.launched_at for effect in before
    ]
    stored = client.post(endpoint + "/store").json()["slots"][0]
    assert stored != original
    assert stored == combos(client)["running"]["definition"]
    assert path.exists()


def test_combo_save_reset_and_restart_preserve_regular_slots(fx):
    client, _, path, _ = fx
    endpoint = "/api/fx/combo/1/slots/0"
    definition = combos(client)["slots"][0]
    definition["name"] = "My club Combo"
    standalone = slot(client)
    standalone["name"] = "Independent dimmer"
    assert client.put("/api/fx/1/chase/slots/0", json=standalone).status_code == 200
    assert client.post(endpoint + "/apply", json=definition).status_code == 200
    document = json.loads(path.read_text())
    assert len(document["slots"]) == 1 and len(document["combos"]) == 1
    changed = {**definition, "name": "Next launch"}
    assert client.put(endpoint, json=changed).json()["running"]["definition"]["name"] == "My club Combo"
    patch = club_patch()
    restarted = create_app(AppConfig(universes=[5]), patch=patch, rig_store=make_rig(patch), fx_path=path)
    with TestClient(restarted) as fresh:
        assert combos(fresh)["slots"][0]["name"] == "Next launch"
        assert combos(fresh)["running"] is None
        assert slot(fresh)["name"] == "Independent dimmer"
    assert client.delete(endpoint).json()["slots"][0]["name"] == "Slow Atmosphere"
    assert combos(client)["running"]["definition"]["name"] == "My club Combo"
    assert slot(client)["name"] == "Independent dimmer"


def test_combo_preview_isolation_and_go(fx):
    client, app, _, _ = fx
    assert client.post("/api/fx/combo/1/slots/0/start").status_code == 200
    engine = app.state.fixture_controller._effects
    live = engine.all_running(preview=False)
    assert client.post("/api/preview", json={"active": True}).status_code == 200
    assert client.post("/api/fx/1/rainbow/slots/1/start").status_code == 200
    assert combos(client)["running"]["modified"]
    assert client.post("/api/fx/combo/1/stop").status_code == 200
    assert engine.all_running(preview=False) == live
    assert client.post("/api/preview", json={"active": False}).status_code == 200
    assert combos(client)["running"] is None
    assert client.get("/api/fx/1/rainbow/slots").json()["running"]["slot"] == 1


def test_combo_pad_edit_toggle_and_feedback(fx):
    client, _, _, _ = fx
    view = event(client, hw.NOTE_SCENE_LAUNCH_FIRST + 3)
    assert view["combo_open"] and view["effect_types"][3]["selected"]
    assert all(view["pads"][row * 8]["label"] for row in range(5))
    event(client, hw.NOTE_SHIFT)
    view = event(client, hw.pad_note(0))
    assert view["fx_dialog"]["page"] == "combo"
    assert combos(client)["running"] is None
    event(client, hw.NOTE_SHIFT, value=0)
    view = event(client, hw.pad_note(0))
    assert view["pads"][0]["active"]
    assert view["combo_status"] == "Slow Atmosphere"
    client.post("/api/fx/1/rainbow/slots/1/start")
    view = client.get("/api/apc40").json()
    assert "modified" in view["pads"][0]["label"]
    event(client, hw.pad_note(0))
    assert combos(client)["running"] is None
    assert client.get("/api/fx/1/rainbow/slots").json()["running"] is not None


def test_combo_disabled_families_clear_old_effects(fx):
    client, _, _, _ = fx
    endpoint = "/api/fx/combo/1/slots/0"
    assert client.post(endpoint + "/start").status_code == 200
    definition = combos(client)["slots"][0]
    definition["effects"] = definition["effects"][:1]
    assert client.post(endpoint + "/apply", json=definition).status_code == 200
    assert client.get("/api/fx/1/rainbow/slots").json()["running"] is None
    assert len(combos(client)["running"]["definition"]["effects"]) == 1


def test_combo_invalid_component_rejects_entire_launch(fx):
    client, app, _, _ = fx
    assert client.post("/api/fx/2/chase/slots/0/start").status_code == 200
    engine = app.state.fixture_controller._effects
    before = engine.all_running()
    definition = combos(client, 2)["slots"][0]
    move = next(effect for effect in definition["effects"] if effect["kind"] == "circle")
    move["params"].update(target_ids=["missing"], pattern="none")
    response = client.post("/api/fx/combo/2/slots/0/apply", json=definition)
    assert response.status_code == 422 and "missing" in response.text
    assert engine.all_running() == before
    definition = combos(client, 1)["slots"][0]
    definition["effects"].append(definition["effects"][0])
    assert client.put("/api/fx/combo/1/slots/0", json=definition).status_code == 422
    definition["effects"] = []
    assert client.put("/api/fx/combo/1/slots/0", json=definition).status_code == 422


def test_fixture_colour_picker_reports_selected_pad_for_display_blink(fx):
    client, _, _, _ = fx
    event(client, hw.NOTE_CLIP_STOP)
    view = event(client, hw.pad_note(4))
    assert view["pads"][4]["kind"] == "fixture_colour"
    assert view["pads"][4]["active"]
    assert sum(pad["active"] for pad in view["pads"]) == 1
    view = event(client, hw.pad_note(hw.WHITE_PAD))
    assert view["pads"][hw.WHITE_PAD]["active"]
    assert not view["pads"][4]["active"]


def test_combo_all_three_families_and_individual_stop(fx):
    client, _, _, rig = fx
    permit_combo_movement(rig)
    endpoint = "/api/fx/combo/2/slots/0"
    launched = client.post(endpoint + "/start")
    assert launched.status_code == 200, launched.text
    assert len(launched.json()["running"]["definition"]["effects"]) == 3
    assert client.post("/api/fx/2/circle/stop").status_code == 200
    assert combos(client, 2)["running"]["modified"]
    assert client.post("/api/fx/2/circle/slots/1/start").status_code == 200
    assert client.post("/api/fx/combo/2/stop").status_code == 200
    assert client.get("/api/fx/2/circle/slots").json()["running"]["slot"] == 1
    assert client.get("/api/fx/2/chase/slots").json()["running"] is None
    assert client.get("/api/fx/2/rainbow/slots").json()["running"] is None


def test_combo_group_single_suspension_and_standby_rejection(fx):
    client, _, _, _ = fx
    # This Combo deliberately has no Move component; mode ownership is independent of calibration.
    assert client.post("/api/fx/combo/2/slots/1/start").status_code == 200
    event(client, hw.NOTE_ACTIVATOR, channel=3)
    assert not combos(client, 2)["running"]["active"]
    assert client.post("/api/fx/combo/2/slots/1/start").status_code == 422
    assert client.post("/api/fx/combo/3/slots/1/start").status_code == 200
    event(client, hw.NOTE_ACTIVATOR, channel=2)
    assert combos(client, 2)["running"]["active"]
    assert not combos(client, 3)["running"]["active"]
    assert not combos(client, 2)["running"]["modified"]


def test_combo_movement_permission_failure_is_atomic(fx):
    client, app, _, rig = fx
    assert client.post("/api/fx/2/chase/slots/0/start").status_code == 200
    engine = app.state.fixture_controller._effects
    before = engine.all_running()
    # Remove all permitted movement areas without changing any FX state.
    rig.set_assignments({})
    response = client.post("/api/fx/combo/2/slots/0/start")
    assert response.status_code == 422, response.text
    assert engine.all_running() == before


def test_combo_failed_save_preserves_runtime(fx, monkeypatch):
    client, app, _, _ = fx
    endpoint = "/api/fx/combo/1/slots/0"
    assert client.post(endpoint + "/start").status_code == 200
    engine = app.state.fixture_controller._effects
    before = engine.all_running()
    definition = combos(client)["slots"][0]
    definition["name"] = "Not saved"

    def fail(*_):
        raise OSError("disk unavailable")

    monkeypatch.setattr("lichtwerk.fx_presets.write_json_atomic", fail)
    response = client.post(endpoint + "/apply", json=definition)
    assert response.status_code == 500 and "disk unavailable" in response.text
    assert engine.all_running() == before
    assert combos(client)["slots"][0]["name"] == "Slow Atmosphere"


def test_combo_ring_and_beam_are_independent(tmp_path):
    from test_apc40 import DEFAULT_FIXTURES_DIR, build_patch, load_fixture_library
    from lichtwerk.patch import PatchedFixture

    library = load_fixture_library(DEFAULT_FIXTURES_DIR)
    patch = build_patch([PatchedFixture(
        id="moving-head-1", name="Head", definition="equinox-fusion-orbit-mkii",
        mode="37ch", universe=5, start_address=7, groups=["moving-heads"],
    )], library, [5])
    rig = make_rig(patch)
    permit_combo_movement(rig)
    app = create_app(AppConfig(universes=[5]), patch=patch, rig_store=rig,
                     fx_path=tmp_path / "fx-presets.json")
    with TestClient(app) as client:
        assert client.post("/api/fx/combo/2/slots/0/start").status_code == 200
        ring = combos(client, 2, "ring")
        assert len(ring["slots"][0]["effects"]) == 2
        assert "circle" not in ring["families"]
        assert client.post("/api/fx/combo/2/slots/0/start?component=ring").status_code == 200
        assert client.post("/api/fx/combo/2/stop?component=ring").status_code == 200
        assert combos(client, 2, "ring")["running"] is None
        assert combos(client, 2)["running"] is not None
        ring["slots"][0]["effects"].append(slot(client, 2, "circle"))
        assert client.put("/api/fx/combo/2/slots/0?component=ring", json=ring["slots"][0]).status_code == 422


def test_combo_even_selection_applied_only_once(fx):
    client, _, _, rig = fx
    permit_combo_movement(rig)
    definition = combos(client, 2)["slots"][0]
    for effect in definition["effects"]:
        effect["params"]["selection"] = "even"
    response = client.post("/api/fx/combo/2/slots/0/apply", json=definition)
    assert response.status_code == 200, response.text


def permit_combo_movement(rig):
    from lichtwerk.room import HeadAssignment
    from test_zones import INCLUDE, calibrated

    for head in rig.head_ids():
        rig.set_head(head, calibrated())
    rig.set_assignments({head: HeadAssignment(include_zones=[INCLUDE.id]) for head in rig.head_ids()})


def test_combo_saved_tour_protects_target_and_modified_store_excludes_replacement(fx):
    client, _, _, rig = fx
    definition = combos(client, 2)["slots"][0]
    move = next(effect for effect in definition["effects"] if effect["kind"] == "circle")
    move["params"].update(target_ids=["in-zone"], pattern="none")
    assert client.put("/api/fx/combo/2/slots/0", json=definition).status_code == 200
    with pytest.raises(ValueError, match="Combo"):
        rig.delete_target("in-zone")
    assert client.delete("/api/fx/combo/2/slots/0").status_code == 200

    assert client.post("/api/fx/combo/1/slots/0/start").status_code == 200
    assert client.post("/api/fx/1/rainbow/slots/1/start").status_code == 200
    stored = client.post("/api/fx/combo/1/slots/0/store").json()["slots"][0]
    assert [effect["kind"] for effect in stored["effects"]] == ["chase"]
    assert client.get("/api/fx/1/rainbow/slots").json()["running"]["slot"] == 1


def test_move_page_reports_tour_target_suitability_without_moving(fx):
    from lichtwerk.room import Target

    client, app, _, rig = fx
    permit_combo_movement(rig)
    document = rig.room_file()
    rig.set_room_file(document.model_copy(update={"targets": [
        *document.targets, Target(id="unmeasured", name="Unmeasured"),
        Target(id="floor-centre", name="Floor centre", x=2.0, y=3.0, z=0.0),
        # Above the included floor area: the beam lands inside it, but the target itself is not.
        Target(id="floor-high", name="Floor high", x=2.0, y=3.0, z=1.5),
    ]}))
    before = client.get("/api/fixtures").json()
    data = client.get("/api/fx/2/circle/slots").json()
    assert client.get("/api/fixtures").json() == before
    assert data["room"] is not None
    assert data["anchors"] and all("included area" in line for line in data["anchors"])
    tour = {item["id"]: item for item in data["tour_targets"]}
    assert set(tour) == {t.id for t in rig.room_file().targets}
    assert tour["unmeasured"]["reachable"] is False and "coordinates" in tour["unmeasured"]["note"]
    assert tour["in-zone"]["x"] == 3.5 and tour["in-zone"]["z"] == 0.0
    assert tour["floor-centre"]["pattern_ok"] is True
    assert tour["floor-high"]["reachable"] is True and tour["floor-high"]["pattern_ok"] is False
    assert tour["in-zone"]["reachable"] is True and tour["in-zone"]["pattern_ok"] is False

    # Reported suitability matches what launch validation accepts.
    for target_id, item in tour.items():
        for pattern in ("none", "circle"):
            definition = slot(client, 2, "circle")
            definition["params"].update(target_ids=[target_id], pattern=pattern, size=0.3)
            if target_id == "unmeasured":
                continue
            client.put("/api/fx/2/circle/slots/0", json=definition)
            started = client.post("/api/fx/2/circle/slots/0/start").status_code == 200
            expected = item["reachable"] if pattern == "none" else item["pattern_ok"]
            assert started == expected, (target_id, pattern, item)
            client.post("/api/fx/2/circle/stop")

    assert client.get("/api/fx/1/chase/slots").json()["tour_targets"] == []


def test_zone_routes_report_status_quick_include_and_protect_zones(fx):
    from lichtwerk.room import HeadAssignment
    from test_zones import INCLUDE

    client, _, _, rig = fx
    permit_combo_movement(rig)
    # Real permission edits go through the strip (APC Aim/Permissions); mirror that here.
    rig.set_position_settings({}, {"apc40:2": HeadAssignment(include_zones=["floor-area"])})
    extra = INCLUDE.model_copy(update={
        "id": "floor-b", "name": "Floor B", "corner": {"x": 3.5, "y": 5.0}, "length": 1.5, "width": 2.0,
    })
    rig.save_zone(extra)
    data = client.get("/api/fx/2/circle/slots").json()
    zones = {item["id"]: item for item in data["tour_zones"]}
    assert zones["floor-area"]["state"] == "included"
    # Booth is an "exclude" zone at room level only; the strip's own permissions decide.
    assert zones["booth"]["state"] == "excluded"
    assert zones["floor-b"]["state"] == "excluded" and "tap to include" in zones["floor-b"]["note"]
    x0, y0, x1, y1 = zones["floor-area"]["box"]
    assert x0 < x1 and y0 < y1
    assert {aim["head"] for aim in data["current_aims"]} == set(rig.head_ids())
    assert all("floor-area" in aim["include_zones"] for aim in data["current_aims"])

    # A zone route through an excluded zone is refused until it is included.
    definition = slot(client, 2, "circle")
    definition["params"].update(zone_ids=["floor-area", "floor-b"], pattern="circle", size=0.3)
    assert client.put("/api/fx/2/circle/slots/0", json=definition).status_code == 200
    refused = client.post("/api/fx/2/circle/slots/0/start")
    assert refused.status_code == 422 and "Floor B" in refused.json()["detail"]

    before = client.get("/api/fixtures").json()
    included = client.post("/api/fx/2/circle/include-zone/floor-b")
    assert included.status_code == 200, included.text
    assert client.get("/api/fixtures").json() == before  # permission only: nothing moves
    assert {z["id"]: z["state"] for z in included.json()["tour_zones"]}["floor-b"] == "included"
    booth = client.post("/api/fx/2/circle/include-zone/booth")
    assert booth.status_code == 200, booth.text
    assert {z["id"]: z["state"] for z in booth.json()["tour_zones"]}["booth"] == "included"
    assert client.post("/api/fx/2/circle/include-zone/missing").status_code == 422
    started = client.post("/api/fx/2/circle/slots/0/start")
    assert started.status_code == 200, started.text
    with pytest.raises(ValueError, match="FX preset"):
        rig.delete_zone("floor-b")
    client.post("/api/fx/2/circle/stop")

    # Targets and zones cannot be mixed in one route.
    definition["params"].update(target_ids=["in-zone"])
    assert client.put("/api/fx/2/circle/slots/0", json=definition).status_code == 422

    # Confirmed quick include is the same binary toggle as Aim/Permissions.
    owner = "apc40:2"
    strip = rig.room_file().strip_assignments.get(owner, HeadAssignment())
    rig.set_position_settings({}, {owner: strip.model_copy(update={"exclude_zones": ["floor-b"]})})
    assert client.post("/api/fx/2/circle/include-zone/floor-b").status_code == 200
    assert "floor-b" not in rig.room_file().strip_assignments[owner].exclude_zones
