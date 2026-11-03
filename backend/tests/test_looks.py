"""Looks (scenes with an FX set) and fader soft takeover; simulated output only."""

import pytest

from conftest import FakeClock
from lichtwerk import apc40 as hw
from lichtwerk.apc40_control import Apc40Control
from lichtwerk.apc40_pads import PadStore
from lichtwerk.effect_engine import EffectEngine
from lichtwerk.effects import PRESETS, ComboDefinition, EffectType, FxDefinition
from lichtwerk.fixture_service import FixtureController
from lichtwerk.fx_presets import FxPresetStore
from lichtwerk.fx_service import FxService
from lichtwerk.looks import LookPlayer
from lichtwerk.midi_control import MidiEvent
from lichtwerk.output import SimulatedDmxOutput
from lichtwerk.position_service import PositionService
from lichtwerk.preview import PreviewService
from lichtwerk.room import RigStore
from lichtwerk.scene_service import SceneService
from lichtwerk.scenes import LookFx, LookStripFx, SceneContent, SceneFile, SceneStore
from lichtwerk.service import LightingService
from lichtwerk.tempo import TempoClock
from test_apc40 import club_patch

BAR, HEADS, HEAD1 = "apc40:1", "apc40:2", "apc40:3"
SPOTS = "apc40:5"


def definition(kind: EffectType, row: int = 0) -> FxDefinition:
    source = PRESETS[kind][row]
    return FxDefinition(name=source.name, kind=kind, params=source.params)


class LookRig:
    def __init__(self) -> None:
        self.clock = FakeClock()
        self.patch = club_patch()
        self.service = LightingService(SimulatedDmxOutput(), (5,))
        rig = RigStore(self.patch)
        self.controller = FixtureController(self.patch, self.service, clock=self.clock, rig=rig)
        self.store = SceneStore(self.patch)
        self.scenes = SceneService(self.controller, self.store)
        self.positions = PositionService(self.controller, rig)
        self.scenes.set_positions(self.positions)
        self.effects = EffectEngine(self.patch, TempoClock(clock=self.clock), rig=rig)
        self.controller.set_effects(self.effects)
        self.fx = FxService(self.controller, self.positions, self.effects, FxPresetStore(self.patch, rig), 40)
        self.preview = PreviewService(self.controller, self.scenes, self.effects, positions=self.positions)
        self.pads = PadStore()
        self.apc = Apc40Control(
            self.controller, self.scenes, self.service, self.positions, self.pads,
            clock=self.clock, preview=self.preview, effects=self.effects, fx_service=self.fx,
        )
        self.scenes.looks = LookPlayer(self.apc, self.scenes)
        self.scenes.snapshot_listeners.append(self.apc.scene_applied)

    def press(self, number: int, channel: int = 1) -> None:
        self.apc.handle(MidiEvent("note", channel, number, 127))
        self.apc.handle(MidiEvent("note", channel, number, 0))

    def fader(self, track: int, value: int, virtual: bool = False) -> None:
        self.apc.handle(MidiEvent("cc", track, hw.CC_TRACK_FADER, value), virtual=virtual)

    def start(self, track: int, kind: EffectType, row: int = 0) -> None:
        self.apc.start_effect(track, kind, row)

    def running(self, owner: str, kind: EffectType):
        return self.effects.running(owner, kind)


@pytest.fixture
def rig() -> LookRig:
    return LookRig()


def test_capture_live_stores_running_fx_with_live_tweaks_and_modes(rig):
    rig.start(1, EffectType.CHASE, 1)
    rig.effects.set_param(BAR, EffectType.CHASE, "size", 0.3)
    rig.start(5, EffectType.CHASE)  # DJ spots: not part of the look below
    scene = rig.scenes.capture("Drop", 0.0, fixture_ids=["led-bar-1", "moving-head-1"], include_fx=True)
    assert scene.fx is not None
    assert scene.fx.modes == {"heads": "group"}
    [entry] = scene.fx.strips
    assert (entry.strip, entry.preset, entry.combo) == ("led-bar-1", 1, None)
    assert entry.effects[0].params.size == 0.3
    # Human-readable JSON that loads back unchanged.
    data = SceneFile(scenes=[scene]).model_dump(mode="json")
    assert SceneFile.model_validate(data).scenes[0] == scene


def test_capture_without_fx_is_a_plain_scene(rig):
    rig.start(1, EffectType.CHASE)
    assert rig.scenes.capture("Plain", 0.0, fixture_ids=["led-bar-1"]).fx is None


def test_recall_replaces_fx_only_on_strips_in_the_look(rig):
    rig.start(1, EffectType.CHASE, 1)
    look = rig.scenes.capture("Look", 0.0, fixture_ids=["led-bar-1"], include_fx=True)
    rig.effects.stop_all(BAR)
    rig.start(1, EffectType.RAINBOW)  # another FX on the look's strip: stopped by the recall
    rig.start(5, EffectType.CHASE)  # DJ spots are not in the look: keep running
    rig.scenes.recall(look.id)
    assert rig.running(BAR, EffectType.CHASE).preset == 1
    assert rig.running(BAR, EffectType.RAINBOW) is None
    assert rig.running(SPOTS, EffectType.CHASE) is not None
    assert rig.scenes.look_notice() is None
    assert rig.store.get(look.id) == look  # recall never changes the stored look


def test_old_scene_leaves_fx_untouched(rig):
    rig.store.create(SceneContent(name="Old", fixtures={"led-bar-1": {"red": 1.0}}))
    rig.start(1, EffectType.CHASE)
    rig.scenes.recall("old")
    assert rig.running(BAR, EffectType.CHASE) is not None


def test_look_without_fx_stops_fx_of_its_strips(rig):
    look = rig.scenes.capture("Still", 0.0, fixture_ids=["led-bar-1"], include_fx=True)
    rig.start(1, EffectType.CHASE)
    rig.scenes.recall(look.id)
    assert rig.running(BAR, EffectType.CHASE) is None


def test_recall_switches_to_the_captured_strip_mode(rig):
    rig.press(hw.NOTE_ACTIVATOR, channel=3)  # heads to single mode
    rig.start(3, EffectType.CHASE)
    look = rig.scenes.capture("Single", 0.0, fixture_ids=["moving-head-1"], include_fx=True)
    assert look.fx.modes == {"heads": "single"}
    rig.press(hw.NOTE_ACTIVATOR, channel=2)  # back to group mode
    assert rig.apc.mode("heads") == "group"
    rig.scenes.recall(look.id)
    assert rig.apc.mode("heads") == "single"
    assert rig.running(HEAD1, EffectType.CHASE).active


def test_look_without_fx_still_restores_single_mode(rig):
    rig.press(hw.NOTE_ACTIVATOR, channel=6)  # DJ spots to single mode, no FX running
    look = rig.scenes.capture("Booth", 0.0, fixture_ids=["dj-spot-1", "dj-spot-2"], include_fx=True)
    assert look.fx.modes == {"spots": "single"} and look.fx.strips == []
    rig.press(hw.NOTE_ACTIVATOR, channel=5)  # back to group mode
    assert rig.apc.mode("spots") == "group"
    rig.scenes.recall(look.id)
    assert rig.apc.mode("spots") == "single"


def test_blocked_move_fx_reports_and_the_rest_still_runs(rig):
    move = LookStripFx(strip="moving-heads", preset=0, effects=[definition(EffectType.CIRCLE)])
    dimmer = LookStripFx(strip="led-bar-1", preset=2, effects=[definition(EffectType.CHASE, 2)])
    rig.store.create(SceneContent(
        name="Drop", fixtures={"led-bar-1": {"intensity": 1.0}, "moving-head-1": {"intensity": 1.0}},
        fx=LookFx(modes={"heads": "group"}, strips=[move, dimmer]),
    ))
    rig.press(hw.pad_note(0))  # an empty pad first: nothing is assigned yet
    rig.scenes.recall("drop")
    assert rig.controller.state("led-bar-1")["intensity"] == 1.0
    assert rig.running(BAR, EffectType.CHASE).preset == 2
    assert rig.running(HEADS, EffectType.CIRCLE) is None  # uncalibrated heads: never bypassed
    notice = rig.scenes.look_notice()
    assert notice and "Move FX on Heads" in notice


def test_combo_runs_its_allowed_members_and_reports_the_rest(rig):
    combo = LookStripFx(strip="moving-heads", preset=1, combo="Peak",
                        effects=[definition(EffectType.CHASE), definition(EffectType.CIRCLE)])
    rig.store.create(SceneContent(
        name="Peak", fixtures={"moving-head-1": {"intensity": 1.0}},
        fx=LookFx(modes={"heads": "group"}, strips=[combo]),
    ))
    rig.scenes.recall("peak")
    [running] = rig.effects.combo_effects(HEADS)
    assert (running.kind, running.combo_name, running.combo_slot) == (EffectType.CHASE, "Peak", 1)
    assert "Move FX of Combo 'Peak'" in rig.scenes.look_notice()


def test_look_in_preview_is_staged_until_go(rig):
    rig.start(1, EffectType.RAINBOW)
    look = rig.store.create(SceneContent(
        name="Staged", fixtures={"led-bar-1": {"intensity": 1.0}},
        fx=LookFx(strips=[LookStripFx(strip="led-bar-1", preset=0, effects=[definition(EffectType.CHASE)])]),
    ))
    rig.preview.start()
    rig.scenes.recall(look.id)
    live = {(o, p) for o, p, _ in rig.effects.all_running(preview=False)}
    assert live == {(BAR, EffectType.RAINBOW)}
    rig.preview.go()
    live = {(o, p) for o, p, _ in rig.effects.all_running(preview=False)}
    assert live == {(BAR, EffectType.CHASE)}


def test_store_refuses_looks_for_unknown_strips_and_missing_route_targets(rig):
    unknown = LookFx(strips=[LookStripFx(strip="nope", preset=0, effects=[definition(EffectType.CHASE)])])
    with pytest.raises(ValueError, match="unknown strip 'nope'"):
        rig.store.create(SceneContent(name="X", fixtures={"led-bar-1": {"red": 1.0}}, fx=unknown))
    params = PRESETS[EffectType.CIRCLE][0].params.model_copy(update={"target_ids": ["gone"]})
    route = FxDefinition(name="Route", kind=EffectType.CIRCLE, params=params)
    missing = LookFx(strips=[LookStripFx(strip="moving-heads", preset=0, effects=[route])])
    with pytest.raises(ValueError, match="missing targets/zones \\(gone\\)"):
        rig.store.create(SceneContent(name="Y", fixtures={"moving-head-1": {"intensity": 1.0}}, fx=missing))


def test_shift_on_an_empty_scene_pad_opens_capture_live(rig):
    rig.press(hw.pad_note(4))
    assert rig.apc.show_dialog_request()[1:] == ("scene", None, 4, False)
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_SHIFT, 127))
    rig.press(hw.pad_note(5))
    assert rig.apc.show_dialog_request()[1:] == ("scene", None, 5, True)


# --- soft takeover ---------------------------------------------------------------------------


def bar_level(rig) -> float:
    return rig.controller.state("led-bar-1")["intensity"]


def test_fader_is_detached_after_a_recall_until_it_passes_the_level(rig):
    rig.fader(1, 25)
    rig.store.create(SceneContent(name="Bright", fixtures={"led-bar-1": {"intensity": 0.8}}))
    rig.scenes.recall("bright")
    assert rig.apc.pickup(1)
    rig.fader(1, 40)  # below the level: the light does not jump
    assert bar_level(rig) == 0.8
    assert rig.apc.fader(1) == 40 / 127  # the screen shows the physical position
    rig.fader(1, 110)  # passes 0.8: caught
    assert not rig.apc.pickup(1)
    assert bar_level(rig) == 110 / 127
    rig.fader(1, 60)  # attached again: last input wins
    assert bar_level(rig) == 60 / 127


def test_unknown_fader_position_is_caught_near_the_level(rig):
    rig.store.create(SceneContent(name="Half", fixtures={"led-bar-1": {"intensity": 0.5}}))
    rig.scenes.recall("half")
    rig.fader(1, 20)
    assert bar_level(rig) == 0.5
    rig.fader(1, 64)
    assert not rig.apc.pickup(1)


def test_on_screen_fader_takes_over_at_once(rig):
    rig.store.create(SceneContent(name="Half", fixtures={"led-bar-1": {"intensity": 0.5}}))
    rig.scenes.recall("half")
    rig.fader(1, 10, virtual=True)
    assert bar_level(rig) == 10 / 127
    assert not rig.apc.pickup(1)


def test_scene_without_levels_does_not_detach_and_master_is_never_detached(rig):
    rig.store.create(SceneContent(name="Red", fixtures={"led-bar-1": {"red": 1.0}}))
    rig.scenes.recall("red")
    assert not rig.apc.pickup(1)
    rig.apc.handle(MidiEvent("cc", 1, hw.CC_MASTER_FADER, 64))
    assert rig.controller.master() == pytest.approx(64 / 127)


def test_detached_fader_is_not_reapplied_when_its_strip_becomes_active(rig):
    rig.press(hw.NOTE_ACTIVATOR, channel=3)  # heads single
    rig.fader(3, 10)
    rig.press(hw.NOTE_ACTIVATOR, channel=2)  # heads group
    rig.store.create(SceneContent(name="Heads", fixtures={"moving-head-1": {"intensity": 0.9}}))
    rig.scenes.recall("heads")
    assert rig.apc.pickup(3)
    rig.press(hw.NOTE_ACTIVATOR, channel=3)  # single again: the stale fader must not jump
    assert rig.controller.state("moving-head-1")["intensity"] == 0.9


# --- Milestone 2: sequences that wait for NEXT -------------------------------------------------

from lichtwerk.sequence_service import SequenceService  # noqa: E402
from lichtwerk.sequences import SequenceContent, SequenceStep, SequenceStore  # noqa: E402


class ShowRig(LookRig):
    def __init__(self) -> None:
        super().__init__()
        self.sequence_store = SequenceStore(self.store)
        self.sequences = SequenceService(self.scenes, self.sequence_store, self.effects.tempo, self.clock)

    def look(self, name: str, level: float, fx: LookFx | None = None) -> str:
        return self.store.create(SceneContent(name=name, fixtures={"led-bar-1": {"intensity": level}}, fx=fx)).id

    def sequence(self, *steps: SequenceStep, loop: bool = False) -> str:
        return self.sequence_store.create(SequenceContent(name="Build", steps=list(steps), loop=loop)).id

    def advance(self, seconds: float) -> None:
        self.clock.now += seconds
        self.controller.tick()

    def level(self) -> float:
        return self.controller.snapshot()["led-bar-1"]["intensity"]


@pytest.fixture
def show() -> ShowRig:
    return ShowRig()


def test_wait_step_holds_until_next(show):
    build, drop = show.look("Build", 0.3), show.look("Drop", 1.0)
    sequence = show.sequence(SequenceStep(scene_id=build, fade=0.0, wait=True), SequenceStep(scene_id=drop, fade=0.0, hold=2.0))
    show.sequences.start(sequence)
    show.advance(60.0)
    status = show.sequences.status()
    assert (status["step_index"], status["waiting"], status["until_next"]) == (0, True, None)
    assert status["next_scene_name"] == "Drop"
    assert show.level() == pytest.approx(0.3)
    show.sequences.next()
    assert show.level() == pytest.approx(1.0)
    assert show.sequences.status()["step_index"] == 1 and not show.sequences.status()["waiting"]


def test_next_also_skips_a_timed_step(show):
    a, b = show.look("A", 0.2), show.look("B", 0.8)
    sequence = show.sequence(SequenceStep(scene_id=a, fade=0.0, hold=30.0), SequenceStep(scene_id=b, fade=0.0, hold=30.0))
    show.sequences.start(sequence)
    show.advance(1.0)
    assert show.sequences.status()["until_next"] == pytest.approx(29.0)
    show.sequences.next()  # the user wins (Q11)
    assert show.sequences.status()["step_index"] == 1
    assert show.level() == pytest.approx(0.8)


def test_next_after_the_last_step_completes_or_loops(show):
    a, b = show.look("A", 0.2), show.look("B", 0.8)
    one_shot = show.sequence(SequenceStep(scene_id=a, fade=0.0, wait=True), SequenceStep(scene_id=b, fade=0.0, wait=True))
    show.sequences.start(one_shot)
    show.sequences.next()
    assert show.sequences.next()["status"] == "completed"
    with pytest.raises(ValueError, match="no sequence is running"):
        show.sequences.next()
    looped = show.sequence(SequenceStep(scene_id=a, fade=0.0, wait=True), SequenceStep(scene_id=b, fade=0.0, wait=True), loop=True)
    show.sequences.start(looped)
    show.sequences.next()
    assert show.sequences.next()["step_index"] == 0


def test_next_during_a_fade_starts_the_next_step_from_the_current_level(show):
    a, b = show.look("A", 0.0), show.look("B", 1.0)
    c = show.look("C", 0.5)
    sequence = show.sequence(SequenceStep(scene_id=a, fade=0.0, hold=1.0), SequenceStep(scene_id=b, fade=10.0, wait=True),
                             SequenceStep(scene_id=c, fade=0.0, wait=True))
    show.sequences.start(sequence)
    show.advance(1.0)
    show.advance(5.0)
    assert show.level() == pytest.approx(0.5, abs=0.05)
    show.sequences.next()
    assert show.sequences.status()["step_index"] == 2
    assert show.level() == pytest.approx(0.5)


def test_next_resumes_a_paused_sequence(show):
    a, b = show.look("A", 0.2), show.look("B", 0.8)
    sequence = show.sequence(SequenceStep(scene_id=a, fade=0.0, wait=True), SequenceStep(scene_id=b, fade=0.0, wait=True))
    show.sequences.start(sequence)
    show.sequences.pause()
    status = show.sequences.next()
    assert (status["status"], status["step_index"]) == ("running", 1)


def test_sequence_step_with_a_look_switches_mode_and_starts_its_fx(show):
    show.press(hw.NOTE_ACTIVATOR, channel=6)  # spots single
    dimmer = LookStripFx(strip="led-bar-1", preset=2, effects=[definition(EffectType.CHASE, 2)])
    plain = show.look("Plain", 0.4)
    drop = show.look("Drop", 1.0, LookFx(modes={"spots": "group"}, strips=[dimmer]))
    sequence = show.sequence(SequenceStep(scene_id=plain, fade=0.0, wait=True), SequenceStep(scene_id=drop, fade=0.0, wait=True))
    show.sequences.start(sequence)
    assert show.running(BAR, EffectType.CHASE) is None
    show.press(hw.NOTE_SCENE_LAUNCH_FIRST + hw.NEXT_SCENE_LAUNCH_INDEX)  # NEXT on the APC
    assert show.apc.last_error() is None
    assert show.running(BAR, EffectType.CHASE).preset == 2
    assert show.apc.mode("spots") == "group"
    assert show.level() == pytest.approx(1.0)


def test_timed_look_step_from_the_tick_starts_fx_and_reports_blocked_parts(show):
    move = LookStripFx(strip="moving-heads", preset=0, effects=[definition(EffectType.CIRCLE)])
    dimmer = LookStripFx(strip="led-bar-1", preset=1, effects=[definition(EffectType.CHASE, 1)])
    a = show.look("A", 0.2)
    peak = show.look("Peak", 1.0, LookFx(modes={"heads": "group"}, strips=[move, dimmer]))
    sequence = show.sequence(SequenceStep(scene_id=a, fade=0.0, hold=1.0), SequenceStep(scene_id=peak, fade=0.0, wait=True))
    show.sequences.start(sequence)
    show.advance(1.5)
    assert show.sequences.status()["step_index"] == 1
    assert show.running(BAR, EffectType.CHASE).preset == 1
    assert show.running(HEADS, EffectType.CIRCLE) is None
    assert "Move FX on" in show.scenes.look_notice()
    assert show.sequences.status()["status"] == "running"  # the show never stops


def test_tick_skips_while_the_apc_is_busy_and_catches_up(show):
    from threading import Thread
    a, b = show.look("A", 0.2), show.look("B", 0.8)
    sequence = show.sequence(SequenceStep(scene_id=a, fade=0.0, hold=1.0), SequenceStep(scene_id=b, fade=0.0, wait=True))
    show.sequences.start(sequence)
    with show.apc.lock:  # another thread holds the APC: its tick must not block
        show.clock.now += 2.0
        worker = Thread(target=show.controller.tick)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert show.sequences.status()["step_index"] == 0
    show.controller.tick()
    assert show.sequences.status()["step_index"] == 1


def test_next_led_flashes_on_the_beat_while_waiting(show):
    index = hw.NEXT_SCENE_LAUNCH_INDEX
    assert show.apc.scene_launch_colours(True)[index] == 0
    show.effects.tempo.set_bpm(60.0)  # one beat per second
    a, b = show.look("A", 0.2), show.look("B", 0.8)
    show.sequences.start(show.sequence(SequenceStep(scene_id=a, fade=0.0, hold=4.0), SequenceStep(scene_id=b, fade=0.0, wait=True)))
    assert show.apc.scene_launch_colours(True)[index] == hw.NEXT_COLOURS[1]
    show.advance(5.0)
    show.clock.now += 1.1 - show.effects.tempo.beats() % 1.0  # 0.1 beat after a beat
    # Follows the beat, not the free-running blink timer.
    assert show.apc.scene_launch_colours(False)[index] == hw.NEXT_COLOURS[0]
    show.clock.now += 0.5
    assert show.apc.scene_launch_colours(True)[index] == 0
    show.sequences.stop()
    assert show.apc.scene_launch_colours(True)[index] == 0


def test_wait_steps_round_trip_in_json(show):
    a = show.look("A", 0.2)
    sequence = show.sequence(SequenceStep(scene_id=a, fade=0.0, wait=True))
    stored = show.sequence_store.get(sequence)
    assert SequenceContent.model_validate(stored.model_dump(mode="json", exclude={"id"})).steps[0].wait


# --- Milestone 3: accent pads -------------------------------------------------------------------

from lichtwerk.apc40_api import PadRequest  # noqa: E402
from lichtwerk.apc40_pads import Apc40File, PadAssignment  # noqa: E402


def accent_pad(show: ShowRig, accent: str, beats: float = 2.0, values: dict | None = None) -> int:
    scene = show.store.create(SceneContent(name="Hit", fixtures=values or {"led-bar-1": {"intensity": 1.0}})).id
    show.pads.assign(PadAssignment(pad=0, scene=scene, accent=accent, accent_beats=beats))
    return hw.pad_note(0)


def bar_output(show: ShowRig) -> float:
    return show.controller.output_state("led-bar-1")["intensity"]


def test_accent_while_held_returns_exactly_with_fx_running(show):
    show.controller.apply({"led-bar-1": {"intensity": 0.2}}, fade_seconds=0.0)
    show.start(1, EffectType.RAINBOW)  # keeps running under the accent
    note = accent_pad(show, "hold")
    before = show.controller.output_state("led-bar-1")
    show.apc.handle(MidiEvent("note", 1, note, 127))
    assert show.apc.last_error() is None
    assert bar_output(show) == 1.0
    assert show.level() == pytest.approx(0.2)  # live state untouched
    assert show.apc.pad_colours()[0] == hw.ACCENT_ACTIVE_COLOUR
    assert show.scenes.active_scene() is None  # an accent is not a recall
    show.apc.handle(MidiEvent("note", 1, note, 0))
    assert show.controller.output_state("led-bar-1") == before
    assert show.running(BAR, EffectType.RAINBOW) is not None
    assert show.apc.accents.active() == set()


def test_timed_accent_runs_its_beats_and_retriggers(show):
    show.effects.tempo.set_bpm(60.0)
    show.controller.apply({"led-bar-1": {"intensity": 0.2}}, fade_seconds=0.0)
    note = accent_pad(show, "beats", 2.0)
    show.press(note)  # press and release: a timed accent keeps running
    show.advance(1.5)
    assert bar_output(show) == 1.0
    show.press(note)  # retrigger: 2 beats from now
    show.advance(1.5)
    assert bar_output(show) == 1.0
    show.advance(0.6)
    assert bar_output(show) == pytest.approx(0.2)
    assert show.apc.accents.active() == set()


def test_accent_plays_the_full_look_and_everything_underneath_returns(show):
    show.controller.apply({"moving-head-1": {"pan": 0.3, "intensity": 0.1}}, fade_seconds=0.0)
    show.start(1, EffectType.RAINBOW)  # the bar's own FX keeps running under the accent
    chase = LookStripFx(strip="led-bar-1", preset=1, effects=[definition(EffectType.CHASE, 1)])
    scene = show.store.create(SceneContent(
        name="Hit", fixtures={"led-bar-1": {"intensity": 1.0}, "moving-head-1": {"pan": 0.9}},
        fx=LookFx(modes={"heads": "single"}, strips=[chase]),
    )).id
    show.pads.assign(PadAssignment(pad=0, scene=scene, accent="hold"))
    note = hw.pad_note(0)
    before = {f: show.controller.output_state(f) for f in ("led-bar-1", "moving-head-1")}
    show.apc.handle(MidiEvent("note", 1, note, 127))
    assert show.apc.last_error() is None and show.scenes.look_notice() is None
    assert show.controller.output_state("moving-head-1")["pan"] == pytest.approx(0.9)  # positions too
    assert show.effects.owner_running("accent:0:led-bar-1", overlay=True)  # the look's FX, on top
    # Nothing underneath changed: live values, the strip's own FX, strip modes.
    assert show.controller.state("moving-head-1")["pan"] == pytest.approx(0.3)
    assert show.running(BAR, EffectType.RAINBOW) is not None
    assert show.running(BAR, EffectType.CHASE) is None
    assert show.apc.mode("heads") == "group"
    show.apc.handle(MidiEvent("note", 1, note, 0))
    assert {f: show.controller.output_state(f) for f in before} == before
    assert not show.effects.owner_running("accent:0:led-bar-1", overlay=True)


def test_accent_fx_that_cannot_run_is_reported_and_the_rest_plays(show):
    move = LookStripFx(strip="moving-heads", preset=0, effects=[definition(EffectType.CIRCLE)])
    scene = show.store.create(SceneContent(
        name="Hit", fixtures={"led-bar-1": {"intensity": 1.0}}, fx=LookFx(strips=[move]),
    )).id
    show.pads.assign(PadAssignment(pad=0, scene=scene, accent="hold"))
    show.apc.handle(MidiEvent("note", 1, hw.pad_note(0), 127))
    assert bar_output(show) == 1.0
    notice = show.scenes.look_notice()
    assert notice and "Move FX on Heads" in notice  # uncalibrated heads: never bypassed


def test_accent_with_a_named_target_aims_without_changing_permissions(clock):
    from test_show import positioned_show
    from test_zones import HEAD
    from lichtwerk.accents import AccentPlayer

    show, rig, positions = positioned_show(clock)
    positions.aim_target(HEAD, "clear", 0)
    show.controller.apply({HEAD: {"intensity": 0.5}}, fade_seconds=0.0)
    show.controller.apply({HEAD: {"pan": 0.5, "tilt": 0.5}}, fade_seconds=0.0)
    assignment = rig.assignment(HEAD).model_copy(deep=True)
    live = show.controller.state(HEAD)
    before = show.controller.output_state(HEAD)
    scene = show.scenes.store.create(SceneContent(
        name="Spot on", fixtures={HEAD: {"intensity": 1.0}}, positions={HEAD: {"target_id": "clear"}},
    ))
    accents = AccentPlayer(show.controller, show.scenes, positions)
    pan, tilt, named = positions.scene_aims({HEAD: {"target_id": "clear"}})[HEAD]
    assert named
    assert accents.start(0, scene.id) == []
    out = show.controller.output_state(HEAD)
    assert (out["pan"], out["tilt"], out["intensity"]) == (pytest.approx(pan), pytest.approx(tilt), 1.0)
    assert show.controller.static_target_active(HEAD, out)  # same beam-guard exception as a recall
    assert rig.assignment(HEAD) == assignment and show.controller.state(HEAD) == live
    accents.release(0)
    assert show.controller.output_state(HEAD) == before


def test_reconnect_ends_a_held_accent(show):
    note = accent_pad(show, "hold")
    show.apc.handle(MidiEvent("note", 1, note, 127))
    show.apc.connected()  # the release was lost with the cable
    assert show.apc.accents.active() == set()
    assert not show.controller.holding("accent:0")


def test_accent_pads_round_trip_and_old_pad_files_still_load():
    old = Apc40File.model_validate({"pads": [{"pad": 0, "scene": "a", "colour": "blue"}]})
    assert old.pads[0].accent == "off"
    request = PadRequest.model_validate({"scene": "a", "accent": "beats", "accent_beats": 4})
    pad = PadAssignment(pad=1, scene="a", accent=request.accent, accent_beats=request.accent_beats)
    data = Apc40File(pads=[pad]).model_dump(mode="json")
    assert Apc40File.model_validate(data).pads[0] == pad
    with pytest.raises(ValueError):
        PadAssignment(pad=1, scene="a", accent="beats", accent_beats=0.0)


def test_pad_file_stores_accent_settings_only_for_accent_pads(tmp_path):
    import json
    from lichtwerk.apc40_pads import PadStore as Store
    path = tmp_path / "apc40.json"
    store = Store(path)
    store.assign(PadAssignment(pad=0, scene="a"))
    store.assign(PadAssignment(pad=1, scene="b", accent="hold"))
    store.assign(PadAssignment(pad=2, scene="c", accent="beats", accent_beats=4.0))
    pads = json.loads(path.read_text())["pads"]
    assert pads[0] == {"pad": 0, "scene": "a", "colour": "white"}
    assert pads[1]["accent"] == "hold" and "accent_beats" not in pads[1]
    assert pads[2]["accent_beats"] == 4.0
    assert Store.load(path).all() == store.all()


# --- Milestone 4: quantize ----------------------------------------------------------------------

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from lichtwerk.apc40_pads import SequencePadAssignment  # noqa: E402
from lichtwerk.show_api import build_router as show_router  # noqa: E402


def quantized(show: ShowRig, phase: float) -> None:
    """Quantize on, 60 BPM (one beat per second), clock moved to the given beat phase."""
    show.effects.tempo.set_bpm(60.0)
    show.apc.quantizer.set_enabled(True)
    show.clock.now += (phase - show.effects.tempo.beats() % 1.0) % 1.0


def look_pad(show: ShowRig, pad: int, name: str, level: float) -> int:
    show.pads.assign(PadAssignment(pad=pad, scene=show.look(name, level)))
    return hw.pad_note(pad)


def test_quantized_look_pad_waits_for_the_next_beat_and_blinks_fast(show):
    show.controller.apply({"led-bar-1": {"intensity": 0.2}}, fade_seconds=0.0)
    note = look_pad(show, 3, "Drop", 1.0)
    quantized(show, 0.5)
    show.press(note)
    assert show.level() == pytest.approx(0.2)
    assert show.apc.quantizer.queued_pads() == {3}
    assert show.apc.quantizer.state()["queued"] == ["Look Drop"]
    lit = set()
    for _ in range(2):  # fast blink: samples half a blink step apart
        show.clock.now += 0.05
        lit.add(show.apc.pad_colours()[3])
        show.clock.now += 0.05
    assert 0 in lit and len(lit) == 2
    show.advance(0.3)  # phase 0.9: still waiting
    assert show.level() == pytest.approx(0.2)
    show.advance(0.15)  # the beat
    assert show.level() == pytest.approx(1.0)
    assert show.apc.quantizer.queued() == []


def test_press_just_after_the_beat_fires_at_once_and_quantize_off_is_immediate(show):
    note = look_pad(show, 3, "Drop", 1.0)
    quantized(show, 0.05)
    show.press(note)
    assert show.level() == pytest.approx(1.0)
    show.apc.quantizer.set_enabled(False)
    show.press(look_pad(show, 4, "Low", 0.3))
    assert show.level() == pytest.approx(0.3)


def test_a_later_look_press_replaces_the_queued_one(show):
    first, second = look_pad(show, 3, "A", 0.4), look_pad(show, 4, "B", 0.9)
    quantized(show, 0.5)
    show.press(first)
    show.press(second)
    assert show.apc.quantizer.queued_pads() == {4}
    show.advance(0.6)
    assert show.level() == pytest.approx(0.9)


def test_quantized_next_and_sequence_pad_fire_on_the_beat(show):
    a, b = show.look("A", 0.2), show.look("B", 0.8)
    sequence = show.sequence(SequenceStep(scene_id=a, fade=0.0, wait=True), SequenceStep(scene_id=b, fade=0.0, wait=True))
    show.pads.assign_sequence(SequencePadAssignment(pad=0, sequence=sequence))
    show.press(hw.NOTE_DEVICE_RIGHT)  # sequence pads
    quantized(show, 0.5)
    show.press(hw.pad_note(0))
    assert show.sequences.status() is None
    show.advance(0.6)
    assert show.sequences.status()["step_index"] == 0
    show.clock.now += 0.4  # phase 0.5
    show.press(hw.NOTE_SCENE_LAUNCH_FIRST + hw.NEXT_SCENE_LAUNCH_INDEX)
    assert show.sequences.status()["step_index"] == 0
    assert show.apc.quantizer.is_queued("next")
    show.advance(0.6)
    assert show.sequences.status()["step_index"] == 1
    assert show.level() == pytest.approx(0.8)


def test_queued_while_held_accent_let_go_early_plays_one_beat(show):
    show.controller.apply({"led-bar-1": {"intensity": 0.2}}, fade_seconds=0.0)
    note = accent_pad(show, "hold")
    quantized(show, 0.5)
    show.press(note)  # press and release before the beat
    assert bar_output(show) == pytest.approx(0.2)
    show.advance(0.6)
    assert bar_output(show) == 1.0
    show.advance(0.8)
    assert bar_output(show) == 1.0
    show.advance(0.3)
    assert bar_output(show) == pytest.approx(0.2)


def test_turning_quantize_off_fires_what_is_queued(show):
    note = look_pad(show, 3, "Drop", 1.0)
    quantized(show, 0.5)
    show.press(note)
    show.press(hw.NOTE_METRONOME)  # off
    show.controller.tick()
    assert show.level() == pytest.approx(1.0)


def test_queued_action_waits_while_the_apc_is_busy(show):
    from threading import Thread
    note = look_pad(show, 3, "Drop", 1.0)
    quantized(show, 0.5)
    show.press(note)
    with show.apc.lock:
        show.clock.now += 0.6
        worker = Thread(target=show.controller.tick)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
    show.controller.tick()
    assert show.level() == pytest.approx(1.0)


def test_screen_next_and_quantize_toggle_use_the_same_queue(show):
    app = FastAPI()
    app.include_router(show_router(show.scenes, show.sequences, show.apc.quantizer))
    client = TestClient(app)
    a, b = show.look("A", 0.2), show.look("B", 0.8)
    sequence = show.sequence(SequenceStep(scene_id=a, fade=0.0, wait=True), SequenceStep(scene_id=b, fade=0.0, wait=True))
    show.sequences.start(sequence)
    show.effects.tempo.set_bpm(60.0)
    state = client.put("/api/show/quantize", json={"enabled": True}).json()
    assert state["quantize"] == {"available": True, "enabled": True, "queued": []}
    show.clock.now += (0.5 - show.effects.tempo.beats() % 1.0) % 1.0
    assert client.post(f"/api/sequences/{sequence}/next").json()["step_index"] == 0
    assert client.get("/api/show").json()["quantize"]["queued"] == ["NEXT"]
    assert show.apc.scene_launch_colours(True)[hw.NEXT_SCENE_LAUNCH_INDEX] in (0, hw.NEXT_COLOURS[0])
    show.advance(0.6)
    assert show.sequences.status()["step_index"] == 1
