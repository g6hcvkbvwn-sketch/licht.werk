"""Moving-head fixture rendering, positioning service and API (no hardware involved)."""

import json

import pytest
from fastapi.testclient import TestClient

from lichtwerk.api import create_app
from lichtwerk.config import DEFAULT_FIXTURES_DIR, AppConfig
from lichtwerk.fixtures import load_fixture_library
from lichtwerk.patch import PatchedFixture, build_patch
from lichtwerk.positioning import HeadCalibration, HeadSetup, HeadLimits, HeadRest, Point
from lichtwerk.room import RigStore, RoomFile, Target, head_problems, HeadsFile

HEAD = "moving-head-1"
# Head 1 occupies universe 5, addresses 4-19 (0-based list index = address - 1).
BASE = 3


def make_patch():
    library = load_fixture_library(DEFAULT_FIXTURES_DIR)
    entries = [
        PatchedFixture(
            id=HEAD, name="Head 1", definition="equinox-fusion-orbit-mkii", mode="16ch",
            universe=5, start_address=4, groups=["moving-heads"],
        )
    ]
    return build_patch(entries, library, [5])


def calibrated() -> HeadSetup:
    return HeadSetup(
        position=Point(x=2.0, y=3.0, z=4.0),
        calibration=HeadCalibration(pan_zero_azimuth_deg=0.0, tilt_axis_deg=105.0),
    )


def make_rig(patch, tmp_path=None, head: HeadSetup | None = None) -> RigStore:
    room = RoomFile(
        targets=[
            Target(id="center", name="Center", x=3.0, y=4.0, z=0.0),
            Target(id="unmeasured", name="Unmeasured"),
        ],
    )
    heads = HeadsFile(heads={HEAD: head} if head else {})
    return RigStore(
        patch,
        tmp_path / "room.json" if tmp_path else None,
        tmp_path / "heads.json" if tmp_path else None,
        room,
        heads,
    )


@pytest.fixture
def client(tmp_path):
    patch = make_patch()
    rig = make_rig(patch, tmp_path, calibrated())
    app = create_app(AppConfig(universes=[5]), patch=patch, rig_store=rig)
    with TestClient(app) as c:
        # These tests exercise channel conversion, not the room permission mask.
        c.put(f"/api/heads/{HEAD}/setup-override", json={"enabled": True})
        c.post("/api/output/blackout", json={"active": False})
        yield c


def live_state(client) -> dict:
    return next(f["state"] for f in client.get("/api/fixtures").json() if f["id"] == HEAD)


def channels(client) -> list[int]:
    return client.get("/api/universes/5").json()["channels"]


def test_startup_is_dark_with_default_speed_and_fixed_channels(client) -> None:
    head = channels(client)[BASE : BASE + 16]

    # Speed defaults to fastest; the Orbit's speed channel is inverted (fastest = DMX 0).
    assert head == [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]


def test_pan_tilt_strobe_and_ring_render_to_documented_channels(client) -> None:
    client.post(
        f"/api/fixtures/{HEAD}/state",
        json={"values": {"pan": 1.0, "tilt": 0.5, "strobe": 1.0, "ring_strobe": 1.0, "ring_blue": 1.0}},
    )
    head = channels(client)[BASE : BASE + 16]

    assert head[0] == 255 and head[1] == 128
    assert head[4] == 255 and head[10] == 250  # top of each native strobe range
    assert head[13] == 255
    assert head[14] == 0 and head[15] == 0


def test_strobe_off_is_zero_and_lowest_step_skips_no_function_range(client) -> None:
    client.post(f"/api/fixtures/{HEAD}/state", json={"values": {"strobe": 0.001}})
    assert channels(client)[BASE + 4] >= 10
    client.post(f"/api/fixtures/{HEAD}/state", json={"values": {"strobe": 0.0}})
    assert channels(client)[BASE + 4] == 0


def test_master_scales_ring_and_beam_intensity_but_not_strobe(client) -> None:
    client.post(
        f"/api/fixtures/{HEAD}/state",
        json={"values": {"intensity": 1.0, "ring_intensity": 1.0, "strobe": 1.0}},
    )
    client.post("/api/output/master", json={"level": 0.5})
    head = channels(client)[BASE : BASE + 16]

    assert head[3] == 128 and head[9] == 128 and head[4] == 255


def test_blackout_zeroes_strobe(client) -> None:
    client.post(f"/api/fixtures/{HEAD}/state", json={"values": {"strobe": 1.0, "intensity": 1.0}})
    client.post("/api/output/blackout", json={"active": True})

    assert channels(client)[BASE : BASE + 16] == [0] * 16


def test_blackout_keeps_head_position(client) -> None:
    from lichtwerk.fixtures import MOTION_ATTRIBUTES

    values = {"pan": 0.6, "tilt": 0.3, "speed": 0.8, "intensity": 1.0, "red": 1.0, "strobe": 0.5}
    client.post(f"/api/fixtures/{HEAD}/state", json={"values": values})
    lit = channels(client)[BASE : BASE + 16]
    mode = make_patch().get(HEAD).mode
    motion = [i for i, c in enumerate(mode.channels) if c.attribute in MOTION_ATTRIBUTES]
    assert len(motion) >= 3 and all(lit[i] > 0 for i in motion)

    client.post("/api/output/blackout", json={"active": True})
    dark = channels(client)[BASE : BASE + 16]
    assert [dark[i] for i in motion] == [lit[i] for i in motion]
    assert all(v == 0 for i, v in enumerate(dark) if i not in motion)

    client.post("/api/output/blackout", json={"active": False})
    assert channels(client)[BASE : BASE + 16] == lit


def test_strobe_is_capped_by_configuration(tmp_path) -> None:
    patch = make_patch()
    app = create_app(AppConfig(universes=[5], max_strobe=0.5), patch=patch, rig_store=make_rig(patch))
    with TestClient(app) as client:
        client.put(f"/api/heads/{HEAD}/setup-override", json={"enabled": True})
        client.post("/api/output/blackout", json={"active": False})
        client.post(f"/api/fixtures/{HEAD}/state", json={"values": {"strobe": 1.0}})
        # 0.5 of 10..255 -> 133 (rounded)
        assert channels(client)[BASE + 4] == 133


def test_aim_at_target_moves_head_and_reports_floor_point(client) -> None:
    response = client.post(f"/api/heads/{HEAD}/aim", json={"target": "center"})

    assert response.status_code == 200
    body = response.json()
    assert body["floor_point"]["x"] == pytest.approx(3.0, abs=1e-6)
    assert body["floor_point"]["y"] == pytest.approx(4.0, abs=1e-6)
    # The UI shows pan/tilt in degrees from the fixture's documented range.
    assert (body["pan_degrees"], body["tilt_degrees"]) == (540, 210)
    state = live_state(client)
    assert state["pan"] == pytest.approx(body["pan"])
    assert channels(client)[BASE] == round(body["pan"] * 255)


def test_aim_errors(client) -> None:
    assert client.post(f"/api/heads/{HEAD}/aim", json={"target": "unmeasured"}).status_code == 409
    assert client.post(f"/api/heads/{HEAD}/aim", json={"target": "nope"}).status_code == 404
    assert client.post(f"/api/heads/{HEAD}/aim", json={}).status_code == 422
    own_position = {"point": {"x": 2.0, "y": 3.0, "z": 4.0}}
    assert client.post(f"/api/heads/{HEAD}/aim", json=own_position).status_code == 422
    assert client.post("/api/heads/nope/aim", json={"target": "center"}).status_code == 404


def test_uncalibrated_head_returns_409(tmp_path) -> None:
    patch = make_patch()
    app = create_app(AppConfig(universes=[5]), patch=patch, rig_store=make_rig(patch))
    with TestClient(app) as client:
        response = client.post(f"/api/heads/{HEAD}/aim", json={"target": "center"})

    assert response.status_code == 409
    assert "not calibrated" in response.json()["detail"] or "mounting" in response.json()["detail"]


def test_limits_clamp_output_and_persist(client, tmp_path) -> None:
    setup = calibrated().model_copy(update={"limits": HeadLimits(pan_min=0.2, pan_max=0.4)})
    response = client.put(f"/api/heads/{HEAD}", json=setup.model_dump(mode="json"))
    assert response.status_code == 200

    client.post(f"/api/fixtures/{HEAD}/state", json={"values": {"pan": 1.0}})
    assert channels(client)[BASE] == round(0.4 * 255)
    # Stored logical state is untouched; only the output is clamped.
    assert live_state(client)["pan"] == 1.0
    saved = json.loads((tmp_path / "heads.json").read_text())
    assert saved["heads"][HEAD]["limits"]["pan_max"] == 0.4


def test_rest_position(client) -> None:
    assert client.post(f"/api/heads/{HEAD}/rest", json={}).status_code == 409
    setup = calibrated().model_copy(update={"rest": HeadRest(pan=0.5, tilt=0.25)})
    client.put(f"/api/heads/{HEAD}", json=setup.model_dump(mode="json"))
    client.post(f"/api/heads/{HEAD}/rest", json={})

    assert channels(client)[BASE : BASE + 2] == [128, 64]


def test_calibration_fit_endpoint(client) -> None:
    from lichtwerk.positioning import aim_at
    from lichtwerk.fixtures import MotionRange

    truth = HeadSetup(
        position=Point(x=2.0, y=3.0, z=4.0),
        calibration=HeadCalibration(pan_sign=-1, pan_zero_azimuth_deg=30.0, tilt_axis_deg=100.0),
    )
    room = client.get("/api/room").json()
    room["targets"] = [
        {"id": f"c{i}", "name": f"C{i}", "x": x, "y": y, "z": 0.0}
        for i, (x, y) in enumerate([(0, 0), (6, 0), (0, 8), (6, 8)])
    ]
    assert client.put("/api/room", json=room).status_code == 200
    records = []
    for target in room["targets"]:
        aim = aim_at(truth, MotionRange(pan_degrees=540, tilt_degrees=210), Point(x=target["x"], y=target["y"], z=0.0))
        records.append({"pan": aim.pan, "tilt": aim.tilt, "target": target["id"]})

    response = client.post(f"/api/heads/{HEAD}/calibration/fit", json={"observations": records})

    assert response.status_code == 200
    body = response.json()
    assert body["acceptable"] and body["max_deg"] < 0.01
    too_few = client.post(f"/api/heads/{HEAD}/calibration/fit", json={"observations": records[:1]})
    assert too_few.status_code == 409


def test_heads_file_must_match_patch() -> None:
    patch = make_patch()
    assert head_problems(HeadsFile(heads={"ghost": HeadSetup()}), patch)


def test_room_validation_and_persistence_errors_name_the_file(tmp_path) -> None:
    patch = make_patch()
    (tmp_path / "room.json").write_text('{"targets": [{"id": "a", "name": "A", "x": 1}]}')
    with pytest.raises(ValueError, match="room.json"):
        RigStore.load(tmp_path / "room.json", tmp_path / "heads.json", patch)
    (tmp_path / "room.json").write_text("{not json")
    with pytest.raises(ValueError, match="Invalid JSON"):
        RigStore.load(tmp_path / "room.json", tmp_path / "heads.json", patch)
    (tmp_path / "room.json").write_text("{}")
    (tmp_path / "heads.json").write_text('{"heads": {"ghost": {}}}')
    with pytest.raises(ValueError, match="ghost"):
        RigStore.load(tmp_path / "room.json", tmp_path / "heads.json", patch)


def test_missing_files_mean_nothing_configured(tmp_path) -> None:
    rig = RigStore.load(tmp_path / "r.json", tmp_path / "h.json", make_patch())

    assert rig.head(HEAD).calibrated is False
    assert rig.room_file().targets == []


def test_shipped_config_loads_and_has_no_guessed_values() -> None:
    from lichtwerk.config import DEFAULT_HEADS_PATH, DEFAULT_PATCH_PATH, DEFAULT_ROOM_PATH
    from lichtwerk.patch import load_patch

    library = load_fixture_library(DEFAULT_FIXTURES_DIR)
    patch = load_patch(DEFAULT_PATCH_PATH, library, list(range(8)))
    rig = RigStore.load(DEFAULT_ROOM_PATH, DEFAULT_HEADS_PATH, patch)

    assert rig.head_ids() == ["moving-head-1", "moving-head-2"]
    # Coordinates are the owner's club measurements (or still empty); none may lie outside
    # the measured room, which would point to a typo.
    room = rig.room_file().room
    for target in rig.room_file().targets:
        if target.point is not None:
            p = target.point
            assert 0 <= p.x <= room.size_x and 0 <= p.y <= room.size_y and 0 <= p.z <= room.size_z, target.id


def test_37ch_mode_layout_16bit_pan_tilt_and_mirrored_ring() -> None:
    from lichtwerk.fixtures import render_mode

    definition = load_fixture_library(DEFAULT_FIXTURES_DIR)["equinox-fusion-orbit-mkii"]
    mode = definition.mode("37ch")
    values = render_mode(
        mode,
        {
            "pan": 0.5, "tilt": 0.25, "speed": 1.0, "intensity": 1.0, "strobe": 1.0,
            "red": 1.0, "white": 0.5, "ring_intensity": 0.5, "ring_strobe": 1.0,
            "ring_red": 1.0, "ring_blue": 1.0,
        },
    )

    assert len(values) == 37
    assert values[0:4] == [128, 0, 64, 0]  # coarse/fine pan, coarse/fine tilt
    assert values[4] == 0  # speed 1.0 = fastest = DMX 0 (inverted channel)
    assert values[5] == 255 and values[6] == 255
    assert values[7] == 0  # colour macro channel is never used
    assert values[8] == 255 and values[11] == 128
    assert values[12] == 128 and values[13] == 250
    assert values[14:17] == [0, 0, 0]
    # CH18-35: six segments with the same colour
    assert values[17:35] == [255, 0, 255] * 6
    assert values[35] == 0 and values[36] == 0


def test_orbit_speed_knob_right_means_faster_movement() -> None:
    from lichtwerk.fixtures import ChannelDefinition, render_mode

    library = load_fixture_library(DEFAULT_FIXTURES_DIR)
    for mode_name, index in (("37ch", 4), ("16ch", 2)):
        mode = library["equinox-fusion-orbit-mkii"].mode(mode_name)
        assert [render_mode(mode, {"speed": v})[index] for v in (0.0, 0.5, 1.0)] == [255, 128, 0]

    with pytest.raises(Exception, match="invert"):
        ChannelDefinition(attribute="strobe", active_min=1, active_max=255, invert=True)
    with pytest.raises(Exception, match="invert"):
        ChannelDefinition(fixed_dmx=0, label="x", invert=True)


def test_37ch_mode_pan_resolution_is_finer_than_8bit() -> None:
    from lichtwerk.fixtures import render_mode

    mode = load_fixture_library(DEFAULT_FIXTURES_DIR)["equinox-fusion-orbit-mkii"].mode("37ch")
    a = render_mode(mode, {"pan": 0.5})[0:2]
    b = render_mode(mode, {"pan": 0.5 + 1 / 65535})[0:2]

    assert a != b and a[0] == b[0]


def test_6ch_bar_layout_and_native_strobe() -> None:
    from lichtwerk.fixtures import render_mode

    mode = load_fixture_library(DEFAULT_FIXTURES_DIR)["cameo-thunder-wash-100-rgb"].mode("6Ch")

    assert render_mode(mode, {"intensity": 1.0, "red": 1.0, "blue": 0.5}) == [255, 0, 255, 0, 128, 0]
    assert render_mode(mode, {"strobe": 1.0})[1] == 250
    # Manual 6-CH table: only 128-250 is the plain strobe; 11-127 are random/ramp/break effects.
    assert 128 <= render_mode(mode, {"strobe": 0.001})[1] <= 129
    assert render_mode(mode, {"strobe": 0.0})[1] == 0  # open, not the 'closed' 6-10 range


def test_6ch_bar_strobe_rates_never_reach_the_effect_range() -> None:
    from lichtwerk.fixtures import render_mode
    library = load_fixture_library(DEFAULT_FIXTURES_DIR)
    mode = library["cameo-thunder-wash-100-rgb"].mode("6Ch")
    for hz in (0.5, 1, 5, 10, 20, 30):
        level = hz / 30
        assert 128 <= render_mode(mode, {"strobe": level})[1] <= 250


def test_mirror_requires_an_earlier_channel() -> None:
    from lichtwerk.fixtures import FixtureMode

    with pytest.raises(Exception):
        FixtureMode(name="m", channel_count=1, channels=[{"attribute": "red", "mirror": True}])
    with pytest.raises(Exception):
        FixtureMode(name="m", channel_count=2, channels=[{"attribute": "red"}, {"attribute": "red"}])
    ok = FixtureMode(
        name="m", channel_count=2, channels=[{"attribute": "red"}, {"attribute": "red", "mirror": True}]
    )
    assert ok.attributes() == ["intensity", "red"]
