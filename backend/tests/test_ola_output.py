import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import pytest
from fastapi.testclient import TestClient

from lichtwerk.api import create_app
from lichtwerk.config import AppConfig
from lichtwerk.output import GatedDmxOutput, OlaHttpDmxOutput, OutputError


class RecordingOutput:
    def __init__(self) -> None:
        self.sent: list[tuple[int, tuple[int, ...]]] = []
        self.fail = False

    def send_universe(self, universe, slots) -> None:
        if self.fail:
            raise OutputError("olad down")
        self.sent.append((universe, tuple(slots)))


def ola_config(enabled: bool = True) -> AppConfig:
    return AppConfig.validate_configuration(
        {
            "output_mode": "ola",
            "physical_output_enabled": enabled,
            "universes": [5, 6],
            "ola": {"allowed_universes": [6]},
        }
    )


def test_configured_output_starts_in_blackout_and_only_allowed_universes() -> None:
    real = RecordingOutput()
    with TestClient(create_app(ola_config(), real_output=real)) as client:
        client.post("/api/commands/set-channel", json={"universe": 6, "channel": 1, "value": 9})
        assert real.sent and all(universe == 6 and max(frame) == 0 for universe, frame in real.sent)

        refused = client.post("/api/output/arm", json={"armed": True, "confirm_universes": [5]})
        assert refused.status_code == 422
        client.post("/api/output/blackout", json={"active": False})

        armed = client.post("/api/output/arm", json={"armed": True, "confirm_universes": [6]})
        assert armed.json()["physical_output_enabled"] is True
        assert all(u == 6 for u, _ in real.sent)
        assert real.sent[-1][1][0] == 9

        client.post("/api/commands/set-channel", json={"universe": 5, "channel": 1, "value": 7})
        assert all(u == 6 for u, _ in real.sent)


def test_blackout_and_disarm_send_zeros() -> None:
    real = RecordingOutput()
    with TestClient(create_app(ola_config(), real_output=real)) as client:
        client.post("/api/commands/set-channel", json={"universe": 6, "channel": 1, "value": 200})
        client.post("/api/output/arm", json={"armed": True, "confirm_universes": [6]})
        client.post("/api/output/blackout", json={"active": True})
        assert max(real.sent[-1][1]) == 0

        client.post("/api/output/blackout", json={"active": False})
        assert real.sent[-1][1][0] == 200
        client.post("/api/output/arm", json={"armed": False})
        assert max(real.sent[-1][1]) == 0
        count = len(real.sent)
        client.post("/api/commands/set-channel", json={"universe": 6, "channel": 1, "value": 5})
        assert len(real.sent) == count


def test_arming_requires_config_enable() -> None:
    real = RecordingOutput()
    with TestClient(create_app(ola_config(enabled=False), real_output=real)) as client:
        response = client.post("/api/output/arm", json={"armed": True, "confirm_universes": [6]})
        assert response.status_code == 409 and real.sent == []

    with TestClient(create_app(AppConfig(universes=[6]))) as client:
        assert client.post("/api/output/arm", json={"armed": True, "confirm_universes": [6]}).status_code == 409


def test_failure_disarms_and_is_reported() -> None:
    real = RecordingOutput()
    with TestClient(create_app(ola_config(), real_output=real)) as client:
        client.post("/api/output/arm", json={"armed": True, "confirm_universes": [6]})
        client.post("/api/output/blackout", json={"active": False})
        real.fail = True
        client.post("/api/commands/set-channel", json={"universe": 6, "channel": 1, "value": 1})
        status = client.get("/api/status").json()
        assert status["physical_output_enabled"] is False
        assert "olad down" in status["output_error"]
        sent = len(real.sent)
        real.fail = False
        client.post("/api/commands/set-channel", json={"universe": 6, "channel": 1, "value": 2})
        client.get("/api/status")
        assert len(real.sent) == sent
        retried = client.post("/api/output/retry").json()
        assert retried["physical_output_enabled"] is True
        assert len(real.sent) > sent
        assert real.sent[-1][1][0] == 2
        assert client.get("/api/status").json()["output_error"] is None


def test_output_os_error_closes_gate_until_explicit_retry() -> None:
    class FailedOutput:
        def send_universe(self, universe, slots):
            raise OSError("connection reset")

    gate = GatedDmxOutput(FailedOutput(), [6])
    gate.arm()
    gate.send_universe(6, [0] * 512)
    assert not gate.armed
    assert gate.last_error == "connection reset"


def test_retry_failure_stays_disabled_and_simulation_cannot_enable_output() -> None:
    real = RecordingOutput()
    real.fail = True
    with TestClient(create_app(ola_config(), real_output=real)) as client:
        assert client.get("/api/status").json()["physical_output_enabled"] is False
        assert client.post("/api/output/retry").json()["physical_output_enabled"] is False
        assert "olad down" in client.get("/api/status").json()["output_error"]
    with TestClient(create_app(AppConfig(universes=[6]), real_output=real)) as client:
        assert client.post("/api/output/retry").status_code == 409
    with TestClient(create_app(ola_config(enabled=False), real_output=real)) as client:
        assert client.post("/api/output/retry").status_code == 409


def test_shutdown_zeroes_armed_output() -> None:
    real = RecordingOutput()
    with TestClient(create_app(ola_config(), real_output=real)) as client:
        client.post("/api/commands/set-channel", json={"universe": 6, "channel": 2, "value": 50})
        client.post("/api/output/arm", json={"armed": True, "confirm_universes": [6]})
    assert max(real.sent[-1][1]) == 0


def test_gate_requires_universes() -> None:
    with pytest.raises(ValueError):
        GatedDmxOutput(RecordingOutput(), [])


def test_ola_config_validation() -> None:
    with pytest.raises(ValueError, match="requires an"):
        AppConfig.validate_configuration({"output_mode": "ola", "universes": [6]})
    with pytest.raises(ValueError, match="configured universes"):
        AppConfig.validate_configuration(
            {"output_mode": "ola", "universes": [6], "ola": {"allowed_universes": [7]}}
        )
    with pytest.raises(ValueError):
        AppConfig.validate_configuration(
            {"output_mode": "ola", "universes": [6],
             "ola": {"host": "192.168.1.5", "allowed_universes": [6]}}
        )


def test_ola_http_adapter_posts_full_universe_to_local_stub() -> None:
    received: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            received.update(parse_qs(self.rfile.read(length).decode()))
            received["path"] = self.path
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        slots = [0] * 512
        slots[0] = 255
        OlaHttpDmxOutput("127.0.0.1", server.server_port).send_universe(6, slots)
    finally:
        thread.join(timeout=5)
        server.server_close()

    assert received["path"] == "/set_dmx"
    assert received["u"] == ["6"]
    assert received["d"][0].split(",")[:2] == ["255", "0"]
    assert len(received["d"][0].split(",")) == 512


def test_ola_http_adapter_reports_unreachable_olad() -> None:
    with pytest.raises(OutputError):
        OlaHttpDmxOutput("127.0.0.1", 1, timeout=0.2).send_universe(6, [0] * 512)
