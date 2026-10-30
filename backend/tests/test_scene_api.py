import json

import pytest
from fastapi.testclient import TestClient

from lichtwerk.api import create_app
from lichtwerk.config import AppConfig
from lichtwerk.scenes import SceneStore
from conftest import rig_patch


@pytest.fixture
def client(tmp_path):
    patch = rig_patch()
    store = SceneStore.load(tmp_path / "scenes.json", patch)
    with TestClient(create_app(AppConfig(universes=[5]), patch=patch, scene_store=store)) as c:
        c.post("/api/output/blackout", json={"active": False})
        yield c


def universe5(client: TestClient) -> list[int]:
    return client.get("/api/universes/5").json()["channels"]


RED_BARS = {"name": "Red bars", "groups": {"bars": {"intensity": 1.0, "red": 1.0}}}


def test_groups_are_listed_and_controllable(client) -> None:
    assert client.get("/api/groups").json() == [
        {"id": "all", "name": "All", "fixtures": ["bar-1", "bar-2", "wash-1"]},
        {"id": "bars", "name": "Bars", "fixtures": ["bar-1", "bar-2"]},
    ]
    response = client.post("/api/groups/bars/state", json={"values": {"intensity": 1, "red": 1}})

    assert response.json() == {"group": "bars", "changed_fixtures": ["bar-1", "bar-2"]}
    assert universe5(client)[:6] == [255, 0, 0, 255, 0, 0]


@pytest.mark.parametrize(
    ("group", "values", "status"),
    [
        ("missing", {"red": 1.0}, 404),
        ("bars", {"white": 1.0}, 422),
        ("bars", {"red": 2.0}, 422),
        ("bars", {}, 422),
    ],
)
def test_group_command_errors(client, group, values, status) -> None:
    assert client.post(f"/api/groups/{group}/state", json={"values": values}).status_code == status


def test_scene_crud_round_trip_persists_to_disk(client, tmp_path) -> None:
    created = client.post("/api/scenes", json=RED_BARS)
    assert created.status_code == 201
    assert created.json()["id"] == "red-bars"
    updated = client.put("/api/scenes/red-bars", json={**RED_BARS, "fade_seconds": 3})
    assert updated.json()["fade_seconds"] == 3.0
    copy = client.post("/api/scenes/red-bars/duplicate", json={"name": "Second"})
    assert copy.status_code == 201
    assert copy.json()["id"] == "second"

    on_disk = json.loads((tmp_path / "scenes.json").read_text())
    assert [s["id"] for s in on_disk["scenes"]] == ["red-bars", "second"]
    assert client.delete("/api/scenes/second").json() == {"deleted": "second"}
    assert [s["id"] for s in client.get("/api/scenes").json()] == ["red-bars"]


@pytest.mark.parametrize(
    ("method", "path", "body", "status"),
    [
        ("post", "/api/scenes", {"name": "x", "fixtures": {"ghost": {"red": 1}}}, 422),
        ("post", "/api/scenes", {"name": "x"}, 422),
        ("post", "/api/scenes", {"name": "x", "fixtures": {"bar-1": {"red": 9}}}, 422),
        ("put", "/api/scenes/missing", RED_BARS, 404),
        ("post", "/api/scenes/missing/duplicate", None, 404),
        ("delete", "/api/scenes/missing", None, 404),
        ("post", "/api/scenes/missing/recall", None, 404),
    ],
)
def test_scene_errors(client, method, path, body, status) -> None:
    kwargs = {"json": body} if body is not None else {}
    assert client.request(method.upper(), path, **kwargs).status_code == status


def test_recall_is_immediate_with_override_and_reported_in_status(client) -> None:
    client.post("/api/scenes", json={**RED_BARS, "fade_seconds": 30})
    response = client.post("/api/scenes/red-bars/recall", json={"fade_seconds": 0})

    assert response.status_code == 200
    assert universe5(client)[:6] == [255, 0, 0, 255, 0, 0]
    status = client.get("/api/status").json()
    assert status["active_scene"] == "red-bars"
    assert status["transition_active"] is False


def test_recall_with_stored_fade_reports_running_transition(client) -> None:
    client.post("/api/scenes", json={**RED_BARS, "fade_seconds": 30})
    client.post("/api/scenes/red-bars/recall")

    assert client.get("/api/status").json()["transition_active"] is True


def test_capture_stores_current_state(client) -> None:
    client.post("/api/fixtures/bar-1/state", json={"values": {"intensity": 1.0, "blue": 0.5}})
    response = client.post("/api/scenes/capture", json={"name": "Now", "fade_seconds": 1})

    assert response.status_code == 201
    assert response.json()["fixtures"]["bar-1"] == {
        "intensity": 1.0,
        "red": 0.0,
        "green": 0.0,
        "blue": 0.5,
    }


def test_master_scales_output_without_changing_state(client) -> None:
    client.post("/api/fixtures/bar-1/state", json={"values": {"intensity": 1.0, "red": 1.0}})
    assert client.post("/api/output/master", json={"level": 0.5}).json() == {"master": 0.5}

    assert universe5(client)[0] == 128
    assert client.get("/api/status").json()["master"] == 0.5
    bar = next(f for f in client.get("/api/fixtures").json() if f["id"] == "bar-1")
    assert bar["state"]["intensity"] == 1.0
    assert client.post("/api/output/master", json={"level": 1.5}).status_code == 422
