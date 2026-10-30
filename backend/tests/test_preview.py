"""Milestone 13: preview mode, live holds, independent APC40 strip set points and the room map."""

import json

import pytest
from fastapi.testclient import TestClient

from lichtwerk import apc40 as hw
from lichtwerk.api import create_app
from lichtwerk.apc40_control import Apc40Control
from lichtwerk.apc40_pads import PadStore
from lichtwerk.config import AppConfig
from lichtwerk.midi_control import MidiControl, MidiEvent
from lichtwerk.midi_feedback import desired_leds
from lichtwerk.midi_mapping import MidiAction, MidiKey, MidiMappingStore
from lichtwerk.preview import PreviewService
from lichtwerk.room import RigStore
from lichtwerk.room_map import MapStore, light_colour
from lichtwerk.scenes import SceneContent, SceneStore
from lichtwerk.strobe import strobe_hz, strobe_level

from test_apc40 import Rig, club_patch


class PreviewRig(Rig):
    def __init__(self) -> None:
        super().__init__()
        self.preview = PreviewService(self.controller, self.scenes)
        self.apc = Apc40Control(
            self.controller, self.scenes, self.service, self.positions, self.pads,
            clock=self.clock, preview=self.preview,
        )

    def dmx(self, channel: int) -> int:
        return self.service.read_output(5)[channel - 1]


@pytest.fixture
def rig() -> PreviewRig:
    return PreviewRig()


# --- core preview -------------------------------------------------------------------------


def test_preview_changes_are_not_sent_until_go(rig) -> None:
    rig.controller.apply({"led-bar-1": {"intensity": 0.5, "red": 1.0}}, 0.0)
    before = rig.service.read_output(5)
    rig.preview.start()
    rig.controller.apply({"led-bar-1": {"intensity": 1.0, "red": 0.0, "blue": 1.0}}, 0.0)
    rig.controller.set_values("dj-spot-1", {"intensity": 1.0})
    assert rig.service.read_output(5) == before
    assert rig.controller.state("led-bar-1")["blue"] == 1.0  # the edited (preview) state
    assert rig.controller.live_state("led-bar-1")["blue"] == 0.0
    rig.preview.go()
    assert not rig.preview.active()
    assert rig.controller.live_state("led-bar-1")["blue"] == 1.0
    assert rig.dmx(1) == 255  # bar dimmer
    assert rig.controller.live_state("dj-spot-1")["intensity"] == 1.0


def test_go_promotes_current_fade_without_snapping_to_target(rig) -> None:
    rig.preview.start()
    rig.controller.apply({"led-bar-1": {"intensity": 1.0}}, fade_seconds=5.0)
    rig.preview.go()
    assert rig.dmx(1) == 0 and rig.controller.transition_active()


def test_preview_starts_from_current_fade_phase(rig) -> None:
    rig.controller.apply({"led-bar-1": {"intensity": 1.0}}, fade_seconds=10.0)
    rig.preview.start()
    assert rig.controller.state("led-bar-1")["intensity"] == 0.0


def test_master_blackout_and_flash_stay_live_in_preview(rig) -> None:
    rig.controller.apply({"led-bar-1": {"intensity": 0.2}}, 0.0)
    rig.preview.start()
    rig.controller.set_master(0.5)
    assert rig.dmx(1) == round(0.2 * 0.5 * 255)
    rig.service.set_blackout(True)
    assert rig.dmx(1) == 0
    rig.service.set_blackout(False)
    rig.controller.set_master(1.0)
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_RECORD_ARM, 127))  # flash LED bar
    assert rig.dmx(1) == 255
    rig.apc.handle(MidiEvent("note", 1, hw.NOTE_RECORD_ARM, 0))
    assert rig.dmx(1) == round(0.2 * 255)
    assert rig.preview.active()


def test_scene_recall_in_preview_is_shown_live_only_after_go(rig) -> None:
    rig.preview.start()
    rig.scenes.recall("red")
    assert rig.scenes.active_scene() == "red"
    assert rig.scenes.live_scene() is None
    assert rig.dmx(3) == 0  # bar red (6Ch: dimmer, strobe, red)
    rig.preview.go()
    assert rig.scenes.active_scene() == "red" == rig.scenes.live_scene()
    assert rig.dmx(3) == 255


def test_capture_in_preview_stores_the_preview_state(rig) -> None:
    rig.preview.start()
    rig.controller.apply({"led-bar-1": {"green": 1.0}}, 0.0)
    scene = rig.scenes.capture("Preview green", 0.0)
    assert scene.fixtures["led-bar-1"]["green"] == 1.0


# --- APC40 --------------------------------------------------------------------------------


def test_session_record_toggles_preview_and_lights_its_led(rig) -> None:
    rig.press(hw.NOTE_SESSION_RECORD)
    assert rig.preview.active()
    assert rig.apc.leds()[("note", 1, hw.NOTE_SESSION_RECORD)] == hw.LED_ON
    rig.fader(1, 127)
    assert rig.dmx(1) == 0
    rig.press(hw.NOTE_SESSION_RECORD)  # Go
    assert not rig.preview.active() and rig.dmx(1) == 255
    assert rig.apc.leds()[("note", 1, hw.NOTE_SESSION_RECORD)] == hw.LED_OFF


def test_group_and_single_strips_keep_their_own_set_points(rig) -> None:
    rig.fader(5, 100)  # spots group (active)
    view = {s.track: rig.apc.fader(s.track) for s in rig.apc.strips}
    assert view[5] == pytest.approx(100 / 127)
    assert view[6] is None and view[7] is None  # singles do not follow the group
    rig.fader(6, 20)  # spot 1 (inactive): stored only
    assert rig.state("dj-spot-1")["intensity"] == pytest.approx(100 / 127)
    rig.press(hw.NOTE_ACTIVATOR, channel=6)  # single mode: spot 1 applies its own set point
    assert rig.state("dj-spot-1")["intensity"] == pytest.approx(20 / 127)
    assert rig.state("dj-spot-2")["intensity"] == pytest.approx(100 / 127)  # unknown: unchanged
    rig.fader(6, 60)
    assert rig.apc.fader(5) == pytest.approx(100 / 127)  # the group does not follow a single
    rig.press(hw.NOTE_ACTIVATOR, channel=5)  # back to group: its own set point again
    assert rig.state("dj-spot-1")["intensity"] == pytest.approx(100 / 127)


def test_saturation_knob_of_inactive_strip_is_stored_and_applied_on_switch(rig) -> None:
    knob6 = hw.CC_TRACK_KNOB_FIRST + 5
    before = rig.controller.state("dj-spot-1")
    rig.cc(knob6, 0)  # spot 1 inactive (group mode)
    assert rig.controller.state("dj-spot-1") == before
    assert rig.apc.saturation(6) == 0.0
    assert rig.apc.saturation(5) is not None  # group shows the fixtures (no own set point)
    rig.press(hw.NOTE_ACTIVATOR, channel=6)
    state = rig.controller.state("dj-spot-1")
    assert state["red"] == state["green"] == state["blue"]  # saturation 0 = grey/white


def test_colour_and_strobe_set_points_follow_the_strip(rig) -> None:
    rig.press(hw.NOTE_CLIP_STOP, channel=5)  # select spots group
    rig.press(hw.pad_note(0))  # first colour pad
    hue, saturation = hw.colour_pad(0)
    assert rig.apc.settings(5).hue == hue and rig.apc.settings(5).saturation == saturation
    rig.press(hw.NOTE_SOLO, channel=5)  # strobe on for the group
    rig.press(hw.NOTE_ACTIVATOR, channel=6)  # single mode
    assert rig.apc.strobe_on(5) is True  # inactive group shows its own set point
    rig.press(hw.NOTE_SOLO, channel=6)  # spot 1 strobe off
    assert rig.state("dj-spot-1")["strobe"] == 0.0
    assert rig.state("dj-spot-2")["strobe"] > 0.0
    rig.press(hw.NOTE_ACTIVATOR, channel=5)  # group again: strobe set point applies to both
    assert rig.state("dj-spot-1")["strobe"] > 0.0


# --- Xone:K2 ------------------------------------------------------------------------------


def make_k2(rig: PreviewRig):
    mapping = MidiMappingStore(rig.patch)
    control = MidiControl(mapping, rig.controller, rig.scenes, rig.service, preview=rig.preview)
    return control, mapping


def test_k2_preview_button_toggles_and_lights(rig) -> None:
    control, mapping = make_k2(rig)
    key = MidiKey(kind="note", channel=15, number=12)
    mapping.bind(key, MidiAction(type="preview"))
    binding = mapping.bindings()[0]
    assert desired_leds([binding], None, preview=False)[(15, 12)] == "green"
    control.handle(MidiEvent("note", 15, 12, 127))
    assert rig.preview.active()
    assert desired_leds([binding], None, preview=True)[(15, 12)] == "amber"
    assert control.value(binding.action) == 1.0
    control.handle(MidiEvent("note", 15, 12, 0))
    control.handle(MidiEvent("note", 15, 12, 127))  # Go
    assert not rig.preview.active()


def test_k2_flash_is_live_in_preview(rig) -> None:
    control, mapping = make_k2(rig)
    mapping.bind(MidiKey(kind="note", channel=15, number=40), MidiAction(type="flash", target="led-bar-1"))
    rig.preview.start()
    control.handle(MidiEvent("note", 15, 40, 127))
    assert rig.dmx(1) == 255
    control.handle(MidiEvent("note", 15, 40, 0))
    assert rig.dmx(1) == 0


def test_preview_action_takes_no_target() -> None:
    with pytest.raises(ValueError):
        MidiAction(type="preview", target="led-bar-1")


# --- map ----------------------------------------------------------------------------------


def test_light_colour_adds_white_and_scales() -> None:
    assert light_colour({"intensity": 0.5, "red": 1.0, "green": 0.0, "blue": 0.0, "white": 0.5}) == (
        0.5, 0.25, 0.25,
    )
    assert light_colour({"red": 1.0, "green": 1.0, "blue": 0.0}, master=0.5) == (0.5, 0.5, 0.0)
    assert light_colour({"pan": 0.5}) is None


def test_map_store_rejects_invalid_files(tmp_path) -> None:
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"fixtures": {"led-bar-1": {"x": -1, "y": 0}}}))
    with pytest.raises(ValueError):
        MapStore.load(path)


@pytest.fixture
def client(tmp_path):
    patch = club_patch()
    store = SceneStore(patch)
    store.create(SceneContent(name="Red", fade_seconds=0.0, fixtures={"led-bar-1": {"red": 1.0}}))
    app = create_app(
        AppConfig(universes=[5]), patch=patch, scene_store=store, rig_store=RigStore(patch),
        apc40_pads=PadStore(), map_store=MapStore(tmp_path / "map.json"),
    )
    with TestClient(app) as c:
        yield c


def test_preview_api_and_status(client) -> None:
    assert client.get("/api/preview").json() == {"active": False}
    assert client.post("/api/preview", json={"active": True}).json() == {"active": True}
    assert client.get("/api/status").json()["preview_active"] is True
    client.post("/api/scenes/red/recall", json={})
    view = client.get("/api/map").json()
    bar = next(f for f in view["fixtures"] if f["id"] == "led-bar-1")
    assert bar["live"] == [0.0, 0.0, 0.0]
    assert client.post("/api/preview", json={"active": False}).json() == {"active": False}
    assert client.post("/api/preview", json={"active": "no"}).status_code == 422


def test_map_api_places_fixtures_inside_the_room(client, tmp_path) -> None:
    view = client.get("/api/map").json()
    assert view["measured"] is False and (view["width"], view["depth"]) == (5.0, 4.0)
    assert all(f["x"] is None for f in view["fixtures"])
    view = client.put("/api/map/fixtures/dj-spot-1", json={"x": 1.5, "y": 0.5}).json()
    spot = next(f for f in view["fixtures"] if f["id"] == "dj-spot-1")
    assert (spot["x"], spot["y"]) == (1.5, 0.5)
    saved = json.loads((tmp_path / "map.json").read_text())
    assert saved["fixtures"]["dj-spot-1"] == {"x": 1.5, "y": 0.5}
    assert client.put("/api/map/fixtures/dj-spot-1", json={"x": 9.0, "y": 0.5}).status_code == 422
    assert client.put("/api/map/fixtures/nope", json={"x": 1.0, "y": 1.0}).status_code == 404
    view = client.delete("/api/map/fixtures/dj-spot-1").json()
    assert next(f for f in view["fixtures"] if f["id"] == "dj-spot-1")["x"] is None


def test_map_shows_strobe_rate_for_live_and_preview(client) -> None:
    def bar() -> dict:
        return next(f for f in client.get("/api/map").json()["fixtures"] if f["id"] == "led-bar-1")

    assert bar()["live_strobe_hz"] is None
    client.post("/api/preview", json={"active": True})
    client.post("/api/fixtures/led-bar-1/state", json={"values": {"strobe": 0.5}})
    assert bar()["live_strobe_hz"] is None  # nothing sent yet
    assert bar()["preview_strobe_hz"] == pytest.approx(15.0)  # LED bar: 0-30 Hz, linear
    client.post("/api/preview", json={"active": False})
    assert bar()["live_strobe_hz"] == pytest.approx(15.0)


def test_strobe_hz_is_the_inverse_of_strobe_level() -> None:
    patch = club_patch()
    spot = patch.get("dj-spot-1")
    assert strobe_hz(spot, strobe_level(spot, 10.25)) == pytest.approx(10.25)
    assert strobe_hz(spot, 0.0) is None


def test_apc40_view_shows_each_strip_fixtures_colour(client) -> None:
    client.post("/api/fixtures/dj-spot-1/state", json={"values": {"red": 1.0, "white": 0.5}})
    strips = {s["track"]: s for s in client.get("/api/apc40").json()["strips"]}
    assert len(strips[5]["fixture_names"]) == 2
    assert strips[5]["colours"] == [[1.0, 0.5, 0.5], [0.0, 0.0, 0.0]]  # intensity ignored
    assert strips[6]["colours"] == [[1.0, 0.5, 0.5]]
    assert strips[8]["colours"] == []
