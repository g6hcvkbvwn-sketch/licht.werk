"""Segmented moving-head rings, legacy compatibility, and render-time ring FX."""

import json

import pytest

from lichtwerk.effect_engine import EffectEngine
from lichtwerk.effects import EffectParams, EffectType, FxDefinition, fits
from lichtwerk.fixture_service import FixtureController
from lichtwerk.fixtures import load_fixture_library, render_mode
from lichtwerk.fx_presets import FxPresetStore
from lichtwerk.output import SimulatedDmxOutput
from lichtwerk.patch import build_patch
from lichtwerk.preview import PreviewService
from lichtwerk.scene_service import SceneService
from lichtwerk.scenes import SceneContent, SceneStore
from lichtwerk.service import LightingService
from lichtwerk.sequences import SequenceContent, SequenceStep, SequenceStore
from lichtwerk.sequence_service import SequenceService
from lichtwerk.tempo import TempoClock
from lichtwerk.rings import (
    ring_segments,
    segment_attribute,
    segment_colour_attributes,
    segment_participant,
)

from conftest import FakeClock
from test_apc40 import club_patch
from test_moving_head import make_patch as make_16ch_patch
from test_zones import ALL_FLOOR, HEAD, HeadAssignment, make_rig

RING_BEAM = EffectType.CHASE
RING_COLOUR = EffectType.RAINBOW


def make_controller(patch=None, *, clock=None, rig=None):
    patch = patch or club_patch()
    clock = clock or FakeClock()
    lighting = LightingService(SimulatedDmxOutput(), (5,))
    controller = FixtureController(patch, lighting, clock=clock, rig=rig)
    tempo = TempoClock(clock=clock, bpm=120.0)
    effects = EffectEngine(patch, tempo, rig=rig)
    controller.set_effects(effects)
    return controller, lighting, effects, clock


def ring_values(red=0.0, green=0.0, blue=0.0):
    return {
        f"ring_{index}_{colour}": value
        for index in range(1, 7)
        for colour, value in (("red", red), ("green", green), ("blue", blue))
    }


def test_37ch_segments_map_to_channels_and_legacy_writes_fan_out_with_overrides():
    patch = club_patch()
    head = patch.get("moving-head-1")
    assert ring_segments(head) == [1, 2, 3, 4, 5, 6]
    assert segment_attribute(1, "red") == "ring_1_red"
    assert segment_colour_attributes(head, 6) == (
        "ring_6_red", "ring_6_green", "ring_6_blue",
    )
    assert [segment_participant(head.patch.id, n) for n in range(1, 7)] == [
        f"moving-head-1:ring:{n}" for n in range(1, 7)
    ]

    controller, lighting, _, _ = make_controller(patch)
    controller.set_values("moving-head-1", {
        "ring_red": 0.2,
        "ring_green": 0.4,
        "ring_blue": 0.6,
        "ring_1_red": 0.8,
    })
    state = controller.state("moving-head-1")
    assert state["ring_1_red"] == pytest.approx(0.8)
    assert [state[f"ring_{i}_green"] for i in range(1, 7)] == pytest.approx([0.4] * 6)
    assert [state[f"ring_{i}_blue"] for i in range(1, 7)] == pytest.approx([0.6] * 6)

    # Head 1 begins at address 7; its ring RGB starts at CH18 and repeats through CH35.
    channels = lighting.read_output(5)
    assert channels[23:41] == tuple([204, 102, 153] + [51, 102, 153] * 5)

    # Direct render_mode calls also retain the old uniform RGB API.
    mode = head.mode
    uniform = render_mode(mode, {"ring_red": 1.0, "ring_green": 0.5, "ring_blue": 0.0})
    assert uniform[17:35] == [255, 128, 0] * 6


def test_16ch_keeps_one_uniform_ring_participant_and_rejects_segment_attributes():
    patch = make_16ch_patch()
    head = patch.get("moving-head-1")
    assert ring_segments(head) == [1]
    assert segment_colour_attributes(head, 1) == ("ring_red", "ring_green", "ring_blue")
    assert fits(RING_COLOUR, head, component="ring")
    assert not fits(EffectType.CIRCLE, head, component="ring")

    controller, _, effects, _ = make_controller(patch)
    controller.set_values("moving-head-1", {"ring_red": 0.7})
    assert controller.state("moving-head-1")["ring_red"] == pytest.approx(0.7)
    with pytest.raises(ValueError, match="no attribute"):
        controller.set_values("moving-head-1", {"ring_1_red": 1.0})
    effects.start("physical-target", RING_COLOUR, 0, ["moving-head-1"], component="ring")
    assert effects.running("physical-target", RING_COLOUR, component="ring").fixtures == (
        "moving-head-1:ring:1",
    )


def test_legacy_scene_and_global_segment_masks_expand_before_filtering():
    patch = club_patch()
    controller, _, _, _ = make_controller(patch)
    store = SceneStore(patch)
    store.create(SceneContent(
        name="Legacy red",
        fixtures={"moving-head-1": {"ring_red": 0.7, "ring_1_red": 0.9}},
    ))
    scenes = SceneService(controller, store)
    scenes.recall("legacy-red")
    state = controller.state("moving-head-1")
    assert state["ring_1_red"] == pytest.approx(0.9)
    assert [state[f"ring_{i}_red"] for i in range(2, 7)] == pytest.approx([0.7] * 5)

    store.create(SceneContent(
        name="Legacy green",
        fixtures={"moving-head-1": {"ring_green": 0.6}},
    ))
    scenes.set_globals({"moving-head-1": ["ring_2_green"]})
    scenes.recall("legacy-green")
    state = controller.state("moving-head-1")
    assert state["ring_2_green"] == pytest.approx(0.0)
    assert [state[f"ring_{i}_green"] for i in (1, 3, 4, 5, 6)] == pytest.approx([0.6] * 5)

    scenes.set_globals({"moving-head-1": ["ring_blue"]})
    store.create(SceneContent(
        name="Legacy blue",
        fixtures={"moving-head-1": {"ring_blue": 0.8}},
    ))
    scenes.recall("legacy-blue")
    assert [controller.state("moving-head-1")[f"ring_{i}_blue"] for i in range(1, 7)] == [0.0] * 6


def test_sequence_segment_live_control_protects_only_that_section_from_legacy_scene():
    patch = club_patch()
    controller, _, _, clock = make_controller(patch)
    scene_store = SceneStore(patch)
    scene = scene_store.create(SceneContent(
        name="Uniform ring",
        fixtures={"moving-head-1": {"ring_red": 0.65}},
    ))
    scenes = SceneService(controller, scene_store)
    sequence_store = SequenceStore(scene_store)
    sequence = sequence_store.create(SequenceContent(
        name="Keep section two live",
        live_controls={"moving-head-1": ["ring_2_red"]},
        steps=[SequenceStep(scene_id=scene.id, fade=0.0, hold=1.0)],
    ))
    timeline = SequenceService(
        scenes, sequence_store, TempoClock(clock=clock, bpm=120.0), clock=clock
    )

    timeline.start(sequence.id)
    state = controller.state("moving-head-1")
    assert state["ring_2_red"] == pytest.approx(0.0)
    assert [state[f"ring_{i}_red"] for i in (1, 3, 4, 5, 6)] == pytest.approx([0.65] * 5)


def test_ring_chase_is_independent_from_beam_fx_and_does_not_change_base_values():
    patch = club_patch()
    controller, lighting, effects, clock = make_controller(patch)
    head_id = "moving-head-1"
    base = {
        **ring_values(red=0.5),
        "intensity": 0.8,
        "red": 0.25,
    }
    controller.set_values(head_id, base)
    participants = [segment_participant(head_id, i) for i in range(1, 7)]
    chase = FxDefinition(
        name="Odd ring chase",
        kind=RING_BEAM,
        params=EffectParams(
            beats=1.0, size=1.0, spread=0.0, softness=0.0,
            shape="square", selection="odd",
        ),
    )
    effects.start("operator", RING_BEAM, 0, participants, definition=chase, component="ring")
    effects.start("operator", RING_COLOUR, 0, [head_id])
    effects.start(
        "operator", RING_COLOUR, 0, participants,
        definition=FxDefinition(
            name="Ring colour",
            kind=RING_COLOUR,
            params=EffectParams(
                beats=4.0, size=1.0, spread=0.0, softness=0.0,
                colour_mode="palette", palette=["#ff0000", "#0000ff"],
            ),
        ),
        component="ring",
    )

    # Ring and beam colour slots can run for the same owner/page independently.
    assert effects.running("operator", RING_COLOUR) is not None
    assert effects.running("operator", RING_COLOUR, component="ring") is not None
    assert effects.set_param("operator", RING_COLOUR, "size", 0.25, component="ring")
    assert effects.running("operator", RING_COLOUR).params.size == pytest.approx(1.0)

    clock.now += 0.25
    output = controller.output_state(head_id)
    # The chase affects only odd ring participants (sections 1, 3, 5), not the beam.
    assert [output[f"ring_{i}_red"] for i in (1, 3, 5)] == pytest.approx([0.0] * 3)
    assert [output[f"ring_{i}_red"] for i in (2, 4, 6)] == pytest.approx([0.75] * 3)
    assert output["red"] == pytest.approx(0.25)
    assert controller.state(head_id)["red"] == pytest.approx(0.25)
    assert controller.state(head_id)["ring_1_red"] == pytest.approx(0.5)

    effects.stop("operator", RING_COLOUR)
    assert effects.running("operator", RING_COLOUR) is None
    assert effects.running("operator", RING_COLOUR, component="ring") is not None
    assert effects.any_active(component="ring")
    assert not effects.any_active(component="beam")
    assert lighting.read_output(5)[14] == 64  # beam RGB is separate from the ring pipeline


def test_dark_movement_only_blanks_beam_and_master_blackout_still_apply_to_ring():
    patch = club_patch()
    rig = make_rig(patch)
    rig.save_zone(ALL_FLOOR)
    rig.set_assignments({
        HEAD: HeadAssignment(include_zones=["floor-area", "all-floor"]),
    })
    clock = FakeClock()
    controller, lighting, effects, _ = make_controller(patch, clock=clock, rig=rig)
    controller.set_values(HEAD, {
        **ring_values(red=0.8),
        "intensity": 1.0,
        "strobe": 1.0,
        "ring_intensity": 1.0,
        "ring_strobe": 1.0,
        "pan": 0.5,
        "tilt": 0.5,
    })
    effects.start("beam-move", EffectType.CIRCLE, 0, [HEAD])
    clock.now += 2.2
    state = controller.output_state(HEAD)
    assert state["intensity"] == 0.0 and state["strobe"] == 0.0
    assert state["ring_intensity"] == 1.0 and state["ring_strobe"] == 1.0
    assert state["ring_1_red"] == pytest.approx(0.8)

    controller.set_master(0.5)
    channels = lighting.read_output(5)
    assert channels[18] == 128  # ring intensity is master-scaled
    assert channels[23] == 204  # RGB remains independent of master
    lighting.set_blackout(True)
    assert lighting.read_output(5)[18:41] == (0,) * 23


def test_ring_preview_changes_wait_for_go_and_ring_fx_persistence_is_separate(tmp_path):
    patch = club_patch()
    rig = make_rig(patch)
    controller, lighting, effects, clock = make_controller(patch, rig=rig)
    scenes = SceneService(controller, SceneStore(patch))
    preview = PreviewService(controller, scenes, effects)
    preview.start()

    controller.set_values("moving-head-1", {"ring_red": 0.75})
    assert controller.live_state("moving-head-1")["ring_1_red"] == pytest.approx(0.0)
    assert controller.state("moving-head-1")["ring_6_red"] == pytest.approx(0.75)
    assert lighting.read_output(5)[23] == 0

    participants = [segment_participant("moving-head-1", i) for i in range(1, 7)]
    effects.start(
        "preview-ring", RING_BEAM, 0, participants,
        component="ring",
    )
    clock.now += 0.25
    assert controller.preview_output_state("moving-head-1")["ring_1_red"] == 0.0
    preview.go()
    assert lighting.read_output(5)[23] == 0

    path = tmp_path / "fx-presets.json"
    store = FxPresetStore(patch, rig, path)
    definition = FxDefinition(
        name="Stored ring chase",
        kind=RING_BEAM,
        params=EffectParams(
            beats=2.0, size=0.4, spread=0.3, softness=0.0,
            order=participants, reverse=True,
        ),
    )
    store.save("moving-head-1", RING_BEAM, 0, definition, component="ring")
    assert store.get("moving-head-1", RING_BEAM, 0, component="ring") == definition
    assert store.get("moving-head-1", RING_BEAM, 0).name == "Pulse"
    document = json.loads(path.read_text())
    assert document["slots"][0]["component"] == "ring"
    loaded = FxPresetStore(patch, rig, path)
    assert loaded.get("moving-head-1", RING_BEAM, 0, component="ring") == definition


def test_preview_go_with_live_and_preview_ring_families_never_renders_pseudo_ids():
    patch = club_patch()
    controller, _, effects, _ = make_controller(patch)
    scenes = SceneService(controller, SceneStore(patch))
    preview = PreviewService(controller, scenes, effects)
    head_id = "moving-head-1"
    participants = [segment_participant(head_id, i) for i in range(1, 7)]
    controller.set_values(head_id, ring_values(red=0.5))
    effects.start("live-ring-dimmer", RING_BEAM, 0, participants, component="ring")

    preview.start()
    effects.start(
        "preview-ring-colour", RING_COLOUR, 0, participants,
        definition=FxDefinition(
            name="Preview palette", kind=RING_COLOUR,
            params=EffectParams(
                beats=4.0, size=1.0, spread=0.0, softness=0.0,
                colour_mode="palette", palette=["#ff0000", "#0000ff"],
            ),
        ),
        component="ring",
    )
    preview.go()
    assert controller.tick() is False
    assert controller.output_state(head_id)["ring_1_red"] >= 0.0
