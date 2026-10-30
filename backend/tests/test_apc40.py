"""AKAI APC40 MKII: protocol helpers, fixed control logic, LEDs, pad store, API (no hardware)."""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from lichtwerk import apc40 as hw
from lichtwerk.api import create_app
from lichtwerk.apc40_control import Apc40Control, strobe_level
from lichtwerk.apc40_feedback import Apc40Feedback
from lichtwerk.apc40_pads import Apc40File, PadAssignment, PadStore
from lichtwerk.effect_engine import EffectEngine
from lichtwerk.config import DEFAULT_FIXTURES_DIR, AppConfig
from lichtwerk.fixture_service import FixtureController
from lichtwerk.fixtures import ChannelDefinition, load_fixture_library
from lichtwerk.midi_adapter import MidiInput
from lichtwerk.midi_control import MidiEvent
from lichtwerk.output import SimulatedDmxOutput
from lichtwerk.patch import PatchedFixture, build_patch
from lichtwerk.position_service import PositionService
from lichtwerk.preview import PreviewService
from lichtwerk.room import RigStore
from lichtwerk.scene_service import SceneService
from lichtwerk.scenes import SceneContent, SceneStore
from lichtwerk.service import LightingService
from lichtwerk.tempo import TempoClock

from conftest import FakeClock


def club_patch():
    library = load_fixture_library(DEFAULT_FIXTURES_DIR)

    def fixture(fixture_id, definition, mode, start, groups):
        return PatchedFixture(
            id=fixture_id, name=fixture_id, definition=definition, mode=mode, universe=5,
            start_address=start, groups=groups,
        )

    entries = [
        fixture("led-bar-1", "cameo-thunder-wash-100-rgb", "6Ch", 1, []),
        fixture("moving-head-1", "equinox-fusion-orbit-mkii", "37ch", 7, ["moving-heads"]),
        fixture("moving-head-2", "equinox-fusion-orbit-mkii", "37ch", 44, ["moving-heads"]),
        fixture("dj-spot-1", "cameo-q-spot-15-rgbw-bk", "9CH", 81, ["dj-spots"]),
        fixture("dj-spot-2", "cameo-q-spot-15-rgbw-bk", "9CH", 90, ["dj-spots"]),
    ]
    return build_patch(entries, library, [5])


class Rig:
    def __init__(self, pads=None, patch=None) -> None:
        self.clock = FakeClock()
        self.patch = patch if patch is not None else club_patch()
        self.service = LightingService(SimulatedDmxOutput(), (5,))
        rig = RigStore(self.patch)
        self.controller = FixtureController(self.patch, self.service, clock=self.clock, rig=rig)
        store = SceneStore(self.patch)
        store.create(SceneContent(name="Red", fade_seconds=0.0, fixtures={"led-bar-1": {"red": 1.0}}))
        self.scenes = SceneService(self.controller, store)
        self.positions = PositionService(self.controller, rig)
        self.pads = pads or PadStore()
        self.tempo = TempoClock(clock=self.clock)
        self.effects = EffectEngine(self.patch, self.tempo)
        self.controller.set_effects(self.effects)
        self.preview = PreviewService(self.controller, self.scenes, self.effects)
        self.apc = Apc40Control(
            self.controller, self.scenes, self.service, self.positions, self.pads,
            clock=self.clock, preview=self.preview, effects=self.effects,
        )

    def state(self, fixture_id):
        return self.controller.state(fixture_id)

    def press(self, number, channel=1):
        self.apc.handle(MidiEvent("note", channel, number, 127))
        self.apc.handle(MidiEvent("note", channel, number, 0))

    def cc(self, number, value, channel=1):
        self.apc.handle(MidiEvent("cc", channel, number, value))

    def fader(self, track, value):
        self.cc(hw.CC_TRACK_FADER, value, channel=track)


@pytest.fixture
def rig() -> Rig:
    return Rig()


# --- protocol helpers ----------------------------------------------------------------------


def test_introduction_message_and_mido_framing() -> None:
    import mido

    from lichtwerk.midi_adapter import MidoOutput

    assert hw.introduction() == [0x47, 0x7F, 0x29, 0x60, 0x00, 0x04, 0x42, 0x00, 0x01, 0x00]
    with pytest.raises(ValueError):
        hw.introduction(0x10)

    class Port:
        sent = []

        def send(self, message):
            self.sent.append(message)

    port = Port()
    out = MidoOutput(port, mido)
    out.send_sysex(hw.introduction())
    out.send_cc(1, 0x10, 64)
    assert port.sent[0].bytes() == [0xF0, 0x47, 0x7F, 0x29, 0x60, 0x00, 0x04, 0x42, 0, 1, 0, 0xF7]
    assert port.sent[1].bytes() == [0xB0, 0x10, 64]


def test_pad_numbering_runs_from_top_left_and_bottom_row_sends_notes_0_to_7() -> None:
    assert hw.pad_note(0) == 32  # top-left
    assert hw.pad_note(39) == 7  # bottom-right
    assert hw.pad_note(32) == 0  # bottom-left
    assert all(hw.note_pad(hw.pad_note(p)) == p for p in range(hw.PAD_COUNT))
    assert hw.note_pad(40) is None
    with pytest.raises(ValueError):
        hw.pad_note(40)


def test_colour_pads_and_palette() -> None:
    assert len(hw.PALETTE) == 128
    assert hw.nearest_palette(1.0, 0.0, 0.0) == 5  # FF0000
    assert hw.nearest_palette(0.0, 0.0, 0.0) != 0  # black is never chosen
    assert hw.colour_pad(0) == (0.0, 1.0)  # top-left: saturated red
    hue, saturation = hw.colour_pad(8 + 5)  # second row, blue column
    assert hue == pytest.approx(240 / 360) and saturation == 0.75
    assert hw.colour_pad(hw.WHITE_PAD) is None
    assert hw.colour_pad_velocity(0) == 5
    assert hw.colour_pad_velocity(hw.WHITE_PAD) == 3


def test_strobe_rate_table_is_linear_per_fixture(rig) -> None:
    bar = rig.patch.get("led-bar-1")  # 0-30 Hz
    spot = rig.patch.get("dj-spot-1")  # 0.5-20 Hz
    head = rig.patch.get("moving-head-1")  # no Hz in the manual: fallback 1-20 Hz
    assert strobe_level(bar, 15.0) == pytest.approx(0.5)
    assert strobe_level(spot, 20.0) == 1.0
    assert strobe_level(spot, 10.25) == pytest.approx(0.5)
    assert strobe_level(head, 10.5) == pytest.approx(0.5)
    assert strobe_level(head, 0.0) > 0.0  # still "on", at the slowest rate
    assert strobe_level(bar, 99.0) == 1.0


def test_strobe_hz_range_validation() -> None:
    with pytest.raises(ValidationError, match="both"):
        ChannelDefinition(attribute="strobe", active_min=1, active_max=2, min_hz=1.0)
    with pytest.raises(ValidationError, match="below"):
        ChannelDefinition(attribute="strobe", active_min=1, active_max=2, min_hz=5.0, max_hz=5.0)
    with pytest.raises(ValidationError, match="strobe channels"):
        ChannelDefinition(attribute="red", min_hz=1.0, max_hz=5.0)


# --- channel strips ------------------------------------------------------------------------


def test_group_mode_is_default_and_single_strips_are_ignored(rig) -> None:
    rig.fader(2, 127)  # heads group
    assert rig.state("moving-head-1")["intensity"] == 1.0
    assert rig.state("moving-head-2")["intensity"] == 1.0
    rig.fader(3, 0)  # head 1 single strip: ignored in group mode, only remembered
    assert rig.state("moving-head-1")["intensity"] == 1.0
    rig.fader(1, 127)  # LED bar is always active
    assert rig.state("led-bar-1")["intensity"] == 1.0
    rig.fader(8, 127)  # free track: nothing happens


def test_switching_to_single_applies_remembered_faders_and_back(rig) -> None:
    rig.fader(5, 127)  # spots group
    rig.fader(6, 0)
    rig.fader(7, 64)
    rig.press(hw.NOTE_ACTIVATOR, channel=6)  # dark single button -> single mode
    assert rig.apc.mode("spots") == "single"
    assert rig.state("dj-spot-1")["intensity"] == 0.0
    assert rig.state("dj-spot-2")["intensity"] == pytest.approx(64 / 127)
    rig.fader(5, 10)  # group strip now ignored
    assert rig.state("dj-spot-1")["intensity"] == 0.0
    rig.press(hw.NOTE_ACTIVATOR, channel=5)  # group button -> group mode
    assert rig.state("dj-spot-1")["intensity"] == pytest.approx(10 / 127)
    assert rig.state("dj-spot-2")["intensity"] == pytest.approx(10 / 127)
    assert rig.apc.mode("heads") == "group"  # the other family is unaffected


def test_fader_report_after_connect_is_remembered_but_not_applied(rig) -> None:
    rig.apc.connected()
    rig.fader(1, 127)
    rig.cc(hw.CC_MASTER_FADER, 0)
    assert rig.state("led-bar-1")["intensity"] == 0.0
    assert rig.controller.master() == 1.0
    rig.apc.handle(MidiEvent("cc", 1, hw.CC_TRACK_FADER, 127), virtual=True)  # screen still works
    assert rig.state("led-bar-1")["intensity"] == 1.0
    rig.fader(6, 127)  # remembered during the quiet time ...
    rig.clock.now += 1.5
    rig.press(hw.NOTE_ACTIVATOR, channel=6)  # ... and applied when the strip becomes active
    assert rig.state("dj-spot-1")["intensity"] == 1.0
    rig.cc(hw.CC_MASTER_FADER, 0)
    assert rig.controller.master() == 0.0


def test_track_knob_sets_saturation_of_active_strip(rig) -> None:
    rig.cc(hw.CC_TRACK_KNOB_FIRST, 0)  # track 1: LED bar -> white
    bar = rig.state("led-bar-1")
    assert (bar["red"], bar["green"], bar["blue"]) == (1.0, 1.0, 1.0)
    rig.cc(hw.CC_TRACK_KNOB_FIRST + 2, 0)  # head 1: inactive in group mode
    assert rig.state("moving-head-1")["red"] == 0.0


def test_flash_holds_full_intensity_and_restores(rig) -> None:
    rig.fader(1, 20)
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_RECORD_ARM, 127))
    assert rig.controller.output_state("led-bar-1")["intensity"] == 1.0
    assert rig.state("led-bar-1")["intensity"] == pytest.approx(20 / 127)  # state untouched
    assert rig.service.read_output(5)[0] == 255  # bar dimmer = channel 1
    assert rig.apc.leds()[("note", 1, hw.NOTE_RECORD_ARM)] == hw.LED_ON
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_RECORD_ARM, 0))
    assert rig.controller.output_state("led-bar-1")["intensity"] == pytest.approx(20 / 127)
    assert rig.apc.leds()[("note", 1, hw.NOTE_RECORD_ARM)] == hw.LED_OFF


def test_solo_toggles_strobe_and_cue_level_changes_the_shared_rate(rig) -> None:
    rig.press(hw.NOTE_SOLO, channel=1)
    assert rig.state("led-bar-1")["strobe"] == pytest.approx(10 / 30)  # 10 Hz default
    assert rig.apc.leds()[("note", 1, hw.NOTE_SOLO)] == hw.LED_ON
    rig.press(hw.NOTE_SOLO, channel=5)  # spots group
    rig.cc(hw.CC_CUE_LEVEL, 10)  # +10 detents = +5 Hz
    assert rig.apc.strobe_hz() == 15.0
    assert rig.state("led-bar-1")["strobe"] == pytest.approx(0.5)
    assert rig.state("dj-spot-1")["strobe"] == pytest.approx((15 - 0.5) / 19.5)
    assert rig.state("moving-head-1")["strobe"] == 0.0  # not strobing: unaffected
    rig.cc(hw.CC_CUE_LEVEL, 127 - 63)  # far down: clamped to 1 Hz
    assert rig.apc.strobe_hz() == 1.0
    rig.cc(hw.CC_CUE_LEVEL, 63)
    assert rig.apc.strobe_hz() == 20.0
    rig.press(hw.NOTE_SOLO, channel=1)
    assert rig.state("led-bar-1")["strobe"] == 0.0


def test_master_button_toggles_blackout_with_led(rig) -> None:
    rig.press(hw.NOTE_MASTER)
    assert rig.service.blackout_active()
    assert rig.apc.leds()[("note", 1, hw.NOTE_MASTER)] == hw.LED_ON
    rig.press(hw.NOTE_MASTER)
    assert not rig.service.blackout_active()


# --- selection, colour pads, device knobs ----------------------------------------------------


def test_clip_stop_selects_one_channel_and_deselects(rig) -> None:
    rig.press(hw.NOTE_CLIP_STOP, channel=1)
    assert rig.apc.selected() == 1
    rig.press(hw.NOTE_CLIP_STOP, channel=5)
    assert rig.apc.selected() == 5
    leds = rig.apc.leds()
    assert leds[("note", 5, hw.NOTE_CLIP_STOP)] == hw.LED_ON
    assert leds[("note", 1, hw.NOTE_CLIP_STOP)] == hw.LED_OFF
    rig.press(hw.NOTE_CLIP_STOP, channel=5)
    assert rig.apc.selected() is None
    rig.press(hw.NOTE_CLIP_STOP, channel=3)  # head 1 is inactive in group mode
    assert rig.apc.selected() is None
    rig.press(hw.NOTE_CLIP_STOP, channel=8)  # free track
    assert rig.apc.selected() is None


def test_selection_is_dropped_when_its_strip_becomes_inactive(rig) -> None:
    rig.press(hw.NOTE_CLIP_STOP, channel=2)
    rig.press(hw.NOTE_ACTIVATOR, channel=3)
    assert rig.apc.selected() is None


def test_colour_pads_colour_the_selected_channel_but_not_head_rings(rig) -> None:
    rig.press(hw.NOTE_CLIP_STOP, channel=2)  # heads group
    rig.press(hw.pad_note(5))  # top row, blue column
    for head in ("moving-head-1", "moving-head-2"):
        state = rig.state(head)
        assert (state["red"], state["green"], state["blue"]) == pytest.approx((0.0, 0.0, 1.0))
        assert state["ring_blue"] == 0.0
    assert rig.state("led-bar-1")["blue"] == 0.0
    pads = rig.apc.pad_colours()
    assert pads[0] == 5 and pads[hw.WHITE_PAD] == 3
    assert rig.apc.leds()[("note", 1, hw.pad_note(0))] == 5


def test_white_pad_uses_w_led_where_present(rig) -> None:
    rig.press(hw.NOTE_CLIP_STOP, channel=5)
    rig.press(hw.pad_note(0))  # red first
    rig.press(hw.pad_note(hw.WHITE_PAD))
    spot = rig.state("dj-spot-1")
    assert (spot["red"], spot["green"], spot["blue"], spot["white"]) == (0.0, 0.0, 0.0, 1.0)
    rig.press(hw.pad_note(0))  # back to a colour: W off again
    assert rig.state("dj-spot-1")["white"] == 0.0
    rig.press(hw.NOTE_CLIP_STOP, channel=1)  # LED bar has no W: white = full RGB
    rig.press(hw.pad_note(hw.WHITE_PAD))
    bar = rig.state("led-bar-1")
    assert (bar["red"], bar["green"], bar["blue"]) == (1.0, 1.0, 1.0)


def test_device_knobs_follow_the_selected_fixture_kind(rig) -> None:
    knob = hw.CC_DEVICE_KNOB_FIRST
    rig.cc(knob, 127)  # nothing selected: ignored
    assert rig.state("dj-spot-1")["white"] == 0.0
    rig.press(hw.NOTE_CLIP_STOP, channel=5)  # spots: 1 white, 2 fine hue
    rig.cc(knob, 127)
    assert rig.state("dj-spot-2")["white"] == 1.0
    rig.cc(knob + 1, round(127 * 120 / 360))
    assert rig.state("dj-spot-1")["green"] == 1.0
    assert rig.apc.leds()[("cc", 1, hw.CC_DEVICE_RING_TYPE_FIRST)] == hw.RING_VOLUME
    assert rig.apc.leds()[("cc", 1, hw.CC_DEVICE_KNOB_FIRST)] == 127
    assert rig.apc.leds()[("cc", 1, hw.CC_DEVICE_RING_TYPE_FIRST + 2)] == hw.RING_OFF

    rig.press(hw.NOTE_CLIP_STOP, channel=2)  # left columns: 1 pan, 2 tilt, 5 speed, 6 white
    rig.cc(knob, 64)
    rig.cc(knob + 1, 32)
    rig.cc(knob + 4, 127)
    rig.cc(knob + 5, 127)
    head = rig.state("moving-head-2")
    assert head["pan"] == pytest.approx(64 / 127)
    assert head["tilt"] == pytest.approx(32 / 127)
    assert head["speed"] == 1.0 and head["white"] == 1.0


def test_pan_knob_moves_spot_inside_include_zone(rig, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(rig.positions, "has_include_zone", lambda head: head == "moving-head-1")
    monkeypatch.setattr(
        rig.positions, "set_spot", lambda head, u, v, fade_seconds: calls.append((head, u, v))
    )
    rig.press(hw.NOTE_CLIP_STOP, channel=2)
    rig.cc(hw.CC_DEVICE_KNOB_FIRST, 127)
    assert calls == [("moving-head-1", 1.0, 0.5)]  # zone head: spot; tilt stays in the middle
    assert rig.state("moving-head-2")["pan"] == 1.0  # head without a zone: raw pan


# --- scene pads ----------------------------------------------------------------------------


def test_scene_pads_recall_again_and_blink(rig) -> None:
    rig.pads.assign(PadAssignment(pad=0, scene="red", colour="orange"))
    rig.pads.assign(PadAssignment(pad=1, scene="gone", colour="blue"))
    assert rig.apc.pad_colours() == {**{p: 0 for p in range(40)}, 0: 9}  # missing scene: dark
    rig.press(hw.pad_note(0))
    assert rig.scenes.active_scene() == "red"
    assert rig.apc.pad_colours(blink_on=True)[0] == 9
    assert rig.apc.pad_colours(blink_on=False)[0] == 0
    rig.clock.now = 0.6  # second half of a blink period
    assert rig.apc.leds()[("note", 1, hw.pad_note(0))] == 0
    rig.press(hw.pad_note(0))
    assert rig.scenes.active_scene() == "red"
    rig.press(hw.pad_note(1))  # missing scene: logged, nothing happens
    rig.press(hw.pad_note(2))  # unassigned
    assert rig.scenes.active_scene() == "red"
    assert rig.apc.show_dialog_request() == (1, "scene", None, 2)


def test_pad_store_validates_and_saves(tmp_path) -> None:
    path = tmp_path / "apc40.json"
    store = PadStore.load(path)
    store.assign(PadAssignment(pad=3, scene="red", colour="green"))
    assert json.loads(path.read_text())["pads"] == [{"pad": 3, "scene": "red", "colour": "green"}]
    assert PadStore.load(path).get(3).colour == "green"
    store.clear(3)
    assert PadStore.load(path).all() == []
    with pytest.raises(ValidationError, match="Unknown pad colour"):
        PadAssignment(pad=0, scene="red", colour="beige")
    with pytest.raises(ValidationError, match="twice"):
        Apc40File(pads=[PadAssignment(pad=0, scene="a"), PadAssignment(pad=0, scene="b")])
    path.write_text('{"pads": [{"pad": 50, "scene": "x"}]}')
    with pytest.raises(ValueError, match="apc40.json"):
        PadStore.load(path)


# --- LEDs and connection ---------------------------------------------------------------------


def test_leds_show_group_single_mode(rig) -> None:
    leds = rig.apc.leds()
    lit = [t for t in range(1, 9) if leds[("note", t, hw.NOTE_ACTIVATOR)] == hw.LED_ON]
    assert lit == [1, 2, 5]
    rig.press(hw.NOTE_ACTIVATOR, channel=4)
    leds = rig.apc.leds()
    lit = [t for t in range(1, 9) if leds[("note", t, hw.NOTE_ACTIVATOR)] == hw.LED_ON]
    assert lit == [1, 3, 4, 5]
    assert leds[("cc", 1, hw.CC_TRACK_RING_TYPE_FIRST + 7)] == hw.RING_OFF  # track 8


def test_feedback_sends_only_changes_and_everything_after_invalidate() -> None:
    state = {("note", 1, 80): 0, ("cc", 1, 16): 5}
    sent = []
    connection = [1]
    feedback = Apc40Feedback(
        lambda: dict(state),
        lambda c, n, v: sent.append(("note", c, n, v)) or True,
        lambda c, n, v: sent.append(("cc", c, n, v)) or True,
        lambda: connection[0],
    )
    feedback.update()
    assert sorted(sent) == [("cc", 1, 16, 5), ("note", 1, 80, 0)]
    sent.clear()
    feedback.update()
    assert sent == []
    state[("note", 1, 80)] = 1
    feedback.update()
    assert sent == [("note", 1, 80, 1)]
    sent.clear()
    feedback.invalidate()
    feedback.update()
    assert len(sent) == 2
    sent.clear()
    connection[0] = 2
    feedback.update()
    assert len(sent) == 2


class FakeOut:
    def __init__(self) -> None:
        self.sysex = []

    def send_note(self, channel, note, velocity) -> None:
        pass

    def send_cc(self, channel, control, value) -> None:
        pass

    def send_sysex(self, data) -> None:
        self.sysex.append(data)

    def close(self) -> None:
        pass


class FakePorts:
    def __init__(self) -> None:
        self.out = FakeOut()
        self.devices = ["APC40 mkII"]

    def names(self):
        return list(self.devices)

    def output_names(self):
        return list(self.devices)

    def open_output(self, name):
        return self.out

    def open(self, name, callback):
        class Port:
            def close(self):
                pass

        return Port()


def test_input_runs_setup_on_every_connect() -> None:
    ports = FakePorts()
    connects = []
    midi = MidiInput(ports, object(), "apc40 mkii", on_connect=lambda: connects.append(1))
    midi._scan()  # noqa: SLF001 - one supervision round without threads
    assert connects == [1]
    assert midi.send_sysex(hw.introduction())
    assert ports.out.sysex == [hw.introduction()]
    midi._scan()  # still connected: no new setup
    assert connects == [1]
    ports.devices = []
    midi._scan()
    ports.devices = ["APC40 mkII"]
    midi._scan()
    assert connects == [1, 1]


# --- API -----------------------------------------------------------------------------------


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


def test_api_view_events_and_pad_assignment(client, tmp_path) -> None:
    view = client.get("/api/apc40").json()
    assert view["connected"] is False
    assert [s["label"] for s in view["strips"]][:2] == ["led-bar-1", "Moving Heads"]
    assert [s["active"] for s in view["strips"]] == [True, True, False, False, True, False, False, False]
    assert view["pad_mode"] == "scenes" and len(view["pads"]) == 40
    assert view["strobe_hz"] == 10.0
    assert view["position_dialog_id"] == 0
    assert view["position_dialog_kind"] is None

    view = client.put("/api/apc40/pads/0", json={"scene": "red", "colour": "green"}).json()
    assert view["pads"][0]["label"] == "Red" and view["pads"][0]["colour"] == "00FF00"
    assert json.loads((tmp_path / "apc40.json").read_text())["pads"][0]["scene"] == "red"
    assert client.put("/api/apc40/pads/0", json={"scene": "nope"}).status_code == 404
    assert client.put("/api/apc40/pads/0", json={"scene": "red", "colour": "beige"}).status_code == 422
    assert client.put("/api/apc40/pads/40", json={"scene": "red"}).status_code == 404

    event = {"kind": "note", "channel": 1, "number": hw.pad_note(0), "value": 127}
    client.post("/api/apc40/event", json=event)
    assert client.get("/api/status").json()["active_scene"] == "red"

    view = client.post(
        "/api/apc40/event", json={"kind": "note", "channel": 2, "number": hw.NOTE_CLIP_STOP, "value": 127}
    ).json()
    assert view["selected"] == 2 and view["pad_mode"] == "colours"
    assert [k["label"] for k in view["knobs"][:4]] == ["Pan", "Tilt", "Speed", "White"]
    assert view["pads"][hw.WHITE_PAD]["label"] == "White"

    view = client.delete("/api/apc40/pads/0").json()
    assert client.get("/api/apc40").json()["pads"][0]["scene"] is None
