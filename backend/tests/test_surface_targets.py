"""Calculated surface targets are read-only snapshots, never hardware commands."""

import pytest
from fastapi.testclient import TestClient

from lichtwerk.api import create_app
from lichtwerk.config import AppConfig
from lichtwerk.effect_engine import EffectEngine
from lichtwerk.effects import EffectType
from lichtwerk.position_service import PositionService
from lichtwerk.positioning import Point, aim_at
from lichtwerk.tempo import TempoClock
from conftest import FakeClock
from test_apc40 import club_patch
from test_zones import HEAD, HEAD2, make_controller, make_rig


@pytest.mark.parametrize("point", [
    Point(x=3.0, y=3.0, z=0.0),
    Point(x=6.0, y=3.0, z=2.0),
    Point(x=5.0, y=3.0, z=4.5),
])
def test_current_surface_point_matches_first_surface_without_mutation(point):
    patch = club_patch()
    rig = make_rig(patch)
    controller, lighting = make_controller(patch, rig)
    positions = PositionService(controller, rig)
    aim = aim_at(rig.head(HEAD), patch.get(HEAD).definition.motion, point)
    controller.set_values(HEAD, {"pan": aim.pan, "tilt": aim.tilt})
    before = controller.state(HEAD), rig.room_file(), lighting.read_output(5)
    result = positions.current_surface_point(HEAD)
    assert (result.x, result.y, result.z) == pytest.approx((point.x, point.y, point.z))
    assert (controller.state(HEAD), rig.room_file(), lighting.read_output(5)) == before


def test_surface_snapshot_api_validation_and_preview():
    patch = club_patch()
    rig = make_rig(patch)
    with TestClient(create_app(AppConfig(universes=[5]), patch=patch, rig_store=rig)) as client:
        assert client.get(f"/api/heads/{HEAD}/surface-point").status_code == 200
        response = client.get(f"/api/heads/{HEAD2}/surface-point")
        assert response.status_code == 409 and "head position not measured" in response.json()["detail"]
        assert client.get("/api/heads/missing/surface-point").status_code == 404
        assert client.post("/api/preview", json={"active": True}).status_code == 200
        response = client.get(f"/api/heads/{HEAD}/surface-point")
        assert response.status_code == 422 and "Finish preview" in response.json()["detail"]


def test_snapshot_uses_rendered_fx_instead_of_base_position():
    patch = club_patch()
    rig = make_rig(patch)
    clock = FakeClock()
    controller, _ = make_controller(patch, rig, clock)
    positions = PositionService(controller, rig)
    base = aim_at(rig.head(HEAD), patch.get(HEAD).definition.motion, Point(x=3.0, y=3.0, z=0.0))
    controller.set_values(HEAD, {"pan": base.pan, "tilt": base.tilt})
    effects = EffectEngine(patch, TempoClock(clock=clock))
    controller.set_effects(effects)
    effects.start("head", EffectType.CIRCLE, 0, [HEAD])
    output = controller.output_state(HEAD)
    expected = controller.guard.hit(HEAD, output["pan"], output["tilt"])
    result = positions.current_surface_point(HEAD)
    assert result == expected
    assert result != Point(x=3.0, y=3.0, z=0.0)
    assert controller.state(HEAD)["pan"] == base.pan


def test_controller_definition_edit_delete_preserves_ids_order_and_cleans_assignments(tmp_path):
    patch = club_patch()
    rig = make_rig(patch, tmp_path)
    with TestClient(create_app(AppConfig(universes=[5]), patch=patch, rig_store=rig)) as client:
        original = client.get("/api/room").json()
        point = client.get(f"/api/heads/{HEAD}/surface-point").json()
        document = {**original, "targets": original["targets"] + [
            {"id": "surface-target", "name": "Surface target", **point},
        ]}
        assert client.put("/api/room", json=document).status_code == 200
        assignment = {
            "target": "moving-heads", "aim_target": "surface-target",
            "include_zones": ["floor-area"], "exclude_zones": ["booth"],
        }
        assert client.put("/api/positions/strips/apc40:2", json=assignment).status_code == 200
        document = client.get("/api/room").json()
        document["targets"][-1]["name"] = "Renamed"
        document["targets"][-1]["x"] = 1.0
        assert client.put("/api/room", json=document).status_code == 200
        assert [t.id for t in rig.room_file().targets] == ["in-zone", "clear", "surface-target"]
        assert rig.room_file().strip_assignments["apc40:2"].aim_target == "surface-target"
        zone = original["zones"][1]
        zone["name"] = "Renamed area"
        assert client.put(f"/api/zones/{zone['id']}", json=zone).status_code == 200
        assert [z.id for z in rig.room_file().zones] == ["booth", "floor-area"]
        assert client.delete("/api/targets/surface-target").status_code == 204
        assert rig.room_file().strip_assignments["apc40:2"].aim_target is None
        assert client.delete("/api/zones/floor-area").status_code == 204
        assert rig.room_file().strip_assignments["apc40:2"].include_zones == []
        assert client.post("/api/preview", json={"active": True}).status_code == 200
        assert client.delete("/api/targets/clear").status_code == 422
        assert client.delete("/api/zones/booth").status_code == 422
