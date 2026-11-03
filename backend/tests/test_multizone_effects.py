"""Spatial touring uses simulated fixtures only."""

import pytest

from lichtwerk.effect_engine import EffectEngine
from lichtwerk.effects import EffectType
from lichtwerk.positioning import beam_floor_point
from lichtwerk.room import HeadAssignment
from lichtwerk.tempo import TempoClock
from lichtwerk.zones import Zone
from conftest import FakeClock
from test_apc40 import club_patch
from test_zones import HEAD, make_controller, make_rig


@pytest.fixture
def tour():
    patch = club_patch()
    rig = make_rig(patch)
    document = rig.room_file()
    rig.set_room_file(document.model_copy(update={"zones": []}))
    areas = [
        Zone(id=id, name=id, surface="floor", corner={"x": x, "y": y}, length=1.0, width=1.0)
        for id, x, y in (("a", 0.5, 0.5), ("b", 4.0, 5.0), ("c", 1.0, 6.0))
    ]
    for zone in areas:
        rig.save_zone(zone)
    rig.set_assignments({HEAD: HeadAssignment(aim_zone="b", include_zones=["a", "b"])})
    clock = FakeClock()
    controller, lighting = make_controller(patch, rig, clock)
    tempo = TempoClock(clock=clock)
    tempo.set_bpm(120)
    effects = EffectEngine(patch, tempo)
    controller.set_effects(effects)
    controller.set_values(HEAD, {
        "intensity": 1.0, "red": 1.0, "ring_intensity": 1.0, "ring_strobe": 0.5,
    })
    return rig, clock, controller, effects, areas, patch, lighting


@pytest.mark.parametrize("kind", [EffectType.CIRCLE, EffectType.FIGURE8])
def test_patterns_visit_every_included_zone_and_restore_base(tour, kind):
    rig, clock, controller, effects, areas, patch, _ = tour
    effects.start("head", kind, 0, [HEAD])
    effects.set_param("head", kind, "size", 1.0)
    before = controller.state(HEAD)
    start = clock.now
    # Preset 0 is four beats: a two-second pattern, then half a second dark travel.
    for seconds, zone in ((0.2, areas[0]), (1.2, areas[0]), (2.7, areas[1]), (3.7, areas[1]), (5.2, areas[0])):
        clock.now = start + seconds
        values = controller.output_state(HEAD)
        hit = beam_floor_point(rig.head(HEAD), patch.get(HEAD).definition.motion, values["pan"], values["tilt"])
        assert hit is not None and zone.contains(hit, rig.room_file().size)
        assert values["intensity"] == 1.0
    assert controller.state(HEAD) == before
    effects.stop("head", kind)
    assert controller.output_state(HEAD)["pan"] == before["pan"]
    assert controller.state(HEAD) == before


def test_dark_transit_cannot_be_overridden_by_flash(tour):
    _, clock, controller, effects, _, _, _ = tour
    effects.start("head", EffectType.CIRCLE, 0, [HEAD])
    clock.now += 2.25
    controller.hold("flash", {HEAD: {"intensity": 1.0, "ring_intensity": 1.0, "strobe": 1.0}})
    values = controller.output_state(HEAD)
    assert values["intensity"] == values["strobe"] == 0.0
    assert values["ring_intensity"] == 1.0
    assert values["ring_strobe"] == 0.5


def test_excluded_zones_are_not_visited_and_size_zero_uses_centres(tour):
    rig, clock, controller, effects, areas, patch, _ = tour
    rig.set_assignments({HEAD: HeadAssignment(
        include_zones=["a", "b", "c"], exclude_zones=["b"],
    )})
    effects.start("head", EffectType.CIRCLE, 0, [HEAD])
    effects.set_param("head", EffectType.CIRCLE, "size", 0.0)
    clock.now += 2.7
    values = controller.output_state(HEAD)
    hit = beam_floor_point(rig.head(HEAD), patch.get(HEAD).definition.motion, values["pan"], values["tilt"])
    expected = areas[2].point_at(0.5, 0.5, rig.room_file().size)
    assert hit.x == pytest.approx(expected.x)
    assert hit.y == pytest.approx(expected.y)
    assert values["intensity"] == 1.0


def test_exclusion_overlap_still_blanks_a_touring_pattern_after_flash(tour):
    rig, _, controller, effects, areas, _, _ = tour
    rig.save_zone(areas[0].model_copy(update={"id": "blocked"}))
    rig.set_assignments({HEAD: HeadAssignment(
        include_zones=["a", "b"], exclude_zones=["blocked"],
    )})
    effects.start("head", EffectType.CIRCLE, 0, [HEAD])
    controller.hold("flash", {HEAD: {"intensity": 1.0, "ring_intensity": 1.0}})
    values = controller.output_state(HEAD)
    assert values["intensity"] == 0.0
    assert values["ring_intensity"] == 1.0
    assert values["ring_strobe"] == 0.5


def test_tempo_spread_and_transfer_boundaries_are_continuous(tour):
    rig, clock, controller, effects, areas, patch, _ = tour
    effects.start("head", EffectType.CIRCLE, 0, [HEAD, "moving-head-2"])
    rig.set_head("moving-head-2", rig.head(HEAD))
    rig.set_assignments({"moving-head-2": rig.assignment(HEAD)})
    effects.set_param("head", EffectType.CIRCLE, "spread", 1.0)
    start = clock.now
    clock.now = start + 2.7
    # Half-pattern spread keeps head 2 on area a while head 1 has reached b.
    for head, zone in ((HEAD, areas[1]), ("moving-head-2", areas[0])):
        values = controller.output_state(head)
        hit = beam_floor_point(rig.head(head), patch.get(head).definition.motion, values["pan"], values["tilt"])
        assert zone.contains(hit, rig.room_file().size)
    for boundary in (2.0, 2.5, 5.0):
        samples = []
        for offset in (-1e-6, 1e-6):
            clock.now = start + boundary + offset
            values = controller.output_state(HEAD)
            samples.append((values["pan"], values["tilt"]))
        assert samples[0] == pytest.approx(samples[1], abs=1e-5)


def test_target_aim_keeps_existing_target_centred_movement(tour):
    rig, _, controller, effects, _, _, _ = tour
    rig.set_assignments({HEAD: HeadAssignment(
        aim_target="clear", include_zones=["a", "b"],
    )})
    effects.start("head", EffectType.CIRCLE, 0, [HEAD])
    base = controller.state(HEAD)
    with_rig = effects.modulate(HEAD, base, rig=rig)
    assert with_rig == effects.modulate(HEAD, base)


def test_preview_tours_its_own_zones_until_go(tour):
    rig, clock, controller, effects, areas, patch, _ = tour
    effects.start("head", EffectType.CIRCLE, 0, [HEAD])
    controller.start_preview()
    rig.start_preview()
    effects.start_preview()
    rig.set_assignments({HEAD: HeadAssignment(include_zones=["a", "c"])})
    clock.now += 2.7
    def hit(values):
        return beam_floor_point(rig.head(HEAD), patch.get(HEAD).definition.motion, values["pan"], values["tilt"])
    assert areas[1].contains(hit(controller.output_state(HEAD)), rig.room_file().size)
    assert areas[2].contains(hit(controller.preview_output_state(HEAD)), rig.room_file().size)
    rig.go()
    effects.go()
    controller.go()
    assert areas[2].contains(hit(controller.output_state(HEAD)), rig.room_file().size)


def test_unreachable_tour_point_is_dark_and_logged(tour, caplog):
    rig, clock, controller, effects, _, _, _ = tour
    setup = rig.head(HEAD)
    rig.set_head(HEAD, setup.model_copy(update={
        "calibration": setup.calibration.model_copy(update={"pan_zero_azimuth_deg": None, "tilt_axis_deg": None}),
    }))
    effects.start("head", EffectType.CIRCLE, 0, [HEAD])
    controller.hold("flash", {HEAD: {"intensity": 1.0}})
    assert controller.output_state(HEAD)["intensity"] == 0.0
    clock.now += 0.01
    assert controller.output_state(HEAD)["intensity"] == 0.0
    assert caplog.text.count("Multi-zone movement") == 1
