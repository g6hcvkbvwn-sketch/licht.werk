"""Milestone 10 effects: tempo, effect maths, engine (render-time only, preview) and APC40 logic."""

import math

import pytest
from fastapi.testclient import TestClient

from lichtwerk import apc40 as hw
from lichtwerk.api import create_app
from lichtwerk.apc40_pads import PadStore
from lichtwerk.config import AppConfig
from lichtwerk.effects import (
    BEAT_CHOICES,
    PRESETS,
    EffectParams,
    EffectType,
    FxDefinition,
    beats_from_level,
    fits,
    fixture_phase,
    modulate,
    speed_level,
    waveform,
)
from lichtwerk.fixtures import MotionRange
from lichtwerk.midi_control import MidiEvent
from lichtwerk.scenes import SceneContent, SceneStore
from lichtwerk.tempo import MAX_BPM, MIN_BPM, TempoClock

from conftest import FakeClock
from test_apc40 import Rig, club_patch

CHASE, RAINBOW, TWO, CIRCLE, FIGURE8 = (
    EffectType.CHASE, EffectType.RAINBOW, EffectType.TWO_COLOUR, EffectType.CIRCLE,
    EffectType.FIGURE8,
)


def params(beats=1.0, size=1.0, spread=0.0, softness=0.0) -> EffectParams:
    return EffectParams(beats=beats, size=size, spread=spread, softness=softness)


@pytest.fixture
def rig() -> Rig:
    return Rig()


# --- tempo -------------------------------------------------------------------------------------


def test_tempo_counts_beats_and_bpm_changes_do_not_jump() -> None:
    clock = FakeClock()
    tempo = TempoClock(clock, bpm=120.0)
    assert tempo.bpm() == 120.0 and tempo.beats() == 0.0
    clock.now += 1.0
    assert tempo.beats() == pytest.approx(2.0)
    tempo.set_bpm(60.0)
    assert tempo.beats() == pytest.approx(2.0)  # no jump at the change
    clock.now += 1.0
    assert tempo.beats() == pytest.approx(3.0)
    assert tempo.adjust(0.1) == pytest.approx(60.1)
    assert tempo.adjust(-1000) == MIN_BPM
    assert tempo.adjust(1000) == MAX_BPM
    with pytest.raises(ValueError):
        tempo.set_bpm(10)
    with pytest.raises(ValueError):
        tempo.set_bpm(True)


def test_tap_tempo_averages_taps_resyncs_and_resets_after_a_pause() -> None:
    clock = FakeClock()
    tempo = TempoClock(clock)
    tempo.tap()
    assert tempo.bpm() == 128.0  # one tap alone does not change the tempo
    for _ in range(3):
        clock.now += 0.5005  # slightly irregular taps around 120 BPM
        tempo.tap()
    assert tempo.bpm() == pytest.approx(119.9, abs=0.1)
    assert tempo.beats() % 1.0 == pytest.approx(0.0)  # the tap is on the beat
    clock.now += 5.0  # pause: a new sequence starts
    tempo.tap()
    clock.now += 0.6
    assert tempo.tap() == pytest.approx(100.0)


# --- effect maths ------------------------------------------------------------------------------


def test_speed_knob_choices_run_slow_to_fast() -> None:
    assert beats_from_level(0.0) == 32 and beats_from_level(1.0) == 0.25
    assert speed_level(BEAT_CHOICES[0]) == 0.0 and speed_level(0.25) == 1.0
    assert speed_level(3.0) == speed_level(4.0)  # nearest choice


def test_waveform_and_spread() -> None:
    assert waveform(0.0, 0.0) == 1.0 and waveform(0.5, 0.0) == 0.0
    assert waveform(0.0, 1.0) == pytest.approx(1.0) and waveform(0.5, 1.0) == pytest.approx(0.0)
    assert waveform(0.25, 1.0) == pytest.approx(0.5)
    p = params(beats=2.0, spread=1.0)
    assert fixture_phase(1.0, p, 0, 2) == pytest.approx(0.5)
    assert fixture_phase(1.0, p, 1, 2) == pytest.approx(0.0)  # half a cycle behind


def test_chase_dims_by_size_and_never_raises_intensity() -> None:
    state = {"intensity": 0.8}
    assert modulate(CHASE, params(size=1.0), 0.0, state)["intensity"] == pytest.approx(0.8)
    assert modulate(CHASE, params(size=1.0), 0.5, state)["intensity"] == 0.0
    assert modulate(CHASE, params(size=0.5), 0.5, state)["intensity"] == pytest.approx(0.4)
    assert state == {"intensity": 0.8}  # input untouched


def test_rainbow_and_two_colour_keep_brightness() -> None:
    red = {"red": 0.5, "green": 0.0, "blue": 0.0}
    out = modulate(RAINBOW, params(size=1.0), 1 / 3, red)  # +120 degrees: green
    assert (out["red"], out["green"], out["blue"]) == pytest.approx((0.0, 0.5, 0.0))
    dark = modulate(RAINBOW, params(size=1.0), 0.0, {"red": 0.0, "green": 0.0, "blue": 0.0})
    assert dark["red"] == pytest.approx(1.0)  # no colour set: full red as the start
    first = modulate(TWO, params(size=1.0), 0.0, red)
    second = modulate(TWO, params(size=1.0), 0.5, red)
    assert (first["red"], first["green"], first["blue"]) == pytest.approx((0.5, 0.0, 0.0))
    assert (second["red"], second["green"], second["blue"]) == pytest.approx((0.0, 0.5, 0.5))


def test_movement_effects_are_sized_in_degrees_around_the_centre() -> None:
    motion = MotionRange(pan_degrees=540.0, tilt_degrees=210.0)
    centre = {"pan": 0.5, "tilt": 0.5, "speed": 0.2}
    out = modulate(CIRCLE, params(size=1.0), 0.0, centre, motion)
    assert out["pan"] == pytest.approx(0.5 + 30 / 540) and out["tilt"] == pytest.approx(0.5)
    out = modulate(CIRCLE, params(size=1.0), 0.25, centre, motion)
    assert out["pan"] == pytest.approx(0.5) and out["tilt"] == pytest.approx(0.5 + 30 / 210)
    out = modulate(FIGURE8, params(size=0.5), 0.125, centre, motion)
    assert out["pan"] == pytest.approx(0.5 + 15 * math.sin(math.pi / 4) / 540)
    assert out["tilt"] == pytest.approx(0.5 + 7.5 / 210)
    assert out["speed"] == 1.0
    assert centre["speed"] == 0.2
    assert modulate(CIRCLE, params(), 0.0, centre, None) == centre  # no motion range: untouched


def test_effect_types_fit_only_suitable_fixtures() -> None:
    patch = club_patch()
    bar, head = patch.get("led-bar-1"), patch.get("moving-head-1")
    assert fits(CHASE, bar) and fits(RAINBOW, bar) and not fits(CIRCLE, bar)
    assert fits(CIRCLE, head) and fits(TWO, head)
    assert all(len(presets) == 5 for presets in PRESETS.values())


# --- engine in the render pipeline -------------------------------------------------------------


def test_chase_modulates_output_only_and_follows_the_tempo(rig) -> None:
    rig.tempo.set_bpm(120.0)
    rig.controller.set_values("led-bar-1", {"intensity": 0.8, "red": 1.0})
    rig.effects.start("test", CHASE, 0, ["led-bar-1"])  # Pulse: 1 beat, smooth
    rig.controller.tick()
    assert rig.controller.output_state("led-bar-1")["intensity"] == pytest.approx(0.8)
    rig.clock.now += 0.25  # half a beat at 120 BPM
    rig.controller.tick()
    assert rig.controller.output_state("led-bar-1")["intensity"] == pytest.approx(0.0)
    assert rig.service.read_output(5)[0] == 0
    assert rig.controller.state("led-bar-1")["intensity"] == 0.8  # live state never changes
    rig.effects.stop("test", CHASE)
    rig.controller.tick()
    assert rig.service.read_output(5)[0] == round(0.8 * 255)


def test_effects_never_change_scenes_and_scene_recall_keeps_effects(rig) -> None:
    before = rig.scenes.store.get("red").model_dump()
    rig.effects.start("test", RAINBOW, 0, ["led-bar-1"])
    rig.scenes.recall("red")
    rig.clock.now += 2.0
    rig.controller.tick()
    assert rig.effects.running("test", RAINBOW) is not None
    assert rig.scenes.store.get("red").model_dump() == before
    assert rig.controller.state("led-bar-1")["red"] == 1.0


def test_master_scales_after_effects_and_flash_overrides_the_chase(rig) -> None:
    rig.controller.set_values("led-bar-1", {"intensity": 1.0})
    rig.effects.start("test", CHASE, 1, ["led-bar-1"])  # hard chase
    rig.clock.now += 0.25  # off half
    rig.controller.tick()
    assert rig.service.read_output(5)[0] == 0
    rig.controller.hold("flash", {"led-bar-1": {"intensity": 1.0}})
    assert rig.service.read_output(5)[0] == 255
    rig.controller.release("flash")
    rig.clock.now += 0.25  # on half
    rig.controller.set_master(0.5)
    rig.controller.tick()
    assert rig.service.read_output(5)[0] == round(0.5 * 255)


def test_preview_holds_effect_changes_until_go(rig) -> None:
    rig.controller.set_values("led-bar-1", {"intensity": 1.0})
    rig.preview.start()
    rig.effects.start("test", CHASE, 1, ["led-bar-1"])
    rig.clock.now += 0.25
    rig.controller.tick()
    assert rig.service.read_output(5)[0] == 255  # not live yet
    rig.preview.go()
    rig.controller.tick()
    assert rig.service.read_output(5)[0] == 0
    rig.preview.start()
    rig.effects.stop("test", CHASE)
    rig.controller.tick()
    assert rig.service.read_output(5)[0] == 0  # still running live
    rig.preview.go()
    rig.controller.tick()
    assert rig.service.read_output(5)[0] == 255


def test_circle_moves_heads_but_limits_still_apply(rig) -> None:
    rig.controller.set_values("moving-head-1", {"pan": 1.0, "tilt": 0.5})
    rig.effects.start("test", CIRCLE, 2, ["moving-head-1"])  # big circle
    rig.controller.tick()
    out = rig.controller.output_state("moving-head-1")
    assert out["pan"] == 1.0  # clamped at the end of the range
    rig.clock.now += 1.0  # a quarter of 8 beats: top of the circle
    rig.controller.tick()
    out = rig.controller.output_state("moving-head-1")
    assert out["tilt"] > 0.5
    assert rig.controller.state("moving-head-1")["tilt"] == 0.5


# --- APC40 -------------------------------------------------------------------------------------


def press(rig, number, channel=1):
    rig.apc.handle(MidiEvent("note", channel, number, 127))
    rig.apc.handle(MidiEvent("note", channel, number, 0))


def test_scene_launch_opens_and_closes_the_effects_page(rig) -> None:
    press(rig, hw.NOTE_CLIP_STOP, channel=1)
    press(rig, hw.NOTE_SCENE_LAUNCH_FIRST + 1)
    assert rig.apc.fx_open() and rig.apc.fx_type() is RAINBOW
    assert rig.apc.selected() is None  # colour page closed
    press(rig, hw.NOTE_SCENE_LAUNCH_FIRST + 2)  # Move family: page stays open
    assert rig.apc.fx_open() and rig.apc.fx_type() is CIRCLE
    press(rig, hw.NOTE_SCENE_LAUNCH_FIRST + 2)  # the shown family again: close
    assert not rig.apc.fx_open()
    press(rig, hw.NOTE_SCENE_LAUNCH_FIRST)
    press(rig, hw.NOTE_CLIP_STOP, channel=5)  # CLIP STOP closes the effects page
    assert not rig.apc.fx_open() and rig.apc.selected() == 5


def test_track_select_does_not_open_the_page_but_picks_the_knob_strip(rig) -> None:
    press(rig, hw.NOTE_TRACK_SELECT, channel=1)  # remembers stop/edit strip without opening page
    assert not rig.apc.fx_open() and rig.apc.fx_track() == 1
    press(rig, hw.NOTE_SCENE_LAUNCH_FIRST)
    assert rig.apc.fx_track() == 1
    assert rig.apc.leds()[("note", 1, hw.NOTE_TRACK_SELECT)] == hw.LED_ON
    rig.clock.now += 0.25
    assert rig.apc.leds()[("note", 1, hw.NOTE_TRACK_SELECT)] == hw.LED_OFF  # blinking
    press(rig, hw.NOTE_TRACK_SELECT, channel=1)  # again: no strip on the knobs
    assert rig.apc.fx_track() is None
    press(rig, hw.NOTE_TRACK_SELECT, channel=8)  # free track: nothing to edit
    assert rig.apc.fx_track() is None
    press(rig, hw.pad_note(1))  # playing a pad puts its strip on the knobs
    assert rig.apc.fx_track() == 2


def test_scene_launch_leds_show_types_and_blink_while_running(rig) -> None:
    assert rig.apc.scene_launch_colours() == [14, 54, 22, 0, 0]  # three families; spare buttons dark
    press(rig, hw.NOTE_SCENE_LAUNCH_FIRST + 1)
    assert rig.apc.scene_launch_colours() == [14, 53, 22, 0, 0]  # shown family bright
    press(rig, hw.pad_note(1))  # rainbow on the heads
    press(rig, hw.NOTE_SCENE_LAUNCH_FIRST + 1)  # close the page: still blinking
    assert rig.apc.scene_launch_colours(blink_on=True) == [14, 54, 22, 0, 0]
    assert rig.apc.scene_launch_colours(blink_on=False) == [14, 0, 22, 0, 0]
    rig.clock.now += 0.5
    assert rig.apc.leds()[("note", 1, hw.NOTE_SCENE_LAUNCH_FIRST + 1)] == 0
    press(rig, hw.NOTE_ACTIVATOR, channel=3)  # heads in single mode: effect paused
    assert rig.apc.scene_launch_colours(blink_on=False)[1] == 54


def test_shift_effect_pad_requests_the_slot_editor_without_starting_fx(rig) -> None:
    press(rig, hw.NOTE_SCENE_LAUNCH_FIRST)
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_SHIFT, 127))
    press(rig, hw.pad_note(1 + 16))
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_SHIFT, 0))
    request_id, request = rig.apc.fx_dialog_request()
    assert request_id == 1 and request == (2, "chase", 2, "beam")
    assert rig.apc.running_effect(2, CHASE) is None


def test_device_buttons_switch_pad_pages_and_show_the_active_one(rig) -> None:
    leds = rig.apc.leds()
    assert leds[("note", 1, hw.NOTE_DEVICE_LEFT)] == hw.LED_ON and leds[("note", 1, hw.NOTE_DEVICE_RIGHT)] == hw.LED_OFF
    press(rig, hw.NOTE_DEVICE_RIGHT)
    assert rig.apc.show_page() == "sequences"
    leds = rig.apc.leds()
    assert leds[("note", 1, hw.NOTE_DEVICE_LEFT)] == hw.LED_OFF and leds[("note", 1, hw.NOTE_DEVICE_RIGHT)] == hw.LED_ON
    press(rig, hw.NOTE_LEFT)  # bank select no longer switches pages
    assert rig.apc.show_page() == "sequences"


def test_effect_pads_start_switch_and_stop_presets(rig, monkeypatch) -> None:
    # This test covers pad routing; the positioning tests exercise geometry permission.
    monkeypatch.setattr(rig.positions, "movement_allowed", lambda head: True)
    monkeypatch.setattr(rig.positions, "prepare_movement", lambda heads: None)
    press(rig, hw.NOTE_SCENE_LAUNCH_FIRST)
    colours = rig.apc.pad_colours()
    assert colours[1] == 14 and colours[0] == 14  # heads and LED bar columns: dim chase
    assert colours[2] == 0 and colours[5] == 0  # single strips are inactive in group mode
    press(rig, hw.pad_note(1 + 8))  # heads column, row 2
    effect = rig.apc.running_effect(2, CHASE)
    assert effect.preset == 1 and effect.fixtures == ("moving-head-1", "moving-head-2")
    assert rig.apc.pad_colours(blink_on=True)[9] == 13
    assert rig.apc.pad_colours(blink_on=False)[9] == 0
    press(rig, hw.pad_note(1 + 16))  # another preset of the same type replaces it
    assert rig.apc.running_effect(2, CHASE).preset == 2
    press(rig, hw.NOTE_SCENE_LAUNCH_FIRST + 2)
    press(rig, hw.pad_note(1))  # circle combines with the chase
    assert rig.apc.running_effect(2, CIRCLE).preset == 0
    press(rig, hw.NOTE_TRACK_SELECT, channel=2)  # knobs off the heads: LED shows "running"
    assert rig.apc.leds()[("note", 2, hw.NOTE_TRACK_SELECT)] == hw.LED_ON
    press(rig, hw.pad_note(1))  # same pad again: stop
    assert rig.apc.running_effect(2, CIRCLE) is None
    press(rig, hw.pad_note(0))  # LED bar cannot move: nothing
    assert rig.apc.running_effect(1, CIRCLE) is None


def test_device_knobs_edit_the_running_effect(rig) -> None:
    press(rig, hw.NOTE_SCENE_LAUNCH_FIRST)
    knobs = rig.apc.device_knobs()
    assert knobs[1] == ("speed", "Rate", None) and knobs[6][0] == "softness"
    rig.cc(hw.CC_DEVICE_KNOB_FIRST, 127)  # no strip picked: ignored
    press(rig, hw.NOTE_TRACK_SELECT, channel=1)
    rig.cc(hw.CC_DEVICE_KNOB_FIRST, 127)  # nothing running: ignored
    press(rig, hw.pad_note(0))
    rig.cc(hw.CC_DEVICE_KNOB_FIRST, 127)  # fastest
    rig.cc(hw.CC_DEVICE_KNOB_FIRST + 1, 0)  # size 0
    effect = rig.apc.running_effect(1, CHASE)
    assert effect.params.beats == 0.25 and effect.params.size == 0.0
    assert rig.apc.device_knobs()[1][2] == 1.0
    assert rig.apc.leds()[("cc", 1, hw.CC_DEVICE_KNOB_FIRST)] == 127
    press(rig, hw.NOTE_SCENE_LAUNCH_FIRST + 1)  # rainbow has no softness
    assert 6 not in rig.apc.device_knobs()
    assert rig.state("led-bar-1")["intensity"] == 0.0  # knobs never touch the state


def test_effects_follow_the_group_single_mode(rig) -> None:
    press(rig, hw.NOTE_SCENE_LAUNCH_FIRST)
    press(rig, hw.pad_note(1))  # chase on the heads group
    press(rig, hw.NOTE_ACTIVATOR, channel=3)  # single mode
    assert not rig.apc.running_effect(2, CHASE).active
    assert rig.apc.pad_colours()[1] == 0 and rig.apc.pad_colours()[2] == 14
    assert rig.effects.take_render_set() >= {"moving-head-1", "moving-head-2"}  # restored once
    assert rig.effects.take_render_set() == set()
    press(rig, hw.NOTE_ACTIVATOR, channel=2)
    assert rig.apc.running_effect(2, CHASE).active


def test_tap_tempo_and_tempo_knob(rig) -> None:
    press(rig, hw.NOTE_TAP_TEMPO)
    rig.clock.now += 0.5
    press(rig, hw.NOTE_TAP_TEMPO)
    assert rig.apc.bpm() == 120.0
    rig.clock.now += 0.4
    press(rig, hw.NOTE_TAP_TEMPO)
    assert rig.apc.bpm() == pytest.approx(133.3)
    rig.cc(hw.CC_TEMPO, 3)  # three detents clockwise
    assert rig.apc.bpm() == pytest.approx(133.6)
    rig.cc(hw.CC_TEMPO, 127)  # one detent back
    assert rig.apc.bpm() == pytest.approx(133.5)


def test_clip_stop_led_shows_colour_set_and_current_colour_pad_blinks(rig) -> None:
    assert rig.apc.leds()[("note", 1, hw.NOTE_CLIP_STOP)] == hw.LED_OFF
    press(rig, hw.NOTE_CLIP_STOP, channel=1)
    press(rig, hw.pad_note(5))
    assert rig.apc.pad_colours(blink_on=False)[5] == 0
    assert rig.apc.pad_colours(blink_on=False)[4] != 0
    rig.clock.now += 0.5
    assert rig.apc.leds()[("note", 1, hw.NOTE_CLIP_STOP)] == hw.LED_OFF  # blinking: page open
    press(rig, hw.NOTE_CLIP_STOP, channel=1)  # close
    assert rig.apc.leds()[("note", 1, hw.NOTE_CLIP_STOP)] == hw.LED_ON  # colour set
    rig.cc(hw.CC_TRACK_KNOB_FIRST, 64)  # saturation knob: no pad matches any more
    assert rig.apc.settings(1).colour_pad is None


# --- API ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", [RAINBOW, TWO])
def test_colour_fx_picker_edits_runtime_not_fixture_or_preset(rig, kind) -> None:
    definition = FxDefinition(name="Test colour", kind=kind, params=params())
    rig.apc.start_effect(1, RAINBOW, 0, definition)
    original = rig.apc.running_effect(1, RAINBOW)
    base = dict(rig.state("led-bar-1"))
    rig.press(hw.NOTE_CLIP_STOP)
    assert rig.apc.palette_editing()
    assert rig.apc.palette_pads() == {5: [1], 7: [2]}
    assert rig.apc.pad_colours(False)[5] == 0
    assert rig.apc.pad_colours(True)[5] != 0
    rig.press(hw.pad_note(0))  # add saturated red
    running = rig.apc.running_effect(1, RAINBOW)
    assert running.params.palette == ["#0000ff", "#ff00ff", "#ff0000"]
    assert running.params.colour_mode == "palette"
    assert running.kind == kind and running.preset == original.preset
    assert running.launched_at == original.launched_at
    assert definition.params.palette == ["#0000ff", "#ff00ff"]
    assert rig.state("led-bar-1") == base
    rig.press(hw.pad_note(5))  # remove blue
    assert rig.apc.colour_fx_palette() == ["#ff00ff", "#ff0000"]
    rig.press(hw.pad_note(7))  # cannot remove below two
    assert "2–8" in rig.apc.last_error()
    assert rig.apc.colour_fx_palette() == ["#ff00ff", "#ff0000"]
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_SHIFT, 127))
    rig.press(hw.NOTE_CLIP_STOP)  # SHIFT+COLOR switches directly to fixture control
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_SHIFT, 0))
    assert not rig.apc.palette_editing()
    rig.press(hw.pad_note(5))
    assert rig.apc.colour_fx_palette() == []
    assert rig.apc.running_effect(1, RAINBOW).params.palette == ["#ff00ff", "#ff0000"]


def test_colour_fx_picker_white_limit_and_stopped_effect(rig) -> None:
    rig.apc.start_effect(1, RAINBOW, 0)
    rig.press(hw.NOTE_CLIP_STOP)
    for pad in [0, 1, 2, 3, 4, hw.WHITE_PAD]:
        rig.press(hw.pad_note(pad))
    assert len(rig.apc.colour_fx_palette()) == 8
    assert rig.apc.colour_fx_palette()[-1] == "#ffffff"
    rig.press(hw.pad_note(6))
    assert "2–8" in rig.apc.last_error()
    assert len(rig.apc.colour_fx_palette()) == 8
    rig.press(hw.pad_note(hw.WHITE_PAD))
    assert len(rig.apc.colour_fx_palette()) == 7
    base = dict(rig.state("led-bar-1"))
    rig.effects.stop_all()
    rig.press(hw.pad_note(0))
    assert "stopped" in rig.apc.last_error()
    assert rig.state("led-bar-1") == base


def test_colour_fx_picker_preview_is_separate_and_track_selection_closes_picker(rig) -> None:
    rig.apc.start_effect(1, RAINBOW, 0)
    rig.preview.toggle()
    rig.press(hw.NOTE_CLIP_STOP)
    rig.press(hw.pad_note(0))
    assert len(rig.apc.colour_fx_palette()) == 3
    assert len(rig.effects.all_running(preview=False)[0][2].params.palette) == 2
    rig.press(hw.NOTE_TRACK_SELECT, channel=2)
    assert not rig.apc.palette_editing()


@pytest.fixture
def client(tmp_path):
    patch = club_patch()
    store = SceneStore(patch)
    store.create(SceneContent(name="Red", fade_seconds=0.0, fixtures={"led-bar-1": {"red": 1.0}}))
    app = create_app(
        AppConfig(universes=[5]), patch=patch, scene_store=store,
        apc40_pads=PadStore(tmp_path / "apc40.json"),
    )
    with TestClient(app) as c:
        yield c


def test_api_effects_page_and_typed_bpm(client) -> None:
    view = client.get("/api/apc40").json()
    assert view["pad_mode"] == "scenes" and view["bpm"] == 128.0
    assert [knob["knob"] for knob in view["knobs"]] == [1, 2, 3, 5, 6, 7]
    view = client.post(
        "/api/apc40/event",
        json={"kind": "note", "channel": 1, "number": hw.NOTE_SCENE_LAUNCH_FIRST, "value": 127},
    ).json()
    assert view["pad_mode"] == "effects" and view["fx_track"] is None
    assert view["effect_types"][0]["selected"] is True
    view = client.post(
        "/api/apc40/event",
        json={"kind": "note", "channel": 2, "number": hw.NOTE_TRACK_SELECT, "value": 127},
    ).json()
    assert view["fx_track"] == 2
    assert [t["available"] for t in view["effect_types"]] == [True] * 3
    assert view["pads"][1]["label"] == "Pulse" and view["knobs"][0]["label"] == "Rate"
    assert view["strips"][1]["fx_knobs"] is True
    view = client.post(
        "/api/apc40/event",
        json={"kind": "note", "channel": 1, "number": hw.pad_note(1), "value": 127},
    ).json()
    assert view["pads"][1]["active"] is True and view["strips"][1]["fx_running"] is True
    assert view["effect_types"][0]["running"] is True
    for value, shape in [(0, "Legacy"), (21, "Sine"), (42, "Triangle"), (64, "Square"),
                         (85, "Saw Up"), (106, "Saw Down"), (127, "One By One")]:
        response = client.post(
            "/api/apc40/event",
            json={"kind": "cc", "channel": 1, "number": hw.CC_DEVICE_KNOB_FIRST + 2, "value": value},
        )
        assert response.status_code == 200
        knobs = {knob["knob"]: knob for knob in response.json()["knobs"]}
        assert knobs[3]["function"] == "shape"
        assert knobs[3]["display"] == shape
        assert knobs[3]["value"] == pytest.approx(round(value / 127 * 6) / 6)
    response = client.post(
        "/api/apc40/event",
        json={"kind": "cc", "channel": 1, "number": hw.CC_DEVICE_KNOB_FIRST + 6, "value": 64},
    )
    assert response.status_code == 200
    knobs = {knob["knob"]: knob for knob in response.json()["knobs"]}
    assert knobs[7]["function"] == "maximum"
    assert knobs[7]["value"] == pytest.approx(64 / 127)
    assert knobs[7]["display"] == "50%"
    assert client.put("/api/apc40/tempo", json={"bpm": 128}).json()["bpm"] == 128.0
    assert client.put("/api/apc40/tempo", json={"bpm": 500}).status_code == 422


def test_api_colour_palette_picker_matches_hardware_feedback(client) -> None:
    def event(number, kind="note", value=127):
        response = client.post("/api/apc40/event", json={
            "kind": kind, "channel": 1, "number": number, "value": value,
        })
        assert response.status_code == 200
        return response.json()

    event(hw.NOTE_SCENE_LAUNCH_FIRST + 1)
    event(hw.pad_note(0))
    view = event(hw.NOTE_CLIP_STOP)
    assert view["fx_palette"] and view["pad_mode"] == "colours"
    assert view["pads"][5]["active"] and view["pads"][5]["kind"] == "fx_colour"
    assert view["pads"][5]["label"] == "1"
    view = event(hw.pad_note(0))
    assert view["colour_fx_palette"] == ["#0000ff", "#ff00ff", "#ff0000"]
    assert view["pads"][0]["active"] and view["pads"][0]["label"] == "3"
    view = event(hw.CC_DEVICE_KNOB_FIRST, kind="cc", value=127)
    assert view["knobs"][0]["value"] == 1.0  # knobs still edit Colour FX
