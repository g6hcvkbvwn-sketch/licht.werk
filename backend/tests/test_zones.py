"""Zones, head assignments, include-zone aiming and the beam guard (no hardware involved)."""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from lichtwerk.api import create_app
from lichtwerk.config import DEFAULT_FIXTURES_DIR, AppConfig
from lichtwerk.fixture_service import FixtureController
from lichtwerk.fixtures import load_fixture_library
from lichtwerk.output import SimulatedDmxOutput
from lichtwerk.patch import PatchedFixture, build_patch
from lichtwerk.positioning import HeadCalibration, HeadSetup, Point
from lichtwerk.room import HeadAssignment, HeadsFile, RigStore, Room, RoomFile, Target
from lichtwerk.service import LightingService
from lichtwerk.zones import Zone, room_hit

HEAD = "moving-head-1"
HEAD2 = "moving-head-2"
INTENSITY = 3 + 3  # head 1 at address 4; intensity is its 4th channel (0-based list index 6)
SIZE = (6.0, 8.0, 4.5)

# Head 1 hangs at (2, 3, 4). With this calibration, pan 0 points the beam toward +x and
# tilt 0.5 points straight down; tilting further moves the floor spot toward +x:
# floor x = 2 + 4 * tan(210 * tilt - 105 degrees). Tilt 0.6 lands at x = 3.54.
EXCLUDE = Zone(
    id="booth", name="Booth", surface="floor", mode="exclude",
    corner={"x": 3.0, "y": 2.0}, length=1.0, width=2.0,
)
INCLUDE = Zone(
    id="floor-area", name="Floor area", surface="floor", mode="include",
    corner={"x": 1.0, "y": 1.0}, length=2.0, width=4.0,
)
ALL_FLOOR = Zone(
    id="all-floor", name="All floor", surface="floor", mode="include",
    corner={"x": 0.0, "y": 0.0}, length=6.0, width=8.0,
)


def permit_floor(rig):
    rig.save_zone(ALL_FLOOR)
    rig.set_assignments({HEAD: HeadAssignment(include_zones=["all-floor"], exclude_zones=["booth"])})


def make_patch():
    library = load_fixture_library(DEFAULT_FIXTURES_DIR)
    entries = [
        PatchedFixture(
            id=head_id, name=name, definition="equinox-fusion-orbit-mkii", mode="16ch",
            universe=5, start_address=address, groups=["moving-heads"],
        )
        for head_id, name, address in ((HEAD, "Head 1", 4), (HEAD2, "Head 2", 20))
    ]
    return build_patch(entries, library, [5])


def calibrated() -> HeadSetup:
    return HeadSetup(
        position=Point(x=2.0, y=3.0, z=4.0),
        calibration=HeadCalibration(pan_zero_azimuth_deg=0.0, tilt_axis_deg=105.0),
    )


def make_rig(patch, tmp_path=None, sized: bool = True, assignments=None) -> RigStore:
    room = RoomFile(
        room=Room(size_x=SIZE[0], size_y=SIZE[1], size_z=SIZE[2]) if sized else Room(),
        targets=[
            Target(id="in-zone", name="In zone", x=3.5, y=3.0, z=0.0),
            Target(id="clear", name="Clear", x=2.0, y=6.0, z=0.0),
        ],
        zones=[EXCLUDE, INCLUDE],
        assignments=assignments or {},
    )
    return RigStore(
        patch,
        tmp_path / "room.json" if tmp_path else None,
        tmp_path / "heads.json" if tmp_path else None,
        room,
        HeadsFile(heads={HEAD: calibrated()}),
    )


def make_controller(patch, rig, clock=None):
    lighting = LightingService(SimulatedDmxOutput(), (5,))
    kwargs = {"clock": clock} if clock else {}
    return FixtureController(patch, lighting, rig=rig, **kwargs), lighting


@pytest.fixture
def client(tmp_path):
    patch = make_patch()
    app = create_app(AppConfig(universes=[5]), patch=patch, rig_store=make_rig(patch, tmp_path))
    with TestClient(app) as c:
        yield c


def head(client, head_id: str = HEAD) -> dict:
    return client.get(f"/api/heads/{head_id}").json()


# --- geometry ------------------------------------------------------------------------------


def test_zone_point_and_contains_on_each_kind_of_surface() -> None:
    far = Zone(id="far", name="Far", surface="far_wall", corner={"x": 1.0, "z": 0.5}, length=2.0, width=1.0)
    right = Zone(id="r", name="R", surface="right_wall", corner={"y": 2.0, "z": 1.0}, length=4.0, width=2.0)
    ceiling = Zone(id="c", name="C", surface="ceiling", corner={"x": 0.0, "y": 0.0}, length=6.0, width=8.0)

    assert far.point_at(0.5, 1.0, SIZE) == Point(x=2.0, y=8.0, z=1.5)
    assert right.point_at(0.0, 0.5, SIZE) == Point(x=6.0, y=2.0, z=2.0)
    assert ceiling.point_at(1.0, 1.0, SIZE) == Point(x=6.0, y=8.0, z=4.5)
    assert far.contains(Point(x=3.0, y=8.0, z=1.0), SIZE)  # on the border
    assert not far.contains(Point(x=3.1, y=8.0, z=1.0), SIZE)
    assert not far.contains(Point(x=2.0, y=7.0, z=1.0), SIZE)  # not on the wall
    with pytest.raises(ValueError):
        far.point_at(1.2, 0.0, SIZE)


def test_zone_validation() -> None:
    with pytest.raises(ValidationError, match="'x' and 'y'"):
        Zone(id="z", name="Z", surface="floor", corner={"x": 1.0, "z": 1.0}, length=1.0, width=1.0)
    with pytest.raises(ValidationError):
        Zone(id="z", name="Z", surface="floor", corner={"x": 1.0, "y": 1.0}, length=0.0, width=1.0)
    with pytest.raises(ValidationError, match="0 m or more"):
        Zone(id="z", name="Z", surface="floor", corner={"x": -1.0, "y": 1.0}, length=1.0, width=1.0)
    zone = Zone(id="z", name="Z", surface="left_wall", corner={"y": 7.0, "z": 0.0}, length=2.0, width=1.0)
    assert "beyond the room" in zone.fit_problem(SIZE)
    assert zone.fit_problem((None, None, None)) is None


def test_room_hit_finds_floor_and_walls() -> None:
    origin = Point(x=2.0, y=3.0, z=4.0)
    assert room_hit(origin, (0.0, 0.0, -1.0), SIZE) == Point(x=2.0, y=3.0, z=0.0)
    assert room_hit(origin, (1.0, 0.0, 0.0), SIZE) == Point(x=6.0, y=3.0, z=4.0)
    assert room_hit(origin, (0.0, -1.0, 0.0), SIZE) == Point(x=2.0, y=0.0, z=4.0)
    assert room_hit(Point(x=9.0, y=3.0, z=4.0), (0.0, 0.0, -1.0), SIZE) is None


# --- beam guard ----------------------------------------------------------------------------


def test_fade_across_exclude_zone_darkens_beam_only_while_crossing(clock) -> None:
    patch = make_patch()
    rig = make_rig(patch, assignments={HEAD: HeadAssignment(exclude_zones=["booth"])})
    permit_floor(rig)
    controller, lighting = make_controller(patch, rig, clock)
    controller.apply({HEAD: {"pan": 0.0, "tilt": 0.5, "intensity": 1.0}}, fade_seconds=0.0)
    assert lighting.read_output(5)[INTENSITY] == 255

    controller.apply({HEAD: {"tilt": 0.7}}, fade_seconds=2.0)
    clock.now += 1.0  # tilt 0.6: spot at x = 3.54, inside the zone
    controller.tick()
    assert lighting.read_output(5)[INTENSITY] == 0
    assert controller.state(HEAD)["intensity"] == 1.0  # live state is untouched

    clock.now += 1.0  # tilt 0.7: spot at x = 5.6, past the zone
    controller.tick()
    assert lighting.read_output(5)[INTENSITY] == 255


def test_manual_move_stops_at_the_zone_edge() -> None:
    patch = make_patch()
    rig = make_rig(patch, assignments={HEAD: HeadAssignment(exclude_zones=["booth"])})
    permit_floor(rig)
    controller, _ = make_controller(patch, rig)
    controller.set_values(HEAD, {"pan": 0.0, "tilt": 0.5})

    controller.set_values(HEAD, {"tilt": 0.7})

    tilt = controller.state(HEAD)["tilt"]
    hit = controller.guard.hit(HEAD, 0.0, tilt)
    assert 0.55 < tilt < 0.567  # zone starts at tilt 0.5669
    assert 2.95 < hit.x <= 3.0


def test_manual_move_stays_inside_include_zone() -> None:
    patch = make_patch()
    rig = make_rig(patch, assignments={HEAD: HeadAssignment(aim_zone="floor-area")})
    controller, _ = make_controller(patch, rig)
    controller.set_values(HEAD, {"pan": 0.0, "tilt": 0.5})  # (2, 3): inside

    controller.set_values(HEAD, {"tilt": 0.7})

    hit = controller.guard.hit(HEAD, 0.0, controller.state(HEAD)["tilt"])
    assert 2.95 < hit.x <= 3.0  # include zone ends at x = 3


def test_guard_is_inactive_without_room_size() -> None:
    patch = make_patch()
    rig = make_rig(patch, sized=False, assignments={HEAD: HeadAssignment(exclude_zones=["booth"])})
    controller, lighting = make_controller(patch, rig)
    controller.set_values(HEAD, {"pan": 0.0, "tilt": 0.6, "intensity": 1.0})

    assert controller.guard.status(HEAD).reason == "room size not measured"
    assert controller.guard.status(HEAD2).active is False
    assert lighting.read_output(5)[INTENSITY] == 0  # missing geometry fails closed


# --- assignments and API -------------------------------------------------------------------


def test_include_zone_spot_moves_beam_inside_the_rectangle(client) -> None:
    response = client.put(f"/api/positions/{HEAD}/assignment", json={"aim_zone": "floor-area"})
    assert response.status_code == 200 and response.json()["notes"] == []
    hit = head(client)["beam_hit"]
    assert hit["x"] == pytest.approx(2.0, abs=1e-6) and hit["y"] == pytest.approx(3.0, abs=1e-6)

    response = client.post(f"/api/positions/{HEAD}/spot", json={"u": 1.0, "v": 1.0})
    assert response.status_code == 200
    info = response.json()["heads"][0]
    assert info["beam_hit"]["x"] == pytest.approx(3.0, abs=1e-6)
    assert info["beam_hit"]["y"] == pytest.approx(5.0, abs=1e-6)
    assert (info["spot_u"], info["spot_v"]) == (1.0, 1.0)

    assert client.post(f"/api/positions/{HEAD}/spot", json={"u": 1.5, "v": 0.0}).status_code == 422

    # Applying the zone again starts in its geometric centre, not at the last spot.
    response = client.put(f"/api/positions/{HEAD}/assignment", json={"aim_zone": "floor-area"})
    info = response.json()["heads"][0]
    assert (info["spot_u"], info["spot_v"]) == (0.5, 0.5)
    assert info["beam_hit"]["x"] == pytest.approx(2.0, abs=1e-6)
    assert info["beam_hit"]["y"] == pytest.approx(3.0, abs=1e-6)


def test_spot_without_include_zone_is_a_conflict(client) -> None:
    response = client.post(f"/api/positions/{HEAD}/spot", json={"u": 0.5, "v": 0.5})
    assert response.status_code == 409 and "no include zone" in response.json()["detail"]


def test_group_assignment_sets_every_head_and_reports_unmeasured_ones(client, tmp_path) -> None:
    response = client.put(
        "/api/positions/moving-heads/assignment",
        json={"aim_target": "clear", "exclude_zones": ["booth"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert [h["assignment"]["aim_target"] for h in body["heads"]] == ["clear", "clear"]
    assert body["notes"] == ["Head 2: not measured/calibrated yet, saved but not moved."]
    assert body["heads"][1]["guard_reason"] == "head position not measured"
    saved = json.loads((tmp_path / "room.json").read_text())["assignments"]
    assert saved[HEAD2] == {"aim_target": "clear", "aim_zone": None, "include_zones": [], "exclude_zones": ["booth"]}


def test_named_target_is_a_static_exception_even_inside_an_exclusion(client) -> None:
    client.put(f"/api/positions/{HEAD}/assignment", json={"exclude_zones": ["booth"]})

    response = client.post(f"/api/heads/{HEAD}/aim", json={"target": "in-zone"})
    assert response.status_code == 200
    response = client.put(f"/api/positions/{HEAD}/assignment", json={"aim_target": "in-zone", "exclude_zones": ["booth"]})
    assert response.status_code == 200
    assert response.json()["notes"] == []


def test_beam_blocked_status_is_reported(client) -> None:
    client.post(f"/api/fixtures/{HEAD}/state", json={"values": {"pan": 0.0, "tilt": 0.6}})
    assert head(client)["beam_blocked_by"] is None  # no exclude zone assigned yet

    client.put(f"/api/positions/{HEAD}/assignment", json={"exclude_zones": ["booth"]})

    assert head(client)["beam_blocked_by"] == "Booth"


def test_assignment_validation(client) -> None:
    def put(body, target=HEAD):
        return client.put(f"/api/positions/{target}/assignment", json=body)

    assert put({"aim_zone": "booth"}).status_code == 200  # per-strip permission overrides legacy mode
    assert put({"exclude_zones": ["floor-area"]}).status_code == 200
    assert put({"aim_zone": "nope"}).status_code == 422
    assert put({"aim_target": "clear", "aim_zone": "floor-area"}).status_code == 422
    assert put({"exclude_zones": ["booth", "booth"]}).status_code == 422
    assert put({"aim_target": "clear"}, target="nope").status_code == 404


def test_zone_crud_and_delete_removes_assignments(client) -> None:
    client.put(f"/api/positions/{HEAD}/assignment", json={"aim_zone": "floor-area", "exclude_zones": ["booth"]})
    wall = {
        "id": "far", "name": "Far wall", "surface": "far_wall", "mode": "exclude",
        "corner": {"x": 0, "z": 0}, "length": 6, "width": 2,
    }
    assert client.put("/api/zones/far", json=wall).status_code == 200
    assert client.put("/api/zones/far", json={**wall, "length": 7}).status_code == 422  # beyond room
    assert client.put("/api/zones/other", json=wall).status_code == 422  # id mismatch

    assert client.delete("/api/zones/booth").status_code == 204
    assert client.delete("/api/zones/booth").status_code == 404
    room = client.get("/api/room").json()
    assert [z["id"] for z in room["zones"]] == ["floor-area", "far"]
    assert room["assignments"][HEAD] == {"aim_target": None, "aim_zone": "floor-area", "include_zones": ["floor-area"], "exclude_zones": []}

    # Legacy mode is not a global permission toggle: existing strip settings stay intact.
    response = client.put("/api/zones/floor-area", json={**INCLUDE.model_dump(), "mode": "exclude"})
    assert response.status_code == 200
    assert head(client)["assignment"]["include_zones"] == ["floor-area"]


def test_delete_target_clears_aim(client) -> None:
    client.put(f"/api/positions/{HEAD}/assignment", json={"aim_target": "clear"})

    assert client.delete("/api/targets/clear").status_code == 204

    assert "clear" not in [t["id"] for t in client.get("/api/room").json()["targets"]]
    assert head(client)["assignment"]["aim_target"] is None


def test_room_file_rejects_assignment_for_unknown_head(tmp_path) -> None:
    patch = make_patch()
    document = RoomFile(assignments={"bar-1": HeadAssignment(exclude_zones=[])})
    path = tmp_path / "room.json"
    path.write_text(json.dumps(document.model_dump(mode="json")))

    with pytest.raises(ValueError, match="not a patched moving head"):
        RigStore.load(path, tmp_path / "heads.json", patch)
