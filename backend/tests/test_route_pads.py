"""APC pad picking of a running Move FX route; simulated output only."""

from conftest import FakeClock
from lichtwerk import apc40 as hw
from lichtwerk.apc40_control import Apc40Control
from lichtwerk.apc40_pads import PadStore
from lichtwerk.effect_engine import EffectEngine
from lichtwerk.effects import PRESETS, EffectType, FxDefinition
from lichtwerk.fixture_service import FixtureController
from lichtwerk.midi_control import MidiEvent
from lichtwerk.output import SimulatedDmxOutput
from lichtwerk.position_service import PositionService
from lichtwerk.scene_service import SceneService
from lichtwerk.scenes import SceneStore
from lichtwerk.service import LightingService
from lichtwerk.tempo import TempoClock
from test_apc40 import club_patch
from test_zones import HEAD, HEAD2, make_rig

OWNER = "apc40:2"


class RouteRig:
    def __init__(self, tmp_path) -> None:
        self.clock = FakeClock()
        self.patch = club_patch()
        self.rig = make_rig(self.patch, tmp_path)
        self.rig.set_head(HEAD2, self.rig.head(HEAD))
        self.service = LightingService(SimulatedDmxOutput(), (5,))
        self.controller = FixtureController(self.patch, self.service, clock=self.clock, rig=self.rig)
        self.positions = PositionService(self.controller, self.rig)
        self.tempo = TempoClock(clock=self.clock)
        self.effects = EffectEngine(self.patch, self.tempo, rig=self.rig)
        self.controller.set_effects(self.effects)
        self.apc = Apc40Control(
            self.controller, SceneService(self.controller, SceneStore(self.patch)),
            self.service, self.positions, PadStore(), clock=self.clock, effects=self.effects,
        )

    def press(self, number, channel=1):
        self.apc.handle(MidiEvent("note", channel, number, 127))
        self.apc.handle(MidiEvent("note", channel, number, 0))

    def open_positions(self) -> None:
        self.apc.handle(MidiEvent("note", 1, hw.NOTE_SHIFT, 127))
        self.press(hw.NOTE_TRACK_SELECT, channel=2)
        self.apc.handle(MidiEvent("note", 1, hw.NOTE_SHIFT, 0))
        assert self.apc.position_track() == 2

    def include_floor_area(self) -> None:
        self.press(hw.NOTE_DEVICE_LOCK)
        self.press(hw.pad_note(25))
        assert self.apc.last_error() is None
        self.press(hw.NOTE_DEVICE_ON_OFF)

    def start_move(self) -> None:
        source = PRESETS[EffectType.CIRCLE][0]
        # Size 0: the measured test targets need no included area for an unpatterned stop.
        params = source.params.model_copy(update={"size": 0.0})
        self.effects.start(
            OWNER, EffectType.CIRCLE, 0, [f.patch.id for f in self.apc.fixtures(2)],
            definition=FxDefinition(name=source.name, kind=EffectType.CIRCLE, params=params),
        )

    def route(self):
        return self.effects.running(OWNER, EffectType.CIRCLE).params


def test_target_pads_build_an_ordered_route_and_toggle_off(tmp_path):
    rig = RouteRig(tmp_path)
    rig.open_positions()
    rig.start_move()
    aim_before = rig.rig.assignment(HEAD).model_copy()
    rig.press(hw.pad_note(1))
    assert rig.apc.last_error() is None
    assert rig.route().target_ids == ["clear"]
    assert rig.route().single_point
    rig.press(hw.pad_note(0))
    assert rig.route().target_ids == ["clear", "in-zone"]
    assert rig.route().touring
    assert rig.apc.route_pads() == {1: 1, 0: 2}
    rig.press(hw.pad_note(1))
    assert rig.route().target_ids == ["in-zone"]
    rig.press(hw.pad_note(0))
    assert rig.route().stops == []
    # Picking edits only the running FX: the strip's base aim is not recalled or changed.
    assert rig.rig.assignment(HEAD) == aim_before


def test_route_continues_from_the_current_stop_when_adding_a_stop(tmp_path):
    rig = RouteRig(tmp_path)
    rig.open_positions()
    rig.start_move()
    rig.press(hw.pad_note(1))
    rig.press(hw.pad_note(0))
    effect = rig.effects.running(OWNER, EffectType.CIRCLE)
    params = effect.params
    beats_now = rig.tempo.beats(rig.clock())
    # The heads were pinned to "clear"; the tour resumes at its hold, not from the base aim.
    assert abs((beats_now - effect.launch_beat) - params.travel) < 1e-9


def test_zone_pads_need_inclusion_and_switch_route_kind(tmp_path):
    rig = RouteRig(tmp_path)
    rig.open_positions()
    rig.start_move()
    rig.press(hw.pad_note(25))
    assert "excluded" in rig.apc.last_error()
    assert rig.route().stops == []
    rig.press(hw.pad_note(0))
    assert rig.route().target_ids == ["in-zone"]
    rig.include_floor_area()
    rig.press(hw.pad_note(25))
    assert rig.apc.last_error() is None
    assert rig.route().target_ids == []
    assert rig.route().zone_ids == ["floor-area"]
    assert rig.apc.route_pads() == {25: 1}


def test_picked_pads_blink_and_permissions_mode_is_unchanged(tmp_path):
    rig = RouteRig(tmp_path)
    rig.open_positions()
    rig.start_move()
    rig.press(hw.pad_note(1))
    assert rig.apc.pad_colours(blink_on=True)[1] == 21
    assert rig.apc.pad_colours(blink_on=False)[1] == 0
    assert rig.apc.pad_colours(blink_on=False)[0] == 22
    rig.press(hw.NOTE_DEVICE_LOCK)
    assert rig.apc.route_pads() is None
    rig.press(hw.pad_note(25))  # permissions still cycle inclusion; the route is untouched
    assert "floor-area" in rig.rig.assignment(HEAD).include_zones
    assert rig.route().target_ids == ["clear"]


def test_without_move_fx_aim_pads_still_recall_the_base_aim(tmp_path):
    rig = RouteRig(tmp_path)
    rig.open_positions()
    rig.press(hw.pad_note(1))
    assert rig.apc.last_error() is None
    assert rig.rig.assignment(HEAD).aim_target == "clear"
    assert rig.apc.route_pads() is None
