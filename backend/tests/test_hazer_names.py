"""Normalized haze commands and stable, optionally persistent display names."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lichtwerk.api import create_app
from lichtwerk.config import DEFAULT_FIXTURES_DIR, AppConfig
from lichtwerk.fixtures import load_fixture_library
from lichtwerk.patch import PatchedFixture, build_patch
from lichtwerk.positioning import HeadRest, HeadSetup
from lichtwerk.room import HeadsFile, RigStore

from conftest import rig_patch


def haze_patch():
    return build_patch(
        [
            PatchedFixture(
                id="hazer-1", name="Hazer", definition="look-unique-2-1", mode="2ch",
                universe=5, start_address=99,
            ),
            PatchedFixture(
                id="head-1", name="Head", definition="equinox-fusion-orbit-mkii",
                mode="16ch", universe=5, start_address=1,
            ),
        ],
        load_fixture_library(DEFAULT_FIXTURES_DIR),
        [5],
    )


@pytest.fixture
def names_path():
    path = Path("backend/tests/.display-names-test.json")
    assert not path.exists()
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".bak").unlink(missing_ok=True)


def test_haze_starts_at_zero_without_lighting_attributes_and_ignores_blackout_master():
    app = create_app(AppConfig(universes=[5]), patch=haze_patch())
    with TestClient(app) as client:
        haze = next(f for f in client.get("/api/fixtures").json() if f["id"] == "hazer-1")
        assert haze["fixture_type"] == "hazer"
        assert haze["attributes"] == ["pump", "fan"]
        assert haze["state"] == {"pump": 0.0, "fan": 0.0}
        assert client.get("/api/universes/5").json()["channels"][98:100] == [0, 0]
        assert client.get("/api/status").json()["blackout_active"] is True
        response = client.post(
            "/api/fixtures/hazer-1/state", json={"values": {"pump": 0.5, "fan": 0.25}},
        )
        assert response.status_code == 200
        client.post("/api/output/master", json={"level": 0.0})
        assert client.get("/api/universes/5").json()["channels"][98:100] == [128, 64]
        client.post("/api/output/blackout", json={"active": False})
        assert client.get("/api/universes/5").json()["channels"][98:100] == [128, 64]
        for values in ({"pump": -0.1}, {"fan": 1.01}, {"intensity": 1.0}, {"red": 1.0}):
            assert client.post("/api/fixtures/hazer-1/state", json={"values": values}).status_code == 422


def test_haze_preview_is_not_live_and_flash_restores_underlying_live_values():
    app = create_app(AppConfig(universes=[5]), patch=haze_patch())
    controller = app.state.fixture_controller
    with TestClient(app) as client:
        client.post("/api/fixtures/hazer-1/state", json={"values": {"pump": 0.2, "fan": 0.4}})
        client.post("/api/preview", json={"active": True})
        client.post("/api/fixtures/hazer-1/state", json={"values": {"pump": 0.8, "fan": 0.6}})
        assert client.get("/api/universes/5").json()["channels"][98:100] == [51, 102]
        controller.hold("haze-flash", {"hazer-1": {"pump": 1.0, "fan": 1.0}})
        assert client.get("/api/universes/5").json()["channels"][98:100] == [255, 255]
        assert controller.output_state("hazer-1") == {"pump": 1.0, "fan": 1.0}
        controller.release("haze-flash")
        assert client.get("/api/universes/5").json()["channels"][98:100] == [51, 102]
        client.post("/api/preview", json={"active": False})
        assert client.get("/api/universes/5").json()["channels"][98:100] == [204, 153]


def test_first_real_frame_has_blackout_heads_at_rest_and_haze_off():
    patch = haze_patch()
    rig = RigStore(
        patch, heads=HeadsFile(heads={"head-1": HeadSetup(rest=HeadRest(pan=0.25, tilt=0.5))}),
    )
    settings = AppConfig.validate_configuration({
        "output_mode": "ola", "physical_output_enabled": True, "universes": [5],
        "ola": {"allowed_universes": [5]},
    })

    class RecordingOutput:
        def __init__(self):
            self.frames = []

        def send_universe(self, universe, slots):
            self.frames.append((universe, tuple(slots)))

    output = RecordingOutput()
    app = create_app(settings, real_output=output, patch=patch, rig_store=rig)
    assert output.frames == []
    with TestClient(app) as client:
        assert client.get("/api/status").json()["physical_output_enabled"] is True
        universe, first = output.frames[0]
        assert universe == 5
        assert first[:2] == (64, 128)
        assert all(value == 0 for value in first[2:])


def test_status_exposes_active_setup_overrides_and_clears_on_disable():
    app = create_app(AppConfig(universes=[5]), patch=haze_patch())
    with TestClient(app) as client:
        assert client.get("/api/status").json()["setup_overrides"] == []
        assert client.put("/api/heads/head-1/setup-override", json={"enabled": True}).status_code == 200
        assert client.get("/api/status").json()["setup_overrides"] == ["head-1"]
        client.get("/api/fixtures")
        assert client.get("/api/status").json()["setup_overrides"] == ["head-1"]
        assert client.put("/api/heads/head-1/setup-override", json={"enabled": False}).status_code == 200
        assert client.get("/api/status").json()["setup_overrides"] == []


def test_renames_keep_ids_membership_and_live_state_in_memory(patch):
    with TestClient(create_app(AppConfig(universes=[5]), patch=patch)) as client:
        client.post("/api/fixtures/bar-1/state", json={"values": {"intensity": 0.4}})
        changed = client.put("/api/fixtures/bar-1/name", json={"name": "  Front bar  "})
        assert changed.status_code == 200
        assert changed.json()["id"] == "bar-1"
        assert changed.json()["name"] == "Front bar"
        assert changed.json()["state"]["intensity"] == 0.4
        group = client.put("/api/groups/bars/name", json={"name": "Dance floor"})
        assert group.json() == {"id": "bars", "name": "Dance floor", "fixtures": ["bar-1", "bar-2"]}
        assert group.json() in client.get("/api/groups").json()
        assert patch.get("bar-1").patch.name == "Front bar"
        assert patch.group_name("bars") == "Dance floor"
        assert patch.get("bar-1").patch.groups == ["bars", "all"]


@pytest.mark.parametrize("name", ["", " ", "a" * 81, "bad\nname", "bad\x00name"])
def test_invalid_display_names_are_rejected(patch, name):
    with TestClient(create_app(AppConfig(universes=[5]), patch=patch)) as client:
        assert client.put("/api/fixtures/bar-1/name", json={"name": name}).status_code == 422
        assert client.put("/api/groups/bars/name", json={"name": name}).status_code == 422
        assert patch.get("bar-1").patch.name == "bar-1"


def test_unknown_name_ids_return_404(patch):
    with TestClient(create_app(AppConfig(universes=[5]), patch=patch)) as client:
        assert client.put("/api/fixtures/missing/name", json={"name": "Name"}).status_code == 404
        assert client.put("/api/groups/missing/name", json={"name": "Name"}).status_code == 404


def test_display_names_are_persisted_and_loaded_without_mutating_patch_file(patch, names_path):
    with TestClient(create_app(AppConfig(universes=[5]), patch=patch, name_path=names_path)) as client:
        assert client.put("/api/fixtures/bar-1/name", json={"name": "Front"}).status_code == 200
        assert client.put("/api/groups/bars/name", json={"name": "Floor"}).status_code == 200
    stored = json.loads(names_path.read_text())
    assert stored["fixtures"]["bar-1"] == "Front"
    assert stored["groups"]["bars"] == "Floor"
    reloaded = rig_patch()
    with TestClient(create_app(AppConfig(universes=[5]), patch=reloaded, name_path=names_path)) as client:
        assert client.get("/api/fixtures").json()[0]["name"] == "Front"
        assert reloaded.group_name("bars") == "Floor"


def test_invalid_persisted_names_are_actionable_and_not_discarded(patch, names_path):
    names_path.write_text('{"fixtures": {"missing": "Lost"}}', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown fixtures"):
        create_app(AppConfig(universes=[5]), patch=patch, name_path=names_path)
    assert "missing" in names_path.read_text()


def test_failed_name_write_leaves_runtime_name_unchanged(patch, names_path, monkeypatch):
    def fail_write(*_):
        raise OSError("disk unavailable")

    monkeypatch.setattr("lichtwerk.scenes.write_json_atomic", fail_write)
    with TestClient(create_app(AppConfig(universes=[5]), patch=patch, name_path=names_path)) as client:
        response = client.put("/api/fixtures/bar-1/name", json={"name": "New"})
        assert response.status_code == 422
        assert "disk unavailable" in response.json()["detail"]
        assert patch.get("bar-1").patch.name == "bar-1"
