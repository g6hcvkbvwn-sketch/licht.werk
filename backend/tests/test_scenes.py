import json

import pytest

from lichtwerk import scenes as scenes_module
from lichtwerk.fixture_service import FixtureController
from lichtwerk.output import SimulatedDmxOutput
from lichtwerk.scene_service import SceneService
from lichtwerk.scenes import (
    PersistenceError,
    SceneContent,
    SceneStore,
    resolve_scene,
    scene_problems,
    slugify,
)
from lichtwerk.service import LightingService
from conftest import rig_patch


def content(**values) -> SceneContent:
    return SceneContent.model_validate({"name": "Red", **values})


def test_resolution_applies_groups_in_order_then_fixtures(patch) -> None:
    scene = content(
        groups={"all": {"intensity": 1.0, "red": 0.2}, "bars": {"red": 0.5}},
        fixtures={"bar-2": {"red": 0.9}},
    )

    assert resolve_scene(scene, patch) == {
        "bar-1": {"intensity": 1.0, "red": 0.5},
        "bar-2": {"intensity": 1.0, "red": 0.9},
        "wash-1": {"intensity": 1.0, "red": 0.2},
    }


def test_resolution_skips_disabled_fixtures() -> None:
    patch = rig_patch(wash_enabled=False)
    scene = content(groups={"all": {"red": 1.0}}, fixtures={"wash-1": {"blue": 1.0}})

    assert set(resolve_scene(scene, patch)) == {"bar-1", "bar-2"}


def test_scene_problems_reports_every_broken_reference(patch) -> None:
    scene = content(
        groups={"nope": {"red": 1.0}, "bars": {"white": 1.0}},
        fixtures={"ghost": {"red": 1.0}, "bar-1": {"white": 1.0}},
    )

    assert scene_problems(scene, patch) == [
        "unknown group 'nope'",
        "no fixture in group 'bars' has attribute 'white'",
        "unknown fixture 'ghost'",
        "fixture 'bar-1' has no attribute 'white'",
    ]
    assert scene_problems(content(), patch) == ["the scene contains no values"]


@pytest.mark.parametrize(
    "values",
    [
        {"fixtures": {"bar-1": {"red": 1.5}}},
        {"fixtures": {"bar-1": {"red": True}}},
        {"fade_seconds": -1},
        {"fade_seconds": 601},
        {"name": ""},
        {"unexpected": 1},
    ],
)
def test_scene_content_rejects_invalid_values(values) -> None:
    with pytest.raises(ValueError):
        content(**values)


def test_slugify_creates_safe_ids() -> None:
    assert slugify("Warm Wash – Bühne!") == "warm-wash-buhne"
    assert slugify("!!!") == "scene"


def test_create_assigns_unique_ids_and_rejects_bad_references(patch) -> None:
    store = SceneStore(patch)
    first = store.create(content(fixtures={"bar-1": {"red": 1.0}}))
    second = store.create(content(fixtures={"bar-1": {"red": 1.0}}))

    assert (first.id, second.id) == ("red", "red-2")
    with pytest.raises(ValueError, match="unknown fixture 'ghost'"):
        store.create(content(fixtures={"ghost": {"red": 1.0}}))


def test_update_duplicate_and_delete(patch) -> None:
    store = SceneStore(patch)
    scene = store.create(content(fixtures={"bar-1": {"red": 1.0}}))
    updated = store.update(scene.id, content(name="Blue", fixtures={"bar-1": {"blue": 1.0}}))
    copy = store.duplicate(scene.id)

    assert updated.id == "red"
    assert updated.fixtures == {"bar-1": {"blue": 1.0}}
    assert (copy.id, copy.name, copy.fixtures) == ("blue-copy", "Blue copy", updated.fixtures)
    store.delete(scene.id)
    assert [s.id for s in store.all()] == ["blue-copy"]
    with pytest.raises(KeyError):
        store.delete(scene.id)


def test_save_is_atomic_keeps_backup_and_reloads(patch, tmp_path) -> None:
    path = tmp_path / "scenes.json"
    store = SceneStore.load(path, patch)
    assert store.all() == []
    store.create(content(fixtures={"bar-1": {"red": 1.0}}, fade_seconds=2))
    store.create(content(name="Blue", groups={"bars": {"blue": 1.0}}))

    backup = json.loads((tmp_path / "scenes.json.bak").read_text())
    assert [s["id"] for s in backup["scenes"]] == ["red"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["scenes.json", "scenes.json.bak"]
    reloaded = SceneStore.load(path, patch)
    assert reloaded.all() == store.all()


def test_corrupt_file_names_the_file_and_the_backup(patch, tmp_path) -> None:
    path = tmp_path / "scenes.json"
    path.write_text("{not json")

    with pytest.raises(ValueError, match=r"scenes\.json.*scenes\.json\.bak"):
        SceneStore.load(path, patch)


def test_file_with_references_missing_from_patch_is_refused(patch, tmp_path) -> None:
    path = tmp_path / "scenes.json"
    path.write_text(
        json.dumps(
            {"scenes": [{"id": "old", "name": "Old", "fixtures": {"removed": {"red": 1.0}}}]}
        )
    )

    with pytest.raises(ValueError, match="scene 'old': unknown fixture 'removed'"):
        SceneStore.load(path, patch)


def test_failed_write_changes_nothing(patch, tmp_path, monkeypatch) -> None:
    store = SceneStore.load(tmp_path / "scenes.json", patch)
    store.create(content(fixtures={"bar-1": {"red": 1.0}}))

    def fail(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(scenes_module, "write_json_atomic", fail)
    with pytest.raises(PersistenceError, match="disk full"):
        store.create(content(name="Blue", fixtures={"bar-1": {"blue": 1.0}}))
    with pytest.raises(PersistenceError):
        store.delete("red")
    assert [s.id for s in store.all()] == ["red"]


def make_service(patch, clock):
    lighting = LightingService(SimulatedDmxOutput(), (5,))
    controller = FixtureController(patch, lighting, clock=clock)
    return SceneService(controller, SceneStore(patch)), controller, lighting


def test_recall_fades_and_does_not_modify_the_stored_scene(patch, clock) -> None:
    service, controller, lighting = make_service(patch, clock)
    scene = service.store.create(
        content(groups={"bars": {"intensity": 1.0, "red": 1.0}}, fade_seconds=2)
    )
    stored = scene.model_dump()
    service.recall(scene.id)
    controller.set_master(0.5)
    lighting.set_blackout(True)
    controller.set_values("bar-1", {"red": 0.3})
    clock.now += 2
    controller.tick()

    assert service.store.get(scene.id).model_dump() == stored
    assert service.active_scene() == scene.id
    assert controller.state("bar-1")["red"] == 0.3
    assert controller.state("bar-2") == {"intensity": 1.0, "red": 1.0, "green": 0.0, "blue": 0.0}


def test_recall_with_fade_override_and_unknown_scene(patch, clock) -> None:
    service, controller, _ = make_service(patch, clock)
    scene = service.store.create(content(fixtures={"bar-1": {"red": 1.0}}, fade_seconds=10))
    service.recall(scene.id, fade_seconds=0)

    assert controller.state("bar-1")["red"] == 1.0
    assert not controller.transition_active()
    with pytest.raises(KeyError):
        service.recall("missing")


def test_recall_is_repeatable(patch, clock) -> None:
    service, controller, lighting = make_service(patch, clock)
    scene = service.store.create(
        content(groups={"all": {"intensity": 0.8, "green": 1.0}}, fixtures={"bar-1": {"red": 1.0}})
    )
    service.recall(scene.id)
    first = lighting.read_output(5)
    service.release_control()
    controller.set_group_values("all", {"intensity": 0.0, "green": 0.0})
    service.recall(scene.id)

    assert lighting.read_output(5) == first


def test_capture_stores_live_values_and_delete_clears_active(patch, clock) -> None:
    service, controller, _ = make_service(patch, clock)
    controller.set_values("bar-1", {"intensity": 1.0, "red": 0.4})
    controller.set_master(0.2)
    captured = service.capture("Live", fade_seconds=1.5)

    assert captured.fixtures["bar-1"] == {"intensity": 1.0, "red": 0.4, "green": 0.0, "blue": 0.0}
    assert set(captured.fixtures) == {"bar-1", "bar-2", "wash-1"}
    assert captured.fade_seconds == 1.5
    service.recall(captured.id)
    service.delete(captured.id)
    assert service.active_scene() is None
