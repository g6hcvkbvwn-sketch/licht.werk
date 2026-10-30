from fastapi.testclient import TestClient

from lichtwerk.api import create_app
from lichtwerk.config import DEFAULT_FIXTURES_DIR, AppConfig, OlaConfig
from lichtwerk.fixtures import load_fixture_library
from lichtwerk.patch import PatchedFixture, build_patch

LIBRARY = load_fixture_library(DEFAULT_FIXTURES_DIR)


def bar(fixture_id: str = "led-bar-1", start: int = 1, enabled: bool = True) -> PatchedFixture:
    return PatchedFixture(
        id=fixture_id,
        name="LED bar",
        definition="cameo-thunder-wash-100-rgb",
        mode="3Ch3",
        universe=5,
        start_address=start,
        enabled=enabled,
    )


def make_client(*entries: PatchedFixture, real_output=None, config=None) -> TestClient:
    settings = config or AppConfig(universes=[5])
    patch = build_patch(list(entries or [bar()]), LIBRARY, settings.universes)
    return TestClient(
        create_app(settings, real_output=real_output, patch=patch, fixture_library=LIBRARY)
    )


def universe5(client: TestClient) -> list[int]:
    return client.get("/api/universes/5").json()["channels"]


def test_fixture_list_reports_patch_and_dark_initial_state() -> None:
    with make_client() as client:
        fixtures = client.get("/api/fixtures").json()

    assert fixtures == [
        {
            "id": "led-bar-1",
            "name": "LED bar",
            "manufacturer": "Cameo",
            "model": "Thunder Wash 100 RGB",
            "fixture_type": "led_bar",
            "mode": "3Ch3",
            "universe": 5,
            "start_address": 1,
            "channel_count": 3,
            "enabled": True,
            "groups": [],
            "attributes": ["intensity", "red", "green", "blue"],
            "state": {"intensity": 0.0, "red": 0.0, "green": 0.0, "blue": 0.0},
        }
    ]


def test_fixture_definitions_endpoint_lists_library() -> None:
    with make_client() as client:
        definitions = client.get("/api/fixture-definitions").json()

    assert [d["id"] for d in definitions] == [
        "cameo-q-spot-15-rgbw-bk",
        "cameo-thunder-wash-100-rgb",
        "equinox-fusion-orbit-mkii",
        "look-unique-2-1",
    ]


def test_logical_state_maps_to_thunder_wash_channels() -> None:
    with make_client() as client:
        client.post("/api/output/blackout", json={"active": False})
        response = client.post(
            "/api/fixtures/led-bar-1/state",
            json={"values": {"intensity": 0.5, "red": 1.0, "green": 0.5}},
        )
        channels = universe5(client)

    assert response.status_code == 200
    assert response.json()["fixture"]["state"]["red"] == 1.0
    assert channels[:4] == [128, 64, 0, 0]


def test_partial_update_keeps_other_attributes() -> None:
    with make_client() as client:
        client.post("/api/output/blackout", json={"active": False})
        client.post(
            "/api/fixtures/led-bar-1/state",
            json={"values": {"intensity": 1.0, "red": 1.0}},
        )
        client.post("/api/fixtures/led-bar-1/state", json={"values": {"blue": 1.0}})
        channels = universe5(client)

    assert channels[:3] == [255, 0, 255]


def test_fixture_at_other_address_writes_only_its_channels() -> None:
    with make_client(bar("a", 1), bar("b", 10)) as client:
        client.post("/api/output/blackout", json={"active": False})
        client.post("/api/fixtures/b/state", json={"values": {"intensity": 1.0, "green": 1.0}})
        channels = universe5(client)

    assert channels[:3] == [0, 0, 0]
    assert channels[9:12] == [0, 255, 0]


def test_invalid_state_commands_are_rejected() -> None:
    with make_client() as client:
        unknown_attr = client.post(
            "/api/fixtures/led-bar-1/state", json={"values": {"pan": 0.5}}
        )
        out_of_range = client.post(
            "/api/fixtures/led-bar-1/state", json={"values": {"red": 1.5}}
        )
        unknown_fixture = client.post("/api/fixtures/nope/state", json={"values": {"red": 1.0}})
        empty = client.post("/api/fixtures/led-bar-1/state", json={"values": {}})
        channels = universe5(client)

    assert unknown_attr.status_code == 422
    assert out_of_range.status_code == 422
    assert unknown_fixture.status_code == 404
    assert empty.status_code == 422
    assert channels[:3] == [0, 0, 0]


def test_disabled_fixture_rejects_commands() -> None:
    with make_client(bar(enabled=False)) as client:
        response = client.post(
            "/api/fixtures/led-bar-1/state", json={"values": {"intensity": 1.0}}
        )

    assert response.status_code == 409


def test_blackout_hides_fixture_output_without_losing_state() -> None:
    with make_client() as client:
        client.post(
            "/api/fixtures/led-bar-1/state",
            json={"values": {"intensity": 1.0, "red": 1.0}},
        )
        client.post("/api/output/blackout", json={"active": True})
        dark = universe5(client)
        state = client.get("/api/fixtures").json()[0]["state"]
        client.post("/api/output/blackout", json={"active": False})
        restored = universe5(client)

    assert dark[:3] == [0, 0, 0]
    assert state["red"] == 1.0
    assert restored[:3] == [255, 0, 0]


class RecordingOutput:
    def __init__(self) -> None:
        self.frames: list[tuple[int, tuple[int, ...]]] = []

    def send_universe(self, universe: int, channels: tuple[int, ...]) -> None:
        self.frames.append((universe, channels))


def test_fixture_commands_reach_configured_real_output_after_blackout_release() -> None:
    real = RecordingOutput()
    config = AppConfig.validate_configuration(
        {
            "output_mode": "ola",
            "physical_output_enabled": True,
            "universes": [5],
            "ola": OlaConfig(allowed_universes=[5]).model_dump(),
        }
    )
    with make_client(real_output=real, config=config) as client:
        client.post(
            "/api/fixtures/led-bar-1/state",
            json={"values": {"intensity": 1.0, "red": 1.0}},
        )
        assert real.frames and all(max(frame[1]) == 0 for frame in real.frames)
        client.post("/api/output/blackout", json={"active": False})

    armed_frames = [frame for frame in real.frames if frame[1][0] == 255]
    assert armed_frames and armed_frames[0][0] == 5


def test_diagnostics_lists_patch_and_setup_warnings() -> None:
    with make_client(bar(enabled=False)) as client:
        body = client.get("/api/diagnostics").json()

    assert body["output_mode"] == "simulated"
    assert body["armed_universes"] == [] and body["simulated_universes"] == [5]
    assert body["patch"] == [
        {
            "id": "led-bar-1", "name": "LED bar", "universe": 5, "first_address": 1,
            "last_address": 3, "mode": "3Ch3", "enabled": False,
        }
    ]
    assert any("disabled" in w for w in body["warnings"])


def test_diagnostics_reports_recent_errors_without_stack_traces() -> None:
    import logging

    with make_client() as client:
        logging.getLogger("lichtwerk.test").error("Something went wrong in the output")
        body = client.get("/api/diagnostics").json()

    assert [e["message"] for e in body["recent_errors"]] == ["Something went wrong in the output"]
