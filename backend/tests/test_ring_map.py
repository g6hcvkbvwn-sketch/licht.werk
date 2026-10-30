"""Room monitors show rendered ring sections independently of the beam."""

import pytest
from fastapi.testclient import TestClient

from lichtwerk.api import create_app
from lichtwerk.config import AppConfig
from lichtwerk.room_map import ring_colours
from lichtwerk.strobe import strobe_hz
from test_apc40 import DEFAULT_FIXTURES_DIR, build_patch, club_patch, load_fixture_library


def test_map_ring_sections_follow_preview_go_master_blackout_and_ring_strobe():
    patch = club_patch()
    app = create_app(AppConfig(universes=[5]), patch=patch)
    with TestClient(app) as client:
        controller = app.state.fixture_controller
        controller.set_values("moving-head-1", {
            "intensity": 0.0, "ring_intensity": 0.8, "ring_red": 1.0,
            "ring_2_red": 0.0, "ring_2_blue": 1.0, "ring_strobe": 0.5,
        })
        controller.set_master(0.5)
        client.post("/api/output/blackout", json={"active": False}).raise_for_status()

        def head():
            return next(f for f in client.get("/api/map").json()["fixtures"] if f["id"] == "moving-head-1")

        live = head()
        assert live["live"] == [0.0, 0.0, 0.0]
        assert len(live["live_ring"]) == 6
        assert live["live_ring"][0] == pytest.approx([0.4, 0.0, 0.0])
        assert live["live_ring"][1] == pytest.approx([0.0, 0.0, 0.4])
        assert live["live_strobe_hz"] is None
        assert live["live_ring_strobe_hz"] == strobe_hz(patch.get("moving-head-1"), 0.5, "ring_strobe")

        client.post("/api/preview", json={"active": True}).raise_for_status()
        controller.set_values("moving-head-1", {
            "ring_red": 0.0, "ring_blue": 0.0, "ring_green": 1.0, "ring_strobe": 0.0,
        })
        staged = head()
        assert staged["live_ring"] == live["live_ring"]
        assert staged["preview_ring"] == [[0.0, 0.4, 0.0]] * 6
        assert staged["preview_ring_strobe_hz"] is None
        client.post("/api/output/blackout", json={"active": True}).raise_for_status()
        assert head()["live_ring"] == [[0.0, 0.0, 0.0]] * 6
        assert head()["preview_ring"] == staged["preview_ring"]
        client.post("/api/preview", json={"active": False}).raise_for_status()
        client.post("/api/output/blackout", json={"active": False}).raise_for_status()
        assert head()["live_ring"] == staged["preview_ring"]
        controller.set_master(0.0)
        assert head()["live_ring"] == [[0.0, 0.0, 0.0]] * 6


def test_map_ring_reports_effect_output_rather_than_manual_base():
    app = create_app(AppConfig(universes=[5]), patch=club_patch())
    with TestClient(app) as client:
        controller = app.state.fixture_controller
        controller.set_values("moving-head-1", {"ring_intensity": 0.8, "ring_red": 1.0})
        controller.set_master(0.5)
        client.post("/api/output/blackout", json={"active": False}).raise_for_status()
        for family, params in (
            ("chase", {"maximum": 0.5, "size": 0.0}),
            ("rainbow", {"colour_mode": "palette", "palette": ["#0000ff", "#0000ff"]}),
        ):
            endpoint = f"/api/fx/2/{family}/slots"
            definition = client.get(endpoint + "?component=ring").json()["slots"][0]
            definition["params"].update(params)
            client.put(endpoint + "/0?component=ring", json=definition).raise_for_status()
            client.post(endpoint + "/0/start?component=ring").raise_for_status()
        fixture = next(f for f in client.get("/api/map").json()["fixtures"] if f["id"] == "moving-head-1")
        for colour in fixture["live_ring"]:
            assert colour == pytest.approx([0.0, 0.0, 0.2])
        assert controller.state("moving-head-1")["ring_1_red"] == 1.0
        bar = next(f for f in client.get("/api/map").json()["fixtures"] if f["id"] == "led-bar-1")
        assert bar["live_ring"] == bar["preview_ring"] == []


@pytest.mark.parametrize("enabled", [True, False])
def test_uniform_ring_mode_and_disabled_fixture(enabled):
    entries = [
        f.patch.model_copy(update={"mode": "16ch", "enabled": enabled})
        for f in club_patch().fixtures if f.patch.id == "moving-head-1"
    ]
    fixture = build_patch(entries, load_fixture_library(DEFAULT_FIXTURES_DIR), [5]).get("moving-head-1")
    assert ring_colours(fixture, {
        "ring_red": 1.0, "ring_green": 0.5, "ring_blue": 0.0, "ring_intensity": 0.5,
    }, 0.4) == ([(0.2, 0.1, 0.0)] if enabled else [(0.0, 0.0, 0.0)])
