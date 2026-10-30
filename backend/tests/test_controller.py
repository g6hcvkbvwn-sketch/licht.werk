"""Xone:K2 on-screen controller, colour control and the new MIDI actions (no hardware)."""

import pytest
from fastapi.testclient import TestClient

from lichtwerk.api import create_app
from lichtwerk.colour import ColourControl
from lichtwerk.config import DEFAULT_FIXTURES_DIR, AppConfig
from lichtwerk.fixture_service import FixtureController
from lichtwerk.fixtures import load_fixture_library
from lichtwerk.midi_control import MidiControl, MidiEvent, relative_steps
from lichtwerk.midi_feedback import desired_leds
from lichtwerk.midi_mapping import MidiAction, MidiBinding, MidiKey, MidiMappingStore
from lichtwerk.output import SimulatedDmxOutput
from lichtwerk.patch import PatchedFixture, build_patch
from lichtwerk.scene_service import SceneService
from lichtwerk.scenes import SceneStore
from lichtwerk.service import LightingService
from lichtwerk.xone_k2 import CONTROLS, is_relative_cc

LIBRARY = load_fixture_library(DEFAULT_FIXTURES_DIR)


def make_patch():
    entries = [
        PatchedFixture(id="bar", name="Bar", definition="cameo-thunder-wash-100-rgb", mode="6Ch",
                       universe=5, start_address=1),
        PatchedFixture(id="spot-1", name="Spot 1", definition="cameo-q-spot-15-rgbw-bk",
                       mode="9CH", universe=5, start_address=10, groups=["spots"]),
        PatchedFixture(id="spot-2", name="Spot 2", definition="cameo-q-spot-15-rgbw-bk",
                       mode="9CH", universe=5, start_address=20, groups=["spots"]),
    ]
    return build_patch(entries, LIBRARY, [5])


def make_control():
    patch = make_patch()
    service = LightingService(SimulatedDmxOutput(), (5,))
    controller = FixtureController(patch, service, max_strobe=0.5)
    scenes = SceneService(controller, SceneStore(patch))
    mapping = MidiMappingStore(patch)
    control = MidiControl(mapping, controller, scenes, service,
                          is_relative=lambda key: key.kind == "cc" and is_relative_cc(key.number))
    return control, mapping, controller


def cc(number: int) -> MidiKey:
    return MidiKey(kind="cc", channel=15, number=number)


def note(number: int) -> MidiKey:
    return MidiKey(kind="note", channel=15, number=number)


def test_k2_layout_has_every_control_once() -> None:
    keys = [(c.kind, c.number) for c in CONTROLS]
    assert len(keys) == len(set(keys)) == 4 * 9 + 16 + 6
    assert {c.number for c in CONTROLS if c.type == "encoder"} == {0, 1, 2, 3, 20, 21}
    assert [c.number for c in CONTROLS if c.type == "fader"] == [16, 17, 18, 19]


def test_relative_steps_are_twos_complement() -> None:
    assert relative_steps(1) == 1 and relative_steps(3) == 3
    assert relative_steps(127) == -1 and relative_steps(125) == -3


def test_colour_keeps_hue_through_white_and_back() -> None:
    _, _, controller = make_control()
    colour = ColourControl(controller)
    bar = [controller.patch.get("bar")]
    colour.set(bar, hue=1 / 3, saturation=1.0)  # green
    assert controller.state("bar")["green"] == pytest.approx(1.0)
    assert controller.state("bar")["red"] == pytest.approx(0.0)
    colour.set(bar, saturation=0.0)  # white
    assert [controller.state("bar")[c] for c in ("red", "green", "blue")] == [1.0, 1.0, 1.0]
    colour.set(bar, saturation=1.0)  # hue was remembered
    assert controller.state("bar")["green"] == pytest.approx(1.0)
    assert controller.state("bar")["red"] == pytest.approx(0.0)


def test_relative_encoder_turns_hue_of_a_group() -> None:
    control, mapping, controller = make_control()
    mapping.bind(cc(3), MidiAction(type="hue", target="spots"), relative=True)
    control.handle(MidiEvent("cc", 15, 3, 1))  # dark start = red, one step clockwise
    state = controller.state("spot-1")
    assert state["red"] == pytest.approx(1.0) and 0 < state["green"] < 0.2
    assert controller.state("spot-2") == state
    control.handle(MidiEvent("cc", 15, 3, 127))  # one step back = pure red
    assert controller.state("spot-1")["green"] == pytest.approx(0.0)
    assert controller.state("bar")["red"] == 0.0  # other fixtures untouched


def test_white_and_strobe_pots_set_attributes_and_strobe_is_capped() -> None:
    control, mapping, controller = make_control()
    mapping.bind(cc(11), MidiAction(type="white", target="spots"))
    mapping.bind(cc(15), MidiAction(type="strobe", target="spots"))
    control.handle(MidiEvent("cc", 15, 11, 127))
    control.handle(MidiEvent("cc", 15, 15, 127))
    assert controller.state("spot-2")["white"] == 1.0
    assert controller.state("spot-2")["strobe"] == 1.0  # logical value; DMX is capped by max_strobe


def test_flash_and_strobe_hold_restore_on_release() -> None:
    control, mapping, controller = make_control()
    mapping.bind(note(55), MidiAction(type="flash", target="spots"))
    mapping.bind(note(43), MidiAction(type="strobe_hold", target="all"))
    controller.apply({"spot-1": {"intensity": 0.3}}, 0.0)
    control.handle(MidiEvent("note", 15, 55, 127))
    # Held values act on the output only; the logical state is never changed.
    assert controller.output_state("spot-1")["intensity"] == 1.0
    assert controller.state("spot-1")["intensity"] == 0.3
    assert control.held() == {note(55)}
    control.handle(MidiEvent("note", 15, 55, 0))
    assert controller.output_state("spot-1")["intensity"] == 0.3
    assert controller.output_state("spot-2")["intensity"] == 0.0
    control.handle(MidiEvent("note", 15, 43, 127))
    assert controller.output_state("bar")["strobe"] == 1.0
    control.handle(MidiEvent("note", 15, 43, 0))
    assert controller.output_state("bar")["strobe"] == 0.0 and control.held() == set()


def test_actions_need_matching_attributes() -> None:
    _, mapping, _ = make_control()
    with pytest.raises(ValueError):
        mapping.bind(cc(9), MidiAction(type="white", target="bar"))  # the bar has no white
    mapping.bind(cc(8), MidiAction(type="white", target="all"))  # some fixtures have white


def test_learn_marks_encoders_relative() -> None:
    control, mapping, _ = make_control()
    control.start_learn(MidiAction(type="hue", target="bar"))
    control.handle(MidiEvent("cc", 15, 0, 1))
    assert mapping.lookup_binding(cc(0)).relative is True


def test_led_colours_for_held_buttons() -> None:
    bindings = [MidiBinding(key=note(52), action=MidiAction(type="flash", target="all"))]
    assert desired_leds(bindings, None) == {(15, 52): "amber"}
    assert desired_leds(bindings, None, held={note(52)}) == {(15, 52): "red"}


def make_client() -> TestClient:
    patch = make_patch()
    mapping = MidiMappingStore(patch)
    mapping.replace([
        MidiBinding(key=cc(16), action=MidiAction(type="master")),
        MidiBinding(key=cc(18), action=MidiAction(type="intensity", target="spots")),
        MidiBinding(key=cc(2), action=MidiAction(type="hue", target="spots"), relative=True),
        MidiBinding(key=note(15), action=MidiAction(type="blackout")),
    ])
    app = create_app(AppConfig(universes=[5]), patch=patch, fixture_library=LIBRARY,
                     midi_store=mapping)
    return TestClient(app)


def test_controller_view_lists_layout_assignments_and_columns() -> None:
    with make_client() as client:
        body = client.get("/api/controller").json()
    assert body["channel"] == 15
    controls = {c["id"]: c for c in body["controls"]}
    assert len(controls) == len(CONTROLS)
    assert controls["fader-3"]["action"] == {"type": "intensity", "target": "spots"}
    assert controls["fader-3"]["target_label"] == "Spots"
    assert controls["encoder-3"]["relative"] is True
    assert body["blackout"] is True
    assert controls["exit-setup"]["led"] == "red"
    assert controls["matrix-A"]["action"] is None
    titles = [c["title"] for c in body["columns"]]
    assert titles == ["Master", "Column 2", "Spots", "Column 4"]
    assert body["columns"][2]["target"] == "spots"
    targets = {t["id"]: t["actions"] for t in body["targets"]}
    assert "white" not in targets["bar"] and "white" in targets["spots"] and "white" in targets["all"]
    actions = {a["type"]: a for a in body["actions"]}
    assert actions["hue"]["continuous"] and actions["hue"]["target"] == "fixture"
    assert actions["scene"]["target"] == "scene" and actions["blackout"]["target"] == "none"


def test_controller_events_act_like_the_hardware() -> None:
    with make_client() as client:
        assert client.post("/api/controller/event",
                           json={"kind": "cc", "number": 18, "value": 127}).status_code == 200
        fixtures = {f["id"]: f for f in client.get("/api/fixtures").json()}
        assert fixtures["spot-1"]["state"]["intensity"] == 1.0
        client.post("/api/controller/event", json={"kind": "note", "number": 15, "value": 127})
        body = client.get("/api/controller").json()
        assert body["blackout"] is False
        assert {c["id"]: c for c in body["controls"]}["exit-setup"]["led"] == "amber"
        assert client.post("/api/controller/event",
                           json={"kind": "cc", "number": 99, "value": 1}).status_code == 404
