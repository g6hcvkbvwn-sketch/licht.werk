from fastapi.testclient import TestClient

from lichtwerk.api import create_app
from lichtwerk.config import AppConfig
from lichtwerk.output import SimulatedDmxOutput


def make_client() -> TestClient:
    return TestClient(create_app(AppConfig(universes=[6])))


def test_status_labels_simulation_and_disabled_physical_output() -> None:
    with make_client() as client:
        response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "output_mode": "simulated",
        "physical_output_enabled": False,
        "blackout_active": True,
        "universes": [6],
        "real_output_available": False,
        "real_output_universes": [],
        "output_error": None,
        "master": 1.0,
        "active_scene": None,
        "transition_active": False,
        "preview_active": False,
        "setup_overrides": [],
    }


def test_set_channel_and_inspect_complete_512_slot_buffer() -> None:
    with make_client() as client:
        client.post("/api/output/blackout", json={"active": False})
        changed = client.post(
            "/api/commands/set-channel",
            json={"universe": 6, "channel": 1, "value": 255},
        )
        inspected = client.get("/api/universes/6")

    assert changed.status_code == 200
    assert changed.json()["physical_output_enabled"] is False
    assert inspected.status_code == 200
    assert inspected.json()["slot_count"] == 512
    assert len(inspected.json()["channels"]) == 512
    assert inspected.json()["channels"][:3] == [255, 0, 0]


def test_invalid_channel_values_are_rejected() -> None:
    with make_client() as client:
        response = client.post(
            "/api/commands/set-channel",
            json={"universe": 6, "channel": 513, "value": 256},
        )

    assert response.status_code == 422


def test_blackout_is_visible_and_restores_buffer_when_released() -> None:
    with make_client() as client:
        client.post(
            "/api/commands/set-channel",
            json={"universe": 6, "channel": 1, "value": 255},
        )
        blacked_out = client.post("/api/output/blackout", json={"active": True})
        dark_buffer = client.get("/api/universes/6").json()
        restored = client.post("/api/output/blackout", json={"active": False})
        lit_buffer = client.get("/api/universes/6").json()

    assert blacked_out.json()["blackout_active"] is True
    assert dark_buffer["blackout_active"] is True
    assert set(dark_buffer["channels"]) == {0}
    assert restored.json()["blackout_active"] is False
    assert lit_buffer["channels"][0] == 255


def test_default_app_uses_only_simulated_output_adapter() -> None:
    app = create_app(AppConfig(universes=[6]))

    assert isinstance(app.state.dmx_output, SimulatedDmxOutput)


def test_status_polls_do_not_reapply_startup_blackout() -> None:
    with make_client() as client:
        assert client.get("/api/status").json()["blackout_active"] is True
        client.post("/api/output/blackout", json={"active": False})
        for _ in range(3):
            assert client.get("/api/status").json()["blackout_active"] is False
