import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from lichtwerk.api import create_app
from lichtwerk.config import DEFAULT_FIXTURES_DIR, AppConfig, MidiConfig
from lichtwerk.fixture_service import FixtureController
from lichtwerk.fixtures import load_fixture_library
from lichtwerk.midi_adapter import MidiInput, to_event
from lichtwerk.midi_feedback import LedFeedback, desired_leds
from lichtwerk.xone_k2 import led_note
from lichtwerk.midi_control import MidiControl, MidiEvent
from lichtwerk.midi_mapping import (
    MidiAction,
    MidiBinding,
    MidiKey,
    MidiMappingStore,
)
from lichtwerk.patch import PatchedFixture, build_patch
from lichtwerk.scene_service import SceneService
from lichtwerk.scenes import SceneContent, SceneStore
from lichtwerk.service import LightingService
from lichtwerk.output import SimulatedDmxOutput

LIBRARY = load_fixture_library(DEFAULT_FIXTURES_DIR)


def make_patch():
    entry = PatchedFixture(
        id="bar", name="Bar", definition="cameo-thunder-wash-100-rgb", mode="6Ch",
        universe=5, start_address=1, groups=["bars"],
    )
    return build_patch([entry], LIBRARY, [5])


def make_control(path=None):
    patch = make_patch()
    service = LightingService(SimulatedDmxOutput(), (5,))
    controller = FixtureController(patch, service)
    store = SceneStore(patch)
    store.create(SceneContent(name="Red", fade_seconds=0.0, fixtures={"bar": {"red": 1.0}}))
    scenes = SceneService(controller, store)
    mapping = MidiMappingStore(patch, path)
    return MidiControl(mapping, controller, scenes, service), mapping, controller, service, scenes


FADER = MidiKey(kind="cc", channel=15, number=16)
BUTTON = MidiKey(kind="note", channel=15, number=36)
BINDING_RED_SCENE = MidiBinding(key=BUTTON, action=MidiAction(type="scene", target="red"))


def test_binding_kind_must_match_action() -> None:
    with pytest.raises(ValidationError):
        MidiBinding(key=BUTTON, action=MidiAction(type="master"))
    with pytest.raises(ValidationError):
        MidiBinding(key=FADER, action=MidiAction(type="blackout"))
    with pytest.raises(ValidationError):
        MidiAction(type="scene")
    with pytest.raises(ValidationError):
        MidiAction(type="master", target="x")


def test_duplicate_controls_or_actions_are_rejected() -> None:
    _, mapping, *_ = make_control()
    action = MidiAction(type="master")
    with pytest.raises(ValidationError):
        mapping.replace([MidiBinding(key=FADER, action=action),
                         MidiBinding(key=FADER, action=MidiAction(type="intensity", target="bar"))])
    with pytest.raises(ValueError, match="not a fixture or group"):
        mapping.replace([MidiBinding(key=FADER, action=MidiAction(type="intensity", target="nope"))])


def test_fader_sets_master_and_fixture_or_group_intensity() -> None:
    control, mapping, controller, *_ = make_control()
    mapping.replace([
        MidiBinding(key=FADER, action=MidiAction(type="master")),
        MidiBinding(key=MidiKey(kind="cc", channel=15, number=17),
                    action=MidiAction(type="intensity", target="bar")),
        MidiBinding(key=MidiKey(kind="cc", channel=15, number=18),
                    action=MidiAction(type="intensity", target="bars")),
    ])
    control.handle(MidiEvent("cc", 15, 16, 127))
    control.handle(MidiEvent("cc", 15, 17, 127))
    assert controller.master() == 1.0 and controller.state("bar")["intensity"] == 1.0
    control.handle(MidiEvent("cc", 15, 16, 0))
    control.handle(MidiEvent("cc", 15, 18, 0))
    assert controller.master() == 0.0 and controller.state("bar")["intensity"] == 0.0


def test_buttons_recall_scene_and_toggle_blackout_on_press_only() -> None:
    control, mapping, controller, service, scenes = make_control()
    mapping.replace([
        MidiBinding(key=BUTTON, action=MidiAction(type="scene", target="red")),
        MidiBinding(key=MidiKey(kind="note", channel=15, number=37),
                    action=MidiAction(type="blackout")),
    ])
    control.handle(MidiEvent("note", 15, 36, 100))
    assert controller.state("bar")["red"] == 1.0 and scenes.active_scene() == "red"
    control.handle(MidiEvent("note", 15, 37, 100))
    control.handle(MidiEvent("note", 15, 37, 0))  # release does nothing
    assert service.blackout_active()
    control.handle(MidiEvent("note", 15, 37, 100))
    assert not service.blackout_active()


def test_unmapped_wrong_channel_and_bad_targets_never_raise() -> None:
    control, mapping, controller, *_ = make_control()
    mapping.replace([MidiBinding(key=BUTTON, action=MidiAction(type="scene", target="missing"))])
    control.handle(MidiEvent("cc", 3, 99, 50))
    control.handle(MidiEvent("note", 1, 36, 100))  # same note, other channel: not bound
    control.handle(MidiEvent("note", 15, 36, 100))  # scene does not exist: logged only
    assert len(control.monitor()) == 3 and controller.master() == 1.0


def test_learn_binds_next_matching_control_and_persists(tmp_path) -> None:
    path = tmp_path / "midi.json"
    control, mapping, controller, *_ = make_control(path)
    control.start_learn(MidiAction(type="master"))
    control.handle(MidiEvent("note", 15, 36, 100))  # a button is the wrong kind: keep waiting
    assert control.learning() is not None
    control.handle(MidiEvent("cc", 15, 16, 64))
    assert control.learning() is None
    assert mapping.lookup(FADER) == MidiAction(type="master")
    reloaded = MidiMappingStore.load(path, make_patch())
    assert reloaded.lookup(FADER) == MidiAction(type="master")


def test_learn_replaces_previous_binding_of_the_same_action() -> None:
    control, mapping, *_ = make_control()
    for number in (16, 17):
        control.start_learn(MidiAction(type="master"))
        control.handle(MidiEvent("cc", 15, number, 1))
    assert [b.key.number for b in mapping.bindings()] == [17]


def test_mapping_file_with_unknown_target_is_reported(tmp_path) -> None:
    path = tmp_path / "midi.json"
    path.write_text('{"bindings":[{"key":{"kind":"cc","channel":15,"number":1},'
                    '"action":{"type":"intensity","target":"ghost"}}]}')
    with pytest.raises(ValueError, match="ghost"):
        MidiMappingStore.load(path, make_patch())


def test_to_event_translates_only_note_and_cc() -> None:
    mido = pytest.importorskip("mido")
    assert to_event(mido.Message("control_change", channel=14, control=16, value=5)) == MidiEvent("cc", 15, 16, 5)
    assert to_event(mido.Message("note_on", channel=14, note=36, velocity=90)) == MidiEvent("note", 15, 36, 90)
    assert to_event(mido.Message("note_off", channel=14, note=36)) == MidiEvent("note", 15, 36, 0)
    assert to_event(mido.Message("pitchwheel", channel=0, pitch=0)) is None
    assert to_event(mido.Message("clock")) is None


class FakePort:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeOutput:
    def __init__(self) -> None:
        self.sent: list[tuple[int, int, int]] = []

    def send_note(self, channel, note, velocity) -> None:
        self.sent.append((channel, note, velocity))

    def close(self) -> None:
        pass


class FakePorts:
    def __init__(self) -> None:
        self.output = FakeOutput()
        self.devices: list[str] = []
        self.callback = None
        self.fail_open = False
        self.opened: list[FakePort] = []

    def names(self) -> list[str]:
        return list(self.devices)

    def output_names(self) -> list[str]:
        return list(self.devices)

    def open_output(self, name):
        return self.output

    def open(self, name, callback):
        if self.fail_open:
            raise OSError("busy")
        self.callback = callback
        port = FakePort()
        self.opened.append(port)
        return port


def wait_for(condition, timeout=3.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def test_input_connects_reconnects_and_survives_unplugging() -> None:
    control, mapping, controller, *_ = make_control()
    mapping.replace([MidiBinding(key=FADER, action=MidiAction(type="master"))])
    ports = FakePorts()
    midi = MidiInput(ports, control, "xone:k2", rescan_seconds=0.02)
    midi.start()
    try:
        assert not midi.status()["connected"]
        ports.devices = ["Other", "XONE:K2 MIDI"]
        wait_for(lambda: midi.status()["connected"])
        assert midi.status()["connected_device"] == "XONE:K2 MIDI"
        ports.callback(MidiEvent("cc", 15, 16, 0))
        wait_for(lambda: controller.master() == 0.0)

        ports.devices = []  # unplugged
        wait_for(lambda: not midi.status()["connected"])
        assert ports.opened[0].closed
        ports.devices = ["XONE:K2 MIDI"]  # plugged in again
        wait_for(lambda: midi.status()["connected"])
        assert len(ports.opened) == 2
    finally:
        midi.stop()


def test_input_reports_open_failure_and_keeps_running() -> None:
    control, *_ = make_control()
    ports = FakePorts()
    ports.devices = ["XONE:K2"]
    ports.fail_open = True
    midi = MidiInput(ports, control, "xone", rescan_seconds=0.02)
    midi.start()
    try:
        wait_for(lambda: midi.status()["last_error"] is not None)
        assert "busy" in midi.status()["last_error"] and not midi.status()["connected"]
        ports.fail_open = False
        wait_for(lambda: midi.status()["connected"])
    finally:
        midi.stop()


def test_midi_api_status_learn_and_bindings() -> None:
    patch = make_patch()
    ports = FakePorts()
    app = create_app(
        AppConfig(universes=[5], midi=MidiConfig(enabled=True)),
        patch=patch, fixture_library=LIBRARY, midi_ports=ports,
    )
    with TestClient(app) as client:
        body = client.get("/api/midi").json()
        assert body["enabled"] and body["bindings"] == [] and not body["connected"]

        assert client.post("/api/midi/learn", json={"type": "master"}).json()["learning"] == {
            "type": "master", "target": None}
        assert client.delete("/api/midi/learn").json()["learning"] is None
        assert client.post("/api/midi/learn", json={"type": "scene"}).status_code == 422

        bad = client.put("/api/midi/bindings", json={"bindings": [
            {"key": {"kind": "note", "channel": 15, "number": 1}, "action": {"type": "master"}}]})
        assert bad.status_code == 422


def test_midi_is_disabled_by_default() -> None:
    app = create_app(AppConfig(universes=[5]), patch=make_patch(), fixture_library=LIBRARY)
    with TestClient(app) as client:
        body = client.get("/api/midi").json()
        assert body["enabled"] is False
        assert client.post("/api/midi/learn", json={"type": "master"}).status_code == 409


def test_pressing_the_active_scene_button_again_recalls_without_deactivation() -> None:
    control, mapping, controller, _, scenes = make_control()
    mapping.replace([MidiBinding(key=BUTTON, action=MidiAction(type="scene", target="red"))])
    controller.set_values("bar", {"intensity": 1.0})
    control.handle(MidiEvent("note", 15, 36, 100))
    assert scenes.active_scene() == "red"
    control.handle(MidiEvent("note", 15, 36, 100))
    assert scenes.active_scene() == "red"
    assert controller.state("bar")["intensity"] == 1.0
    assert scenes.store.get("red").fixtures == {"bar": {"red": 1.0}}  # stored scene untouched
    control.handle(MidiEvent("note", 15, 36, 100))
    assert scenes.active_scene() == "red"


def test_monitor_keeps_latest_value_per_control() -> None:
    control, *_ = make_control()
    for value in range(100):
        control.handle(MidiEvent("cc", 15, 16, value))
    control.handle(MidiEvent("cc", 15, 17, 5))
    monitor = control.monitor()
    assert [(m["number"], m["value"]) for m in monitor] == [(17, 5), (16, 99)]


def test_led_notes_follow_the_k2_manual() -> None:
    assert [led_note(36, c) for c in ("red", "amber", "green")] == [(36, 127), (72, 127), (108, 127)]
    assert led_note(24, "amber") == (60, 127)  # C1 -> C4
    assert led_note(55, "green") == (127, 127)  # top encoder push window
    assert led_note(15, "red") == (15, 127)  # EXIT SETUP
    assert led_note(12, "green") == (20, 127)  # LAYER
    assert led_note(36, "off") == (36, 0)
    assert led_note(13, "red") is None  # bottom encoder push has no LED


def test_led_state_is_amber_for_scenes_and_green_for_the_active_one() -> None:
    _, mapping, *_ = make_control()
    mapping.replace([
        MidiBinding(key=BUTTON, action=MidiAction(type="scene", target="red")),
        MidiBinding(key=MidiKey(kind="note", channel=15, number=15),
                    action=MidiAction(type="blackout")),
    ])
    assert desired_leds(mapping.bindings(), None) == {(15, 36): "amber", (15, 15): "amber"}
    assert desired_leds(mapping.bindings(), "red", blackout=True) == {
        (15, 36): "green", (15, 15): "red",
    }


def test_led_feedback_sends_one_note_per_change_and_never_other_colour_offs() -> None:
    control, mapping, _, service, scenes = make_control()
    mapping.replace([BINDING_RED_SCENE])
    sent: list[tuple[int, int, int]] = []
    connection = [1]
    leds = LedFeedback(mapping, scenes, service, control.held,
                       lambda c, n, v: sent.append((c, n, v)) or True, lambda: connection[0])
    leds.update()
    assert sent == [(15, 72, 127)]  # amber (C5)
    leds.update()
    assert sent == [(15, 72, 127)]  # nothing changed
    control.handle(MidiEvent("note", 15, 36, 100))  # activate
    leds.update()
    assert sent[1:] == [(15, 108, 127)]  # green (C8) only, no "amber off"
    control.handle(MidiEvent("note", 15, 36, 100))  # recall again
    leds.update()
    assert sent[2:] == []  # repeated recall stays green
    connection[0] = 2
    leds.update()
    assert sent[2:] == [(15, 108, 127)]  # sent again after reconnect
    mapping.replace([])
    leds.update()
    assert sent[3:] == [(15, 36, 0)]  # unbound button goes dark


def test_input_sends_led_messages_through_the_output_port() -> None:
    control, *_ = make_control()
    ports = FakePorts()
    ports.devices = ["XONE:K2"]
    midi = MidiInput(ports, control, "xone", rescan_seconds=0.02)
    assert midi.send_note(15, 72, 127) is False  # not connected
    midi.start()
    try:
        wait_for(lambda: midi.status()["connected"])
        assert midi.send_note(15, 72, 127) is True
        assert ports.output.sent == [(15, 72, 127)]
    finally:
        midi.stop()
