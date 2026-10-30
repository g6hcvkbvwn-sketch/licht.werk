"""Commanded haze feedback is distinct from lighting output and preview."""

import pytest
from fastapi.testclient import TestClient

from lichtwerk.api import create_app
from lichtwerk.config import AppConfig, DEFAULT_FIXTURES_DIR
from lichtwerk.fixtures import load_fixture_library
from lichtwerk.patch import PatchedFixture, build_patch


def test_haze_map_reports_live_and_preview_independent_of_lighting_blackout():
    patch = build_patch([PatchedFixture(
        id="hazer-1", name="Hazer", definition="look-unique-2-1", mode="2ch",
        universe=5, start_address=99,
    )], load_fixture_library(DEFAULT_FIXTURES_DIR), [5])
    app = create_app(AppConfig(universes=[5]), patch=patch)
    with TestClient(app) as client:
        controller = app.state.fixture_controller
        controller.set_values("hazer-1", {"pump": 0.25, "fan": 0.5})
        controller.set_master(0)
        first = client.get("/api/map").json()["fixtures"][0]
        assert first["live_haze"] == {"pump": 0.25, "fan": 0.5}
        assert first["preview_haze"] is None
        client.post("/api/preview", json={"active": True}).raise_for_status()
        controller.set_values("hazer-1", {"pump": 0.75, "fan": 1.0})
        staged = client.get("/api/map").json()["fixtures"][0]
        assert staged["live_haze"] == first["live_haze"]
        assert staged["preview_haze"] == {"pump": 0.75, "fan": 1.0}
        controller.hold("test-haze", {"hazer-1": {"pump": 1.0, "fan": 1.0}})
        held = client.get("/api/map").json()["fixtures"][0]
        assert held["live_haze"]["pump"] == 1
        assert held["preview_haze"]["pump"] == 0.75
        controller.release("test-haze")
        client.post("/api/preview", json={"active": False}).raise_for_status()
        promoted = client.get("/api/map").json()["fixtures"][0]
        assert promoted["live_haze"]["pump"] == pytest.approx(0.75)
        assert promoted["preview_haze"] is None
