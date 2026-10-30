"""Deterministic show tests; all output is simulated and clocks are explicitly advanced."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lichtwerk.fixture_service import FixtureController
from lichtwerk.output import SimulatedDmxOutput
from lichtwerk.preview import PreviewService
from lichtwerk.scene_api import build_router as scene_router
from lichtwerk.scene_service import SceneService
from lichtwerk.scenes import SceneContent, SceneStore
from lichtwerk.sequence_service import SequenceService
from lichtwerk.sequences import SequenceContent, SequenceStore
from lichtwerk.service import LightingService
from lichtwerk.show_api import build_router
from lichtwerk.tempo import TempoClock


class Show:
    def __init__(self, patch, clock):
        self.clock = clock
        self.output = SimulatedDmxOutput()
        self.controller = FixtureController(patch, LightingService(self.output, (5,)), clock)
        self.scenes = SceneService(self.controller, SceneStore(patch))
        self.tempo = TempoClock(clock, 120)
        self.error = None
        self.store = SequenceStore(self.scenes.store)
        self.sequences = SequenceService(self.scenes, self.store, self.tempo, clock, lambda: self.error)
        self.preview = PreviewService(self.controller, self.scenes)

    def scene(self, name="Scene", fixture="bar-1", attr="red", value=1.0, fade=0.0):
        return self.scenes.store.create(SceneContent(
            name=name, fixtures={fixture: {attr: value}}, fade_seconds=fade,
        ))

    def sequence(self, steps, timing="seconds", loop=False, live_controls=None):
        return self.store.create(SequenceContent(
            name="Sequence", timing=timing, loop=loop, steps=steps,
            live_controls=live_controls or {},
        ))

    def advance(self, seconds):
        self.clock.now += seconds
        self.controller.tick()


@pytest.fixture
def show(patch, clock):
    return Show(patch, clock)


def test_sequence_incoming_fade_then_hold_then_final_hold(show):
    red = show.scene(fade=2)
    dark = show.scene("Dark", value=0)
    sequence = show.sequence([
        {"scene_id": red.id, "hold": 3.0},
        {"scene_id": dark.id, "fade": 1.0, "hold": 2.0},
    ])
    show.sequences.start(sequence.id)
    show.advance(1)
    assert show.controller.state("bar-1")["red"] == pytest.approx(.5)
    assert show.sequences.status()["phase"] == "fade"
    show.advance(2)
    assert show.controller.state("bar-1")["red"] == 1
    assert show.sequences.status()["remaining"] == 2
    show.advance(2.5)
    assert show.controller.state("bar-1")["red"] == pytest.approx(.5)
    show.advance(2.5)
    assert show.sequences.status()["status"] == "completed"
    assert show.controller.state("bar-1")["red"] == 0
    assert show.scenes.locks() == {}


def test_loop_handles_crossing_several_steps(show):
    bright = show.scene()
    dark = show.scene("Dark", value=0)
    sequence = show.sequence([
        {"scene_id": bright.id, "hold": 1.0},
        {"scene_id": dark.id, "hold": 1.0},
    ], loop=True)
    show.sequences.start(sequence.id)
    show.advance(4.5)
    assert show.sequences.status()["step_index"] == 0
    assert show.controller.state("bar-1")["red"] == 1
    assert show.sequences.status()["remaining"] == pytest.approx(.5)


def test_pause_resume_and_stop_freeze_own_fade_but_unrelated_fade_continues(show):
    scene = show.scene(fade=4)
    other = show.scene("Other", fixture="bar-2", attr="blue", fade=4)
    sequence = show.sequence([{"scene_id": scene.id, "hold": 2.0}])
    show.sequences.start(sequence.id)
    show.scenes.recall(other.id)
    show.advance(1)
    show.sequences.pause()
    show.advance(1)
    assert show.controller.state("bar-1")["red"] == pytest.approx(.25)
    assert show.controller.state("bar-2")["blue"] == pytest.approx(.5)
    show.sequences.resume()
    show.advance(1)
    assert show.controller.state("bar-1")["red"] == pytest.approx(.5)
    show.sequences.stop()
    show.advance(20)
    assert show.controller.state("bar-1")["red"] == pytest.approx(.5)
    assert show.controller.state("bar-2")["blue"] == 1
    assert "bar-1" not in show.scenes.locks()


def test_overlap_in_future_step_stops_sequence_but_unrelated_scene_coexists(show):
    first = show.scene(fade=4)
    future = show.scene("Future", fixture="bar-2", attr="blue")
    unrelated = show.scene("Other", fixture="wash-1", attr="green")
    sequence = show.sequence([
        {"scene_id": first.id, "hold": 3.0},
        {"scene_id": future.id, "hold": 2.0},
    ])
    show.sequences.start(sequence.id)
    show.scenes.recall(unrelated.id)
    assert show.sequences.status()["status"] == "running"
    show.advance(1)
    show.scenes.recall(future.id)
    assert show.sequences.status()["status"] == "stopped"
    show.advance(10)
    assert show.controller.state("bar-1")["red"] == pytest.approx(.25)


def test_manual_attrs_always_work_and_repeated_recall_never_off(show):
    scene = show.scene()
    show.scenes.recall(scene.id)
    show.controller.set_values("bar-1", {"red": .2})
    assert show.controller.state("bar-1")["red"] == .2
    show.controller.apply({"bar-1": {"red": .4}}, 0)
    assert show.controller.state("bar-1")["red"] == .4
    show.controller.set_values("bar-1", {"blue": .3})
    show.scenes.recall(scene.id)
    assert show.controller.state("bar-1")["red"] == 1
    assert show.controller.state("bar-1")["blue"] == .3


def test_manual_midfade_replaces_static_only_for_written_attribute(show):
    scene = show.scenes.store.create(SceneContent(
        name="Fade", fade_seconds=4, fixtures={"bar-1": {"red": 1.0, "blue": 1.0}},
    ))
    show.scenes.recall(scene.id)
    show.advance(1)
    show.controller.set_values("bar-1", {"red": .8})
    show.advance(1)
    assert show.controller.state("bar-1")["red"] == .8
    assert show.controller.state("bar-1")["blue"] == .5
    assert show.scenes.store.get(scene.id).fixtures["bar-1"]["red"] == 1


def test_manual_sequence_override_lasts_until_next_step(show):
    first = show.scene(fade=2)
    second = show.scene("Second", value=0, fade=2)
    sequence = show.sequence([
        {"scene_id": first.id, "hold": 1.0},
        {"scene_id": second.id, "hold": 1.0},
    ])
    show.sequences.start(sequence.id)
    show.advance(.5)
    show.controller.set_values("bar-1", {"red": .8})
    show.advance(2)
    assert show.controller.state("bar-1")["red"] == .8
    show.advance(1)
    assert show.sequences.status()["step_index"] == 1
    assert show.controller.state("bar-1")["red"] == pytest.approx(.6)


def test_sequence_live_controls_use_current_value_and_do_not_mask_direct_scenes(show):
    show.controller.set_values("bar-1", {"red": .6})
    scene = show.scenes.store.create(SceneContent(
        name="Mixed", fade_seconds=2, fixtures={"bar-1": {"red": 1.0, "blue": 1.0}},
    ))
    sequence = show.sequence(
        [{"scene_id": scene.id, "hold": 1.0}], loop=True, live_controls={"bar-1": ["red"]},
    )
    show.sequences.start(sequence.id)
    show.advance(1)
    assert show.controller.state("bar-1")["red"] == .6
    assert show.controller.state("bar-1")["blue"] == .5
    show.controller.set_values("bar-1", {"red": .3})
    show.advance(8)
    assert show.controller.state("bar-1")["red"] == .3
    direct = show.scene("Direct", value=.9)
    show.scenes.recall(direct.id)
    assert show.controller.state("bar-1")["red"] == .9
    assert show.sequences.status()["status"] == "running"
    show.sequences.stop()
    show.scenes.recall(scene.id, 0)
    assert show.controller.state("bar-1")["red"] == 1
    assert show.store.get(sequence.id).live_controls == {"bar-1": ["red"]}


def test_sequence_stop_preserves_static_fade_on_live_attribute(show):
    static = show.scene("Static", fade=4)
    show.scenes.recall(static.id)
    scene = show.scenes.store.create(SceneContent(
        name="Sequence scene", fixtures={"bar-1": {"red": 0.0, "blue": 1.0}},
    ))
    sequence = show.sequence(
        [{"scene_id": scene.id, "fade": 4.0, "hold": 1.0}],
        live_controls={"bar-1": ["red"]},
    )
    show.sequences.start(sequence.id)
    show.advance(1)
    show.sequences.stop()
    show.advance(1)
    assert show.controller.state("bar-1")["red"] == .5
    assert show.controller.state("bar-1")["blue"] == .25
    assert show.scenes._owners["bar-1"] == {"red": f"scene:{static.id}"}


def test_stop_does_not_cancel_newer_manual_fade(show):
    scene = show.scene(fade=4)
    sequence = show.sequence([{"scene_id": scene.id, "hold": 1.0}])
    show.sequences.start(sequence.id)
    show.advance(1)
    show.controller.apply({"bar-1": {"red": 1.0}}, 3)
    show.sequences.stop()
    show.advance(1)
    assert show.controller.state("bar-1")["red"] == .5


def test_stop_does_not_cancel_static_fade_for_not_yet_entered_step(show):
    static = show.scene("Static", fade=4)
    show.scenes.recall(static.id)
    first = show.scene("First", attr="blue")
    future = show.scene("Future", value=0)
    sequence = show.sequence([
        {"scene_id": first.id, "hold": 2.0},
        {"scene_id": future.id, "hold": 1.0},
    ])
    show.sequences.start(sequence.id)
    show.advance(1)
    assert show.controller.state("bar-1")["red"] == .25
    show.sequences.stop()
    show.advance(1)
    assert show.controller.state("bar-1")["red"] == .5


def test_later_step_does_not_reclaim_manual_fade_on_unselected_attribute(show):
    first = show.scene()
    second = show.scene("Blue", attr="blue")
    sequence = show.sequence([
        {"scene_id": first.id, "hold": 1.0},
        {"scene_id": second.id, "hold": 4.0},
    ])
    show.sequences.start(sequence.id)
    show.controller.apply({"bar-1": {"red": 0.0}}, 4)
    show.advance(1)
    assert show.sequences.status()["step_index"] == 1
    show.sequences.stop()
    show.advance(1)
    assert show.controller.state("bar-1")["red"] == .5


def test_sequence_live_controls_are_preview_isolated_and_go_promotes_snapshot(show):
    scene = show.scene(fade=2)
    live = show.sequence([{"scene_id": scene.id, "hold": 1.0}], loop=True)
    staged = show.sequence(
        [{"scene_id": scene.id, "hold": 1.0}], loop=True, live_controls={"bar-1": ["red"]},
    )
    show.sequences.start(live.id)
    show.advance(.5)
    show.preview.start()
    show.sequences.start(staged.id)
    show.controller.set_values("bar-1", {"red": .7})
    show.advance(.5)
    assert show.controller.live_state("bar-1")["red"] == .5
    assert show.controller.state("bar-1")["red"] == .7
    show.preview.go()
    show.advance(4)
    assert show.controller.live_state("bar-1")["red"] == .7
    assert show.sequences.status()["id"] == staged.id
    show.scenes.recall(scene.id, 0)
    assert show.controller.state("bar-1")["red"] == 1


def test_legacy_global_api_does_not_leak_between_preview_and_live(show):
    show.preview.start()
    show.scenes.set_globals({"bar-1": ["red"]})
    with show.controller.editing_context(False):
        assert show.scenes.globals() == {}
        scene = show.scene()
        show.scenes.recall(scene.id)
    assert show.scenes.globals() == {"bar-1": ["red"]}
    assert show.controller.live_state("bar-1")["red"] == 1


def test_standalone_beat_fades_follow_tempo_and_manual_override(show):
    scene = show.scenes.store.create(SceneContent(
        name="Beats", fade_timing="beats", fade_beats=4,
        fixtures={"bar-1": {"red": 1.0, "blue": 1.0}},
    ))
    show.scenes.recall(scene.id)
    show.advance(.5)
    assert show.controller.state("bar-1")["red"] == .25
    show.tempo.set_bpm(60)
    show.advance(1)
    assert show.controller.state("bar-1")["red"] == .5
    show.controller.set_values("bar-1", {"red": .8})
    show.advance(2)
    assert show.controller.state("bar-1")["red"] == .8
    assert show.controller.state("bar-1")["blue"] == 1


def test_standalone_beat_fade_preview_go_preserves_progress(show):
    scene = show.scenes.store.create(SceneContent(
        name="Beat preview", fade_timing="beats", fade_beats=4,
        fixtures={"bar-1": {"red": 1.0}},
    ))
    show.preview.start()
    show.scenes.recall(scene.id)
    show.advance(.5)
    assert show.controller.state("bar-1")["red"] == .25
    assert show.controller.live_state("bar-1")["red"] == 0
    show.preview.go()
    show.tempo.set_bpm(60)
    show.advance(1)
    assert show.controller.live_state("bar-1")["red"] == .5


@pytest.mark.parametrize("sequence_timing,scene_timing,duration", [
    ("seconds", "seconds", 3.0),
    ("seconds", "beats", 2.0),
    ("beats", "seconds", 8.0),
    ("beats", "beats", 4.0),
])
def test_sequence_converts_stored_scene_fade_units(show, sequence_timing, scene_timing, duration):
    scene = show.scenes.store.create(SceneContent(
        name="Units", fade_seconds=3, fade_timing=scene_timing, fade_beats=4,
        fixtures={"bar-1": {"red": 1.0}},
    ))
    sequence = show.sequence([{"scene_id": scene.id, "hold": 1.0}], timing=sequence_timing)
    show.sequences.start(sequence.id)
    assert show.sequences._live.fades == [duration]


@pytest.mark.parametrize("value", [-1, .25, 3, 64, float("inf"), float("nan")])
def test_scene_rejects_invalid_fade_beats(value):
    with pytest.raises(ValueError):
        SceneContent(name="Invalid", fade_beats=value, fixtures={"bar-1": {"red": 1.0}})


def test_legacy_scene_timing_and_sequence_live_controls_persistence(show, tmp_path):
    scene = show.scene(fade=2)
    assert scene.fade_timing == "seconds"
    assert scene.fade_beats == 1
    path = tmp_path / "sequences.json"
    store = SequenceStore(show.scenes.store, path)
    sequence = store.create(SequenceContent(
        name="Live", steps=[{"scene_id": scene.id}], live_controls={"bar-1": ["red"]},
    ))
    assert SequenceStore.load(path, show.scenes.store).get(sequence.id).live_controls == {"bar-1": ["red"]}
    for mask in ({"missing": ["red"]}, {"bar-1": ["missing"]}):
        with pytest.raises((KeyError, ValueError)):
            store.create(SequenceContent(name="Invalid", steps=[{"scene_id": scene.id}], live_controls=mask))


def test_scene_and_sequence_new_fields_http_validation(show):
    app = FastAPI()
    app.include_router(scene_router(show.controller, show.scenes))
    app.include_router(build_router(show.scenes, show.sequences))
    client = TestClient(app)
    content = {
        "name": "Beat scene", "fade_timing": "beats", "fade_beats": 4,
        "fixtures": {"bar-1": {"red": 1.0}},
    }
    response = client.post("/api/scenes", json=content)
    assert response.status_code == 201
    assert response.json()["fade_timing"] == "beats"
    scene_id = response.json()["id"]
    for fields in ({"fade_beats": 3}, {"fade_beats": -1}, {"fade_beats": "NaN"},
                   {"fade_timing": "frames"}, {"fade_seconds": 601},
                   {"fixtures": {"bar-1": {"red": 1.1}}}):
        assert client.post("/api/scenes", json={**content, **fields}).status_code == 422
    captured = client.post("/api/scenes/capture", json={
        "name": "Capture beats", "fixture_ids": ["bar-1"], "attributes": ["red"],
        "fade_timing": "beats", "fade_beats": .5,
    })
    assert captured.status_code == 201
    assert captured.json()["fade_beats"] == .5
    assert client.post("/api/scenes/capture", json={
        "name": "Bad capture", "fade_beats": 3,
    }).status_code == 422
    definition = {
        "name": "Live", "timing": "beats",
        "steps": [{"scene_id": scene_id, "hold": 1.0}],
        "live_controls": {"bar-1": ["red"]},
    }
    created = client.post("/api/sequences", json=definition)
    assert created.status_code == 201
    assert created.json()["live_controls"] == {"bar-1": ["red"]}
    for mask, status in (({"missing": ["red"]}, 404), ({"bar-1": ["missing"]}, 422)):
        assert client.post("/api/sequences", json={
            **definition, "live_controls": mask,
        }).status_code == status


def test_aimed_scene_zone_is_included_and_other_exclusions_stay(clock):
    from test_zones import HEAD

    show, rig, _ = positioned_show(clock)
    scene = show.scenes.store.create(SceneContent(
        name="Auto include", positions={HEAD: {
            "zone_id": "floor-area", "spot": {"u": .2, "v": .8},
            "excluded_zone_ids": ["floor-area", "booth"],
        }},
    ))
    saved = scene.model_dump()
    assert scene.positions[HEAD].included_zone_ids == ["floor-area"]
    assert scene.positions[HEAD].excluded_zone_ids == ["booth"]
    show.scenes.recall(scene.id)
    assert rig.assignment(HEAD).include_zones == ["floor-area"]
    assert rig.assignment(HEAD).exclude_zones == ["booth"]
    assert show.scenes.store.get(scene.id).model_dump() == saved


def test_sequence_live_positions_preserve_current_aim_permissions(clock):
    from test_zones import HEAD

    show, rig, positions = positioned_show(clock)
    positions.aim_target(HEAD, "clear", 0)
    original = rig.assignment(HEAD).model_copy(deep=True)
    scene = show.scenes.store.create(SceneContent(
        name="Zone", fixtures={HEAD: {"intensity": .5}},
        positions={HEAD: {"zone_id": "floor-area", "excluded_zone_ids": ["booth"]}},
    ))
    sequence = show.sequence(
        [{"scene_id": scene.id, "hold": 1.0}], loop=True, live_controls={HEAD: ["pan", "tilt"]},
    )
    show.sequences.start(sequence.id)
    show.advance(2)
    assert rig.assignment(HEAD) == original
    assert show.controller.state(HEAD)["intensity"] == .5


def test_globals_mask_scene_and_sequence_writes_and_allow_manual(show):
    first = show.scene(fade=2)
    second = show.scene("Second", value=0)
    sequence = show.sequence([
        {"scene_id": first.id, "hold": 1.0},
        {"scene_id": second.id, "hold": 1.0},
    ], loop=True)
    show.sequences.start(sequence.id)
    show.advance(.5)
    show.scenes.set_globals({"bar-1": ["red"]})
    show.controller.set_values("bar-1", {"red": .7})
    show.advance(10)
    show.scenes.recall(second.id)
    assert show.sequences.status()["status"] == "running"
    assert show.controller.state("bar-1")["red"] == .7
    assert show.scenes.locks() == {}


def test_release_freezes_current_direct_fade_and_unlocks_without_rollback(show):
    scene = show.scene(fade=4)
    show.scenes.recall(scene.id)
    show.advance(1)
    show.scenes.release_control(["bar-1"], ["red"])
    show.advance(5)
    assert show.controller.state("bar-1")["red"] == .25
    show.controller.set_values("bar-1", {"red": .4})
    assert show.scenes.store.get(scene.id).fixtures == {"bar-1": {"red": 1}}


def test_release_excludes_every_later_sequence_step_not_just_current_fade(show):
    first = show.scene(fade=2)
    next_scene = show.scene("Next", value=0)
    sequence = show.sequence([
        {"scene_id": first.id, "hold": 1.0},
        {"scene_id": next_scene.id, "hold": 1.0},
    ], loop=True)
    show.sequences.start(sequence.id)
    show.advance(.5)
    show.scenes.release_control(["bar-1"], ["red"])
    show.controller.set_values("bar-1", {"red": .6})
    show.advance(12)
    assert show.controller.state("bar-1")["red"] == .6


def test_immutable_scene_and_sequence_snapshot_then_apply_now(show):
    scene = show.scene(fade=2)
    sequence = show.sequence([{"scene_id": scene.id, "hold": 1.0}], loop=True)
    show.sequences.start(sequence.id)
    show.scenes.store.update(scene.id, SceneContent(name="Edited", fixtures={"bar-1": {"red": .2}}))
    show.store.update(sequence.id, SequenceContent(name="Edited sequence", steps=[{"scene_id": scene.id, "hold": 2.0}]))
    show.advance(2)
    assert show.controller.state("bar-1")["red"] == 1
    assert show.sequences.status()["name"] == "Sequence"
    show.sequences.apply_now()
    assert show.controller.state("bar-1")["red"] == .2
    assert show.sequences.status()["name"] == "Edited sequence"


def test_one_sequence_global_start_replaces_previous_and_releases_old_lock(show):
    first = show.scene(fade=4)
    second = show.scene("Second", fixture="bar-2")
    a = show.sequence([{"scene_id": first.id, "hold": 1.0}])
    b = show.sequence([{"scene_id": second.id, "hold": 1.0}])
    show.sequences.start(a.id)
    show.advance(1)
    show.sequences.start(b.id)
    show.advance(3)
    assert show.sequences.status()["id"] == b.id
    assert show.controller.state("bar-1")["red"] == .25
    assert show.scenes.locks() == {}


def test_beats_inherit_convert_round_up_clamp_and_zero_cut(show):
    a = show.scene("A", fade=.6)  # 1.2 beats => 2 beats at launch.
    b = show.scene("B", fixture="bar-2", fade=100)  # Clamp to 32.
    c = show.scene("C", fixture="wash-1", fade=0)
    sequence = show.sequence([
        {"scene_id": a.id, "hold": .5},
        {"scene_id": b.id, "hold": 1.0},
        {"scene_id": c.id, "hold": 1.0},
    ], timing="beats")
    show.sequences.start(sequence.id)
    assert show.sequences._live.fades == [2, 32, 0]
    show.advance(.5)
    assert show.controller.state("bar-1")["red"] == .5
    show.tempo.set_bpm(60)
    show.advance(.5)
    assert show.controller.state("bar-1")["red"] == .75
    assert show.sequences.status()["remaining_seconds"] == .5
    show.advance(.5)
    assert show.sequences.status()["phase"] == "hold"


@pytest.mark.parametrize("fade,hold", [(.25, 1), (3, 1), (1, 0), (64, 1), (1, 3)])
def test_beat_timing_rejects_nonmusical_values(fade, hold):
    with pytest.raises(ValueError):
        SequenceContent(name="Bad", timing="beats", steps=[{"scene_id": "scene", "fade": fade, "hold": hold}])


def test_output_failure_pauses_and_requires_explicit_resume(show):
    scene = show.scene(fade=4)
    sequence = show.sequence([{"scene_id": scene.id, "hold": 2.0}])
    show.sequences.start(sequence.id)
    show.advance(1)
    show.error = "Output disconnected"
    show.advance(1)
    assert show.sequences.status()["status"] == "paused"
    assert show.sequences.status()["error"] == "Output disconnected"
    with pytest.raises(ValueError, match="still unavailable"):
        show.sequences.resume()
    show.error = None
    show.advance(10)
    assert show.controller.state("bar-1")["red"] == .25
    show.sequences.resume()
    show.advance(1)
    assert show.controller.state("bar-1")["red"] == .5


def test_preview_scene_animates_independently_and_go_preserves_fade(show):
    scene = show.scene(fade=4)
    show.preview.start()
    show.scenes.recall(scene.id)
    show.advance(1)
    assert show.controller.state("bar-1")["red"] == .25
    assert show.controller.live_state("bar-1")["red"] == 0
    show.preview.go()
    assert show.controller.live_state("bar-1")["red"] == .25
    show.advance(1)
    assert show.controller.live_state("bar-1")["red"] == .5


def test_preview_and_live_sequences_run_independently_go_promotes_current_phase(show):
    first = show.scene(fade=4)
    second = show.scene("Second", fixture="bar-2", fade=4)
    a = show.sequence([{"scene_id": first.id, "hold": 2.0}])
    b = show.sequence([{"scene_id": second.id, "hold": 2.0}])
    show.sequences.start(a.id)
    show.advance(1)
    show.preview.start()
    show.sequences.start(b.id)
    show.advance(1)
    assert show.controller.live_state("bar-1")["red"] == .5
    assert show.controller.live_state("bar-2")["red"] == 0
    assert show.controller.state("bar-2")["red"] == .25
    show.preview.go()
    assert show.sequences.status()["id"] == b.id
    assert show.controller.live_state("bar-2")["red"] == .25
    show.advance(1)
    assert show.controller.live_state("bar-2")["red"] == .5


def test_scene_capture_selects_fixture_group_and_attributes_without_changing_original(show):
    show.controller.set_values("bar-1", {"red": .7, "blue": .3})
    scene = show.scenes.capture("Partial", 0, group_ids=["bars"], attributes=["red"])
    assert scene.fixtures == {"bar-1": {"red": .7}, "bar-2": {"red": 0}}
    with pytest.raises(KeyError):
        show.scenes.capture("Bad", 0, fixture_ids=["ghost"])
    with pytest.raises(ValueError, match="no values"):
        show.scenes.capture("Empty", 0, fixture_ids=[])


def test_sequence_reference_guard_and_atomic_reload(show, tmp_path):
    scene = show.scene()
    path = tmp_path / "sequences.json"
    store = SequenceStore.load(path, show.scenes.store)
    show.sequences.store = store
    sequence = store.create(SequenceContent(name="Saved", steps=[{"scene_id": scene.id}]))
    assert SequenceStore.load(path, show.scenes.store).get(sequence.id) == sequence
    with pytest.raises(ValueError, match="referenced"):
        show.scenes.delete(scene.id)
    invalid = json.loads(path.read_text())
    invalid["sequences"][0]["steps"][0]["scene_id"] = "missing"
    path.write_text(json.dumps(invalid))
    with pytest.raises(ValueError, match="missing"):
        SequenceStore.load(path, show.scenes.store)
    store.delete(sequence.id)
    show.scenes.delete(scene.id)


def test_http_crud_capture_lock_globals_playback_and_errors(show):
    app = FastAPI()
    app.include_router(scene_router(show.controller, show.scenes))
    app.include_router(build_router(show.scenes, show.sequences))
    client = TestClient(app)
    scene = client.post("/api/scenes/capture", json={
        "name": "Captured", "fixture_ids": ["bar-1"], "attributes": ["red"],
    }).json()
    sequence_response = client.post("/api/sequences", json={
        "name": "Sequence", "steps": [{"scene_id": scene["id"], "hold": 1.0}],
    })
    assert sequence_response.status_code == 201
    sequence_id = sequence_response.json()["id"]
    assert client.post(f"/api/sequences/{sequence_id}/start").json()["status"] == "running"
    assert client.get("/api/show").json()["locks"] == {}
    assert client.delete(f"/api/scenes/{scene['id']}").status_code == 409
    assert client.post(f"/api/sequences/{sequence_id}/pause").json()["status"] == "paused"
    assert client.post(f"/api/sequences/{sequence_id}/resume").json()["status"] == "running"
    assert client.post(f"/api/sequences/{sequence_id}/restart").json()["step_index"] == 0
    assert client.post(f"/api/sequences/{sequence_id}/apply").status_code == 200
    assert client.put("/api/show/globals", json={"fixtures": {"bar-1": ["red"]}}).status_code == 200
    assert client.get("/api/show").json()["locks"] == {}
    assert client.put("/api/show/globals", json={"fixtures": {"ghost": ["red"]}}).status_code == 404
    assert client.put("/api/show/globals", json={"fixtures": {"bar-1": ["fake"]}}).status_code == 422
    assert client.post("/api/sequences/missing/start").status_code == 404
    assert client.post(f"/api/sequences/{sequence_id}/fake").status_code == 404
    assert client.post(f"/api/sequences/{sequence_id}/start", json={"preview": True}).status_code == 409
    assert client.post("/api/show/release").status_code == 200
    assert client.post(f"/api/sequences/{sequence_id}/stop").json()["status"] == "stopped"
    assert client.delete(f"/api/sequences/{sequence_id}").status_code == 200
    assert client.delete(f"/api/scenes/{scene['id']}").status_code == 200


def test_pad_launch_toggles_same_sequence_and_restart_starts_step_one(show):
    a = show.scene()
    b = show.scene("B", value=0)
    sequence = show.sequence([{"scene_id": a.id, "hold": 1.0}, {"scene_id": b.id, "hold": 1.0}])
    assert show.sequences.launch_pad(sequence.id)["status"] == "running"
    show.advance(1.2)
    assert show.sequences.status()["step_index"] == 1
    assert show.sequences.launch_pad(sequence.id)["status"] == "paused"
    assert show.sequences.toggle()["status"] == "running"
    assert show.sequences.restart()["step_index"] == 0


def test_capture_haze_is_explicit_opt_in_and_sequence_owns_pump_fan(clock):
    from test_hazer_names import haze_patch

    show = Show(haze_patch(), clock)
    show.controller.set_values("hazer-1", {"pump": .4, "fan": .8})
    default = show.scenes.capture("No haze", 0)
    assert "hazer-1" not in default.fixtures
    scene = show.scenes.capture("Haze", 0, fixture_ids=["hazer-1"], include_haze=True)
    assert scene.fixtures == {"hazer-1": {"pump": .4, "fan": .8}}
    show.controller.set_values("hazer-1", {"pump": 0.0, "fan": 0.0})
    sequence = show.sequence([{"scene_id": scene.id, "fade": 2.0, "hold": 1.0}])
    show.sequences.start(sequence.id)
    show.advance(1)
    assert show.controller.state("hazer-1") == {"pump": .2, "fan": .4}
    show.controller.set_values("hazer-1", {"pump": 1.0})
    show.sequences.stop()
    show.advance(5)
    assert show.controller.state("hazer-1") == {"pump": 1.0, "fan": .4}


def positioned_show(clock):
    from test_zones import make_patch, make_rig
    from lichtwerk.position_service import PositionService

    patch = make_patch()
    rig = make_rig(patch)
    show = Show(patch, clock)
    # Use the same rig for the output guard and position resolver.
    show.controller = FixtureController(patch, LightingService(show.output, (5,)), clock, rig)
    show.scenes = SceneService(show.controller, SceneStore(patch))
    positions = PositionService(show.controller, rig)
    show.scenes.set_positions(positions)
    rig.set_definition_validator(show.scenes.validate_room)
    show.store = SequenceStore(show.scenes.store)
    show.sequences = SequenceService(show.scenes, show.store, show.tempo, clock)
    show.preview = PreviewService(show.controller, show.scenes, positions=positions)
    return show, rig, positions


def test_position_scene_recall_capture_references_and_runtime_safety(clock):
    from test_zones import HEAD, HEAD2
    from lichtwerk.positioning import NotConfiguredError

    show, rig, positions = positioned_show(clock)
    scene = show.scenes.store.create(SceneContent(
        name="Zone", fixtures={HEAD: {"intensity": .5}},
        positions={HEAD: {
            "zone_id": "floor-area", "spot": {"u": .2, "v": .8},
            "included_zone_ids": ["floor-area"], "excluded_zone_ids": ["booth"],
        }},
    ))
    show.scenes.recall(scene.id)
    assert rig.assignment(HEAD).include_zones == ["floor-area"]
    assert rig.assignment(HEAD).exclude_zones == ["booth"]
    captured = show.scenes.capture("Captured position", 0, fixture_ids=[HEAD], include_position=True)
    assert captured.positions[HEAD].spot.u == .2
    assert "pan" not in captured.fixtures[HEAD]
    with pytest.raises(ValueError, match="referenced"):
        rig.set_room_file(rig.room_file().model_copy(update={
            "zones": [zone for zone in rig.room_file().zones if zone.id != "booth"],
            "assignments": {},
            "strip_assignments": {},
        }))
    positions.aim_target(HEAD, "clear", 0)
    # Uncalibrated positions can be authored, but cannot change values/locks at recall.
    invalid_runtime = show.scenes.store.create(SceneContent(
        name="Uncalibrated", fixtures={HEAD2: {"intensity": 1.0}},
        positions={HEAD2: {"target_id": "clear"}},
    ))
    before = show.controller.state(HEAD2)
    locks = show.scenes.locks()
    with pytest.raises(NotConfiguredError):
        show.scenes.recall(invalid_runtime.id)
    assert show.controller.state(HEAD2) == before
    assert show.scenes.locks() == locks


def test_positioned_sequence_preview_and_live_keep_permissions_independent(clock):
    from test_zones import HEAD

    show, rig, _ = positioned_show(clock)
    target = show.scenes.store.create(SceneContent(name="Target", positions={HEAD: {"target_id": "clear"}}))
    zone = show.scenes.store.create(SceneContent(name="Zone", positions={HEAD: {
        "zone_id": "floor-area", "spot": {"u": .25, "v": .75},
        "included_zone_ids": ["floor-area"], "excluded_zone_ids": ["booth"],
    }}))
    live = show.sequence([{"scene_id": target.id, "fade": 4.0, "hold": 2.0}])
    staged = show.sequence([{"scene_id": zone.id, "fade": 4.0, "hold": 2.0}])
    show.sequences.start(live.id)
    show.preview.start()
    show.sequences.start(staged.id)
    show.advance(1)
    assert rig.assignment(HEAD, live=True).aim_target == "clear"
    assert rig.assignment(HEAD).aim_zone == "floor-area"
    assert rig.assignment(HEAD).exclude_zones == ["booth"]
    show.preview.go()
    assert show.sequences.status()["id"] == staged.id
    assert rig.assignment(HEAD, live=True).aim_zone == "floor-area"
    assert show.sequences.status()["progress"] == .25


def test_failed_position_recall_returns_actionable_http_error_without_partial_values(clock):
    from test_zones import HEAD2

    show, _, _ = positioned_show(clock)
    app = FastAPI()
    app.include_router(scene_router(show.controller, show.scenes))
    app.include_router(build_router(show.scenes, show.sequences))
    client = TestClient(app)
    response = client.post("/api/scenes", json={
        "name": "Uncalibrated", "fixtures": {HEAD2: {"intensity": 1.0}},
        "positions": {HEAD2: {"target_id": "clear"}},
    })
    assert response.status_code == 201
    scene_id = response.json()["id"]
    before = show.controller.state(HEAD2)
    assert client.post(f"/api/scenes/{scene_id}/recall").status_code == 409
    assert show.controller.state(HEAD2) == before
    seq = client.post("/api/sequences", json={
        "name": "Invalid runtime", "steps": [{"scene_id": scene_id}],
    }).json()
    assert client.post(f"/api/sequences/{seq['id']}/start").status_code == 409
    assert show.sequences.status()["status"] == "paused"
    assert show.sequences.status()["error"]


def test_invalid_position_does_not_stop_existing_overlapping_sequence(clock):
    from test_zones import HEAD2
    from lichtwerk.positioning import NotConfiguredError

    show, _, _ = positioned_show(clock)
    valid = show.scene("Numerical", fixture=HEAD2, attr="intensity", fade=4)
    invalid = show.scenes.store.create(SceneContent(
        name="Invalid", fixtures={HEAD2: {"intensity": .5}},
        positions={HEAD2: {"target_id": "clear"}},
    ))
    running = show.sequence([{"scene_id": valid.id, "hold": 2.0}])
    failed = show.sequence([{"scene_id": invalid.id, "hold": 2.0}])
    show.sequences.start(running.id)
    show.advance(1)
    for action in (lambda: show.scenes.recall(invalid.id), lambda: show.sequences.start(failed.id)):
        with pytest.raises(NotConfiguredError):
            action()
        assert show.sequences.status()["id"] == running.id
        assert show.sequences.status()["status"] == "running"
        assert show.controller.state(HEAD2)["intensity"] == .25
    show.advance(1)
    assert show.controller.state(HEAD2)["intensity"] == .5


def test_capture_does_not_consume_pending_final_output_frame(show):
    show.controller.set_values("bar-1", {"intensity": 1.0})
    scene = show.scene(fade=1)
    show.scenes.recall(scene.id)
    show.clock.now += 2
    assert show.scenes.capture("Current", 0).fixtures["bar-1"]["red"] == 1
    show.controller.tick()
    assert show.output.read_universe(5)[0] == 255


def test_scene_store_returns_defensive_copies(show):
    scene = show.scene()
    scene.fixtures["bar-1"]["red"] = .1
    assert show.scenes.store.get(scene.id).fixtures["bar-1"]["red"] == 1
    fetched = show.scenes.store.get(scene.id)
    fetched.fixtures["bar-1"]["red"] = .2
    assert show.scenes.store.get(scene.id).fixtures["bar-1"]["red"] == 1


@pytest.mark.parametrize("paused", [False, True])
def test_sequence_freezes_future_named_destination_geometry_but_not_safety_permissions(clock, paused):
    from test_zones import HEAD
    from lichtwerk.room import HeadAssignment

    show, rig, _ = positioned_show(clock)
    target = show.scenes.store.create(SceneContent(name="Target", positions={HEAD: {"target_id": "clear"}}))
    zone = show.scenes.store.create(SceneContent(name="Zone", positions={HEAD: {
        "zone_id": "floor-area", "included_zone_ids": ["floor-area"],
    }}))
    sequence = show.sequence([
        {"scene_id": target.id, "hold": 2.0},
        {"scene_id": zone.id, "hold": 2.0},
    ], loop=True)
    show.sequences.start(sequence.id)
    if paused:
        show.sequences.pause()
    before = rig.room_file()
    edits = [
        before.model_copy(update={"targets": [
            item.model_copy(update={"x": 2.5}) if item.id == "clear" else item
            for item in before.targets
        ]}),
        before.model_copy(update={"zones": [
            item.model_copy(update={"length": 1.5}) if item.id == "floor-area" else item
            for item in before.zones
        ]}),
        before.model_copy(update={"room": before.room.model_copy(update={"size_x": 7.0})}),
    ]
    for edit in edits:
        with pytest.raises(ValueError, match="Stop sequence"):
            rig.set_room_file(edit)
        assert rig.room_file() == before
    # Safety masks are runtime inputs, not frozen destination definitions.
    rig.set_assignments({HEAD: HeadAssignment(include_zones=["floor-area"], exclude_zones=["booth"])})
    assert rig.assignment(HEAD).exclude_zones == ["booth"]
    # Unrelated target geometry and display names do not affect the launched destinations.
    rig.set_room_file(rig.room_file().model_copy(update={"targets": [
        item.model_copy(update={"name": "Renamed"}) if item.id == "clear"
        else item.model_copy(update={"x": 3.75})
        for item in before.targets
    ]}))
    show.sequences.stop()
    rig.set_room_file(rig.room_file().model_copy(update={"targets": edits[0].targets}))
    assert rig.target("clear").x == 2.5
