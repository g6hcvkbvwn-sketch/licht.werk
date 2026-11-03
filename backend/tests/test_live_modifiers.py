"""Global live modifiers use simulated output and never mutate saved lighting data."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lichtwerk.effects import EffectParams, EffectType, FxDefinition
from lichtwerk.live_modifiers import LiveModifiers, ModifierSettings
from lichtwerk.show_api import build_router
from lichtwerk.sequences import SequenceContent, SequenceStep
from lichtwerk.scenes import SceneContent
from test_looks import ShowRig, accent_pad, bar_output
from lichtwerk.midi_control import MidiEvent
from lichtwerk.effect_engine import EffectEngine
from lichtwerk.tempo import TempoClock
from test_fx_runtime_v2 import zone_rig, ZONE_B
from test_zones import HEAD, INCLUDE
from conftest import FakeClock


def modifiers(show):
    return LiveModifiers(show.controller, show.effects, {
        1: ("Bar", ["led-bar-1"]),
        2: ("Heads", ["moving-head-1", "moving-head-2"]),
    }, show.sequences)


@pytest.mark.parametrize("speed", [0.25, 0.5, 1.0, 2.0, 4.0])
@pytest.mark.parametrize("timing", ["beats", "seconds"])
def test_sequence_speed_scales_holds_only_and_reports_actual_countdown(speed, timing):
    show = ShowRig()
    show.effects.tempo.set_bpm(120)
    controls = modifiers(show)
    controls.set(ModifierSettings(sequence_speed=speed))
    first, second = show.look("First", 0.4), show.look("Second", 0.8)
    sequence = show.sequence_store.create(SequenceContent(
        name="Speed", timing=timing, steps=[
            SequenceStep(scene_id=first, fade=1.0, hold=4.0),
            SequenceStep(scene_id=second, fade=0.0, wait=True),
        ],
    ))
    original = show.sequence_store.get(sequence.id).model_dump()
    show.sequences.start(sequence.id)
    unit_seconds = 0.5 if timing == "beats" else 1.0
    status = show.sequences.status()
    assert status["until_next"] == pytest.approx(1 + 4 / speed)
    assert status["until_next_seconds"] == pytest.approx((1 + 4 / speed) * unit_seconds)
    show.advance(unit_seconds)
    assert show.sequences.status()["phase"] == "hold"
    assert show.level() == pytest.approx(0.4)
    show.advance(4 / speed * unit_seconds / 2)
    assert show.sequences.status()["until_next"] == pytest.approx(2 / speed)
    show.advance(4 / speed * unit_seconds / 2 + 0.001)
    assert show.sequences.status()["waiting"]
    show.advance(10)
    assert show.sequences.status()["step_index"] == 1
    assert show.sequence_store.get(sequence.id).model_dump() == original
    assert show.effects.tempo.bpm() == 120


def test_sequence_speed_change_preserves_live_and_preview_hold_progress_and_pause():
    show = ShowRig()
    controls = modifiers(show)
    first, second = show.look("First", 0.4), show.look("Second", 0.8)
    sequence = show.sequence(
        SequenceStep(scene_id=first, fade=0, hold=4),
        SequenceStep(scene_id=second, fade=0, wait=True),
    )
    show.sequences.start(sequence)
    show.advance(1)
    show.preview.start()
    show.sequences.pause()
    controls.set(ModifierSettings(sequence_speed=2))
    for live in (False, True):
        status = show.sequences.status(live=live)
        assert status["progress"] == pytest.approx(0.25)
        assert status["until_next"] == pytest.approx(1.5)
    show.advance(0.5)
    assert show.sequences.status()["until_next"] == pytest.approx(1.5)
    assert show.sequences.status(live=True)["until_next"] == pytest.approx(1)
    show.sequences.resume()
    show.advance(0.5)
    assert show.sequences.status()["until_next"] == pytest.approx(1)
    controls.set(ModifierSettings())
    assert show.sequences.status()["until_next"] == pytest.approx(2)
    assert show.sequences.status()["progress"] == pytest.approx(0.5)


def test_sequence_speed_change_during_fade_and_tick_crossing_phases():
    show = ShowRig()
    controls = modifiers(show)
    first, second = show.look("First", 0.4), show.look("Second", 0.8)
    sequence = show.sequence(
        SequenceStep(scene_id=first, fade=2, hold=4),
        SequenceStep(scene_id=second, fade=1, hold=4),
        SequenceStep(scene_id=first, fade=0, wait=True),
    )
    show.sequences.start(sequence)
    show.advance(1)
    assert show.level() == pytest.approx(0.2)
    controls.set(ModifierSettings(sequence_speed=4))
    assert show.sequences.status()["progress"] == pytest.approx(0.5)
    assert show.sequences.status()["until_next"] == pytest.approx(2)
    # One tick consumes the rest of the fade, the shortened hold, and half the next fade.
    show.advance(2.5)
    assert show.sequences.status()["step_index"] == 1
    assert show.sequences.status()["phase"] == "fade"
    assert show.level() == pytest.approx(0.6)
    show.advance(0.5)
    assert show.sequences.status()["until_next"] == pytest.approx(1)
    # An unchanged modifier must not rebase or restart the hold.
    controls.set(ModifierSettings(sequence_speed=4))
    show.advance(1)
    assert show.sequences.status()["waiting"]


@pytest.mark.parametrize("speed,hold,quantum", [
    (1, 4, 1), (2, 4, 1), (4, 4, 1), (4, 2, 0.5), (4, 0.5, 0.5), (0.25, 0.5, 1),
])
def test_sequence_countdown_precision_uses_effective_timings(speed, hold, quantum):
    show = ShowRig()
    modifiers(show).set(ModifierSettings(sequence_speed=speed))
    scene = show.look("Look", 0.5)
    sequence = show.sequence_store.create(SequenceContent(
        name="Beat countdown", timing="beats",
        steps=[SequenceStep(scene_id=scene, fade=1, hold=hold)],
    ))
    show.sequences.start(sequence.id)
    assert show.sequences.status()["countdown_step"] == quantum


@pytest.mark.parametrize("timing", ["beats", "seconds"])
@pytest.mark.parametrize("speed", [0.25, 0.5, 2.0, 4.0])
@pytest.mark.parametrize("layer", ["live", "preview", "overlay"])
def test_fx_speed_preserves_phase_and_scales_elapsed_time(timing, speed, layer):
    show, reference = ShowRig(), ShowRig()
    definition = FxDefinition(
        name="Colour", kind=EffectType.RAINBOW,
        params=EffectParams(beats=4.0, seconds=4.0, timing=timing, size=1.0, spread=0.0, softness=0.0),
    )
    for rig in (show, reference):
        if layer == "preview":
            rig.preview.start()
        rig.effects.start("colour", EffectType.RAINBOW, 0, ["led-bar-1"],
                          definition=definition, overlay=layer == "overlay")
        rig.clock.now += 0.3

    def render(rig):
        return rig.effects.render("led-bar-1", {"red": 1.0, "green": 0.0, "blue": 0.0},
                                  preview=layer == "preview", overlay=layer == "overlay").values

    before = render(show)
    original = show.effects.running("colour", EffectType.RAINBOW) if layer != "overlay" else None
    modifiers(show).set(ModifierSettings(speed=speed))
    assert render(show) == pytest.approx(before)
    show.clock.now += 0.4
    reference.clock.now += 0.4 * speed
    assert render(show) == pytest.approx(render(reference))
    if original is not None:
        assert show.effects.running("colour", EffectType.RAINBOW) == original
    assert show.effects.tempo.bpm() == reference.effects.tempo.bpm()
    before = render(show)
    show.effects.set_speed(1.0)
    assert render(show) == pytest.approx(before)


def test_tint_is_scoped_preserves_levels_and_leaves_capture_untouched():
    show = ShowRig()
    controls = modifiers(show)
    show.controller.apply({
        "led-bar-1": {"intensity": 0.3, "red": 0.5, "green": 0.2},
        "moving-head-1": {"intensity": 0.6, "red": 0.8, "white": 0.7,
                          "ring_1_red": 0.4, "ring_1_green": 0.1},
    }, 0)
    before = show.controller.snapshot()
    controls.set(ModifierSettings(tint="#0000ff", excluded_strips=[2]))
    bar = show.controller.output_state("led-bar-1")
    assert (bar["red"], bar["green"], bar["blue"], bar["intensity"]) == pytest.approx((0, 0, 0.5, 0.3))
    head = show.controller.output_state("moving-head-1")
    assert (head["red"], head["white"], head["ring_1_red"]) == pytest.approx((0.8, 0.7, 0.4))
    controls.set(ModifierSettings(tint="#00ff00", excluded_strips=[1]))
    head = show.controller.output_state("moving-head-1")
    assert (head["red"], head["green"], head["blue"], head["white"]) == pytest.approx((0, 0.8, 0, 0))
    assert (head["ring_1_red"], head["ring_1_green"], head["ring_1_blue"]) == pytest.approx((0, 0.4, 0))
    assert show.controller.snapshot() == before
    scene = show.scenes.capture("Untinted", 0.0)
    assert scene.fixtures["moving-head-1"]["red"] == 0.8
    controls.set(ModifierSettings(excluded_strips=[1]))
    assert show.controller.output_state("moving-head-1")["white"] == 0.7
    assert controls.state()["excluded_strips"] == [1]


def test_tint_defaults_to_all_lighting_including_fixtures_outside_strip_choices():
    show = ShowRig()
    controls = modifiers(show)
    fixtures = ("led-bar-1", "moving-head-1", "moving-head-2", "dj-spot-1", "dj-spot-2")
    for fixture in fixtures:
        show.controller.set_values(fixture, {"red": 0.6, "green": 0.2, "blue": 0.0})
    show.controller.set_values("moving-head-1", {"ring_1_red": 0.4})
    before = show.controller.snapshot()
    controls.set(ModifierSettings(tint="#0000ff"))
    assert controls.state()["excluded_strips"] == []
    for fixture in fixtures:
        output = show.controller.output_state(fixture)
        assert (output["red"], output["green"], output["blue"]) == pytest.approx((0, 0, 0.6))
    assert show.controller.output_state("moving-head-1")["ring_1_blue"] == 0.4
    assert show.controller.snapshot() == before


@pytest.mark.parametrize("excluded", [[5], [6], [5, 6]])
def test_booth_exclusions_follow_physical_fixtures_across_modes_and_look_steps(excluded):
    show = ShowRig()
    controls = LiveModifiers(show.controller, show.effects, {
        5: ("Booth spots", ["dj-spot-1", "dj-spot-2"]),
        6: ("Booth 1", ["dj-spot-1"]),
        7: ("Booth 2", ["dj-spot-2"]),
    }, show.sequences)
    first, second = [
        show.store.create(SceneContent(
            name=name, fixtures={
                fixture: {"intensity": 0.4, "red": red, "green": 0.0, "blue": 0.0}
                for fixture in ("led-bar-1", "dj-spot-1", "dj-spot-2")
            },
        )).id
        for name, red in (("First", 0.6), ("Second", 0.9))
    ]
    controls.set(ModifierSettings(tint="#0000ff", excluded_strips=excluded))
    show.scenes.recall(first)
    for mode in ("single", "group"):
        show.apc.set_family_mode("spots", mode)
        show.sequences.start(show.sequence(
            SequenceStep(scene_id=first, fade=0.0, wait=True),
            SequenceStep(scene_id=second, fade=0.0, wait=True),
        ))
        for red in (0.6, 0.9):
            output = show.controller.output_state("led-bar-1")
            assert (output["red"], output["blue"]) == pytest.approx((0, red))
            for fixture in ("dj-spot-1", "dj-spot-2"):
                output = show.controller.output_state(fixture)
                is_excluded = 5 in excluded or fixture == "dj-spot-1"
                assert (output["red"], output["blue"]) == pytest.approx(
                    (red, 0) if is_excluded else (0, red),
                )
            show.sequences.next()
        assert controls.state()["excluded_strips"] == excluded


@pytest.mark.parametrize("timing", ["beats", "seconds"])
@pytest.mark.parametrize("speed", [0.5, 2.0])
def test_move_tour_speed_preserves_travel_hold_position_and_blanking(timing, speed):
    patch, rig = zone_rig()
    clock, reference_clock = FakeClock(), FakeClock()
    effects = EffectEngine(patch, TempoClock(clock=clock), rig=rig)
    reference = EffectEngine(patch, TempoClock(clock=reference_clock), rig=rig)
    definition = FxDefinition(
        name="Tour", kind=EffectType.CIRCLE,
        params=EffectParams(beats=4.0, size=0.0, spread=0.0, softness=0.0,
                            timing=timing, zone_ids=[INCLUDE.id, ZONE_B.id],
                            pattern="none", travel=1.0, hold=2.0),
    )
    for engine in (effects, reference):
        engine.start("tour", EffectType.CIRCLE, 0, [HEAD], definition=definition)
    base = {"pan": 0.5, "tilt": 0.5, "intensity": 1.0}
    clock.now += 0.2
    reference_clock.now += 0.2
    before = effects.render(HEAD, base)
    effects.set_speed(speed)
    assert effects.render(HEAD, base).values == pytest.approx(before.values)
    for _ in range(10):
        clock.now += 0.4
        reference_clock.now += 0.4 * speed
        actual, expected = effects.render(HEAD, base), reference.render(HEAD, base)
        assert actual.values == pytest.approx(expected.values)
        assert actual.dark_movement == expected.dark_movement


def test_dimmer_and_ring_fx_share_the_modifier_clock():
    show, reference = ShowRig(), ShowRig()
    for rig in (show, reference):
        rig.effects.start("dimmer", EffectType.CHASE, 2, ["led-bar-1"])
        rig.effects.start("ring", EffectType.RAINBOW, 0, ["moving-head-1"], component="ring")
        rig.clock.now += 0.17
    show.effects.set_speed(2.0)
    for _ in range(5):
        show.clock.now += 0.2
        reference.clock.now += 0.4
        for fixture, state in (
            ("led-bar-1", {"intensity": 1.0}),
            ("moving-head-1", {"ring_1_red": 1.0}),
        ):
            assert show.effects.render(fixture, state).values == pytest.approx(
                reference.effects.render(fixture, state).values,
            )


def test_tint_off_reveals_current_fx_colour_not_a_frozen_snapshot():
    show = ShowRig()
    controls = modifiers(show)
    show.controller.set_values("led-bar-1", {"red": 1.0, "intensity": 0.5})
    show.start(1, EffectType.RAINBOW)
    initial = show.controller.output_state("led-bar-1")
    controls.set(ModifierSettings(tint="#0000ff"))
    show.advance(0.7)
    tinted = show.controller.output_state("led-bar-1")
    assert tinted["red"] == tinted["green"] == 0.0
    assert tinted["blue"] > 0.0
    controls.set(ModifierSettings())
    current = show.controller.output_state("led-bar-1")
    assert current != initial
    assert current["intensity"] == 0.5


def test_modifiers_remain_across_looks_steps_accents_and_preview_go():
    show = ShowRig()
    controls = modifiers(show)
    a, b = show.look("A", 0.2), show.look("B", 0.7)
    controls.set(ModifierSettings(speed=2.0, tint="#ff0000"))
    show.scenes.recall(a)
    show.sequences.start(show.sequence(
        SequenceStep(scene_id=a, fade=0.0, hold=2.0),
        SequenceStep(scene_id=b, fade=0.0, wait=True),
    ))
    show.advance(1.0)
    assert show.sequences.status()["step_index"] == 0
    assert show.sequences.status()["remaining"] == pytest.approx(1.0)  # not doubled
    show.advance(1.1)
    assert show.level() == pytest.approx(0.7)
    note = accent_pad(show, "beats", 2.0)
    show.press(note)
    beat_seconds = 60 / show.effects.tempo.bpm()
    show.advance(beat_seconds * 1.5)
    assert bar_output(show) == 1.0
    show.advance(beat_seconds * 0.6)
    assert bar_output(show) == pytest.approx(0.7)
    show.preview.start()
    controls.set(ModifierSettings(speed=0.5, tint="#0000ff"))
    show.preview.go()
    assert controls.state()["speed"] == 0.5
    assert controls.state()["tint"] == "#0000ff"


def test_tint_applies_live_in_preview_and_respects_blackout_master_and_darkness():
    show = ShowRig()
    controls = modifiers(show)
    show.controller.set_values("led-bar-1", {"intensity": 0.8, "red": 1.0})
    show.preview.start()
    show.controller.set_values("led-bar-1", {"red": 0.0, "green": 0.5})
    controls.set(ModifierSettings(tint="#0000ff"))
    assert show.controller.output_state("led-bar-1")["blue"] == 1.0
    assert show.controller.preview_output_state("led-bar-1")["blue"] == 0.5
    show.controller.set_master(0.5)
    assert show.service.read_output(5)[0] == 102  # 0.8 * 0.5 * 255
    show.service.set_blackout(True)
    assert show.service.read_output(5)[0] == 0
    show.service.set_blackout(False)
    assert show.service.read_output(5)[0] == 102
    show.apc.handle(MidiEvent("note", 1, 0x50, 127))  # existing master/blackout mapping
    assert show.service.blackout_active()


def test_modifier_http_validation_is_atomic_and_reset_keeps_exclusions():
    show = ShowRig()
    controls = modifiers(show)
    app = FastAPI()
    app.include_router(build_router(show.scenes, show.sequences, modifiers=controls))
    client = TestClient(app)
    initial = client.get("/api/show").json()["modifiers"]
    assert initial["speed"] == initial["sequence_speed"] == 1.0 and initial["tint"] is None
    assert initial["excluded_strips"] == []
    for values in (
        {"speed": True}, {"speed": 3.0}, {"speed": "2"},
        {"tint": "red"}, {"excluded_strips": [99]}, {"excluded_strips": [1, 1]},
        {"excluded_strips": [True]}, {"strips": [1]},
        {"sequence_speed": True}, {"sequence_speed": 3}, {"sequence_speed": "2"},
    ):
        response = client.put("/api/show/modifiers", json=values)
        assert response.status_code == 422, response.text
        assert client.get("/api/show").json()["modifiers"] == initial
    result = client.put("/api/show/modifiers", json={"speed": 4, "sequence_speed": 0.25, "tint": "#ff0000", "excluded_strips": [1]})
    assert result.status_code == 200
    assert result.json()["modifiers"]["sequence_speed"] == 0.25
    reset = client.put("/api/show/modifiers", json={"speed": 1, "tint": None, "excluded_strips": [1]}).json()
    assert reset["modifiers"]["excluded_strips"] == [1]
    assert reset["modifiers"]["tint"] is None
    assert controls.state()["speed"] == 1
    assert controls.state()["sequence_speed"] == 1
