"""Exercise command-line startup with temporary configuration and simulated output."""

import json

from fastapi.testclient import TestClient

from lichtwerk import cli


def test_cli_persists_fixture_and_group_names_across_restarts(tmp_path, monkeypatch):
    config_path = tmp_path / "app.json"
    config_path.write_text('{"universes": [5]}', encoding="utf-8")
    patch_path = tmp_path / "patch.json"
    patch_data = {
        "fixtures": [{
            "id": "led-bar-1", "name": "Original bar",
            "definition": "cameo-thunder-wash-100-rgb", "mode": "3Ch3",
            "universe": 5, "start_address": 1, "groups": ["bars"],
        }],
    }
    patch_path.write_text(json.dumps(patch_data), encoding="utf-8")
    arguments = ["lichtwerk", "serve", "--config", str(config_path), "--patch", str(patch_path)]
    for option in ("scenes", "room", "heads", "midi", "apc40", "map"):
        arguments.extend([f"--{option}", str(tmp_path / f"{option}.json")])
    monkeypatch.setattr("sys.argv", arguments)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    launches = []

    def run(app, **_):
        with TestClient(app) as client:
            assert client.get("/api/status").json()["output_mode"] == "simulated"
            if not launches:
                assert client.put(
                    "/api/fixtures/led-bar-1/name", json={"name": "Front bar"},
                ).status_code == 200
                assert client.put(
                    "/api/groups/bars/name", json={"name": "Dance floor"},
                ).status_code == 200
                definition = client.get("/api/fx/1/chase/slots").json()["slots"][0]
                definition["name"] = "Persistent triangle"
                definition["params"]["shape"] = "triangle"
                assert client.put("/api/fx/1/chase/slots/0", json=definition).status_code == 200
            else:
                assert client.get("/api/fixtures").json()[0]["name"] == "Front bar"
                assert client.get("/api/groups").json() == [{
                    "id": "bars", "name": "Dance floor", "fixtures": ["led-bar-1"],
                }]
                slots = client.get("/api/fx/1/chase/slots").json()
                assert slots["slots"][0]["name"] == "Persistent triangle"
                assert slots["running"] is None
            launches.append(app)

    monkeypatch.setattr(cli.uvicorn, "run", run)
    cli.main()
    assert json.loads((tmp_path / "fixture-names.json").read_text()) == {
        "fixtures": {"led-bar-1": "Front bar"}, "groups": {"bars": "Dance floor"},
    }
    cli.main()
    assert len(launches) == 2
    assert json.loads(patch_path.read_text()) == patch_data
