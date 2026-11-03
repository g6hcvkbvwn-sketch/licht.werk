"""Approved FX-family contracts and pure runtime behavior."""

import math

import pytest
from pydantic import ValidationError

from lichtwerk.effect_engine import EffectEngine
from lichtwerk.effects import (
    EFFECT_LABELS,
    EFFECT_TYPES,
    PRESETS,
    EffectParams,
    EffectType,
    FxDefinition,
    family,
    factory_choices,
    family_presets,
    modulate,
    seconds_from_level,
    seconds_level,
    shape_value,
)
from lichtwerk.room import HeadAssignment
from lichtwerk.tempo import TempoClock
from test_apc40 import club_patch
from test_zones import HEAD, INCLUDE, make_rig
from conftest import FakeClock


def params(**updates) -> EffectParams:
    return EffectParams.model_validate({
        "beats": 4.0, "size": 1.0, "spread": 0.0, "softness": 0.0, **updates
    })


def test_family_pages_preserve_legacy_types_and_factory_presets():
    assert tuple(EffectType) == (
        EffectType.CHASE, EffectType.RAINBOW, EffectType.TWO_COLOUR,
        EffectType.CIRCLE, EffectType.FIGURE8,
    )
    assert EFFECT_TYPES == (EffectType.CHASE, EffectType.RAINBOW, EffectType.CIRCLE)
    assert list(EFFECT_LABELS.values()) == ["Dimmer", "Colour", "Move"]
    assert family(EffectType.TWO_COLOUR) is EffectType.RAINBOW
    assert family(EffectType.FIGURE8) is EffectType.CIRCLE
    assert sum(map(len, PRESETS.values())) == 25
    choices = factory_choices()
    assert len(choices) == 25
    assert all(isinstance(item, FxDefinition) and " · " in item.name for item in choices)
    assert [item.name for item in family_presets(EffectType.CHASE)] == [
        "Pulse", "Chase", "Wave", "Fast chase", "Breathe",
    ]
    assert [item.name for item in family_presets(EffectType.RAINBOW)] == [
        "Slow rainbow", "Rainbow spread", "Opposite", "Fade", "Neighbour",
    ]
    assert [(item.kind, item.name) for item in family_presets(EffectType.CIRCLE)] == [
        (EffectType.CIRCLE, "Small circle"), (EffectType.CIRCLE, "Medium circle"),
        (EffectType.FIGURE8, "Medium figure 8"), (EffectType.CIRCLE, "Mirror circle"),
        (EffectType.FIGURE8, "Big slow figure 8"),
    ]


@pytest.mark.parametrize("values", [
    {"seconds": float("inf")}, {"travel": float("nan")},
    {"order": ["head-1", "head-1"]},
    {"palette": ["#0000ff"]}, {"palette": ["blue", "#ffffff"]},
    {"target_ids": ["one", "one"]},
])
def test_effect_params_reject_nonfinite_duplicate_or_bad_lists(values):
    with pytest.raises(ValidationError):
        params(**values)


def test_named_shapes_and_legacy_chase_range():
    assert shape_value(0.0, "sine") == pytest.approx(1.0)
    assert shape_value(0.25, "sine") == pytest.approx(0.5)
    assert shape_value(0.5, "triangle") == pytest.approx(0.0)
    assert shape_value(0.5, "saw_up") == pytest.approx(0.5)
    assert shape_value(0.0, "saw_down") == pytest.approx(1.0)
    base = {"intensity": 0.8}
    for phase in (0.0, 0.2, 0.5, 0.9):
        out = modulate(
            EffectType.CHASE, params(shape="sine", maximum=0.7, size=0.5),
            phase, base,
        )
        assert 0.8 * 0.35 - 1e-9 <= out["intensity"] <= 0.8 * 0.7 + 1e-9
    legacy = modulate(
        EffectType.CHASE,
        EffectParams(beats=1, size=1, spread=0, softness=0),
        0.5,
        base,
    )
    assert legacy["intensity"] == 0.0 and base["intensity"] == 0.8


def test_fixed_palette_is_independent_of_base_rgb_and_preserves_intensity():
    state = {"red": 0.0, "green": 1.0, "blue": 0.0, "white": 0.8, "intensity": 0.4}
    fixed = params(colour_mode="palette", palette=["#ff0000", "#0000ff"])
    red = modulate(EffectType.TWO_COLOUR, fixed, 0.0, state)
    assert (red["red"], red["green"], red["blue"]) == pytest.approx((1, 0, 0))
    assert red["white"] == 0.0 and red["intensity"] == 0.4
    midpoint = modulate(EffectType.RAINBOW, fixed, 0.25, state)
    assert (midpoint["red"], midpoint["green"], midpoint["blue"]) == pytest.approx((0.5, 0, 0.5))
    stepped = modulate(
        EffectType.RAINBOW,
        params(colour_mode="palette", palette=["#ff0000", "#0000ff"], transition="step"),
        0.49, state,
    )
    assert (stepped["red"], stepped["green"], stepped["blue"]) == pytest.approx((1, 0, 0))


def test_seconds_speed_curve_is_logarithmic():
    assert seconds_from_level(0.0) == pytest.approx(120.0)
    assert seconds_from_level(1.0) == pytest.approx(0.25)
    assert seconds_level(seconds_from_level(0.37)) == pytest.approx(0.37)
    assert seconds_from_level(0.5) == pytest.approx(math.sqrt(30.0))


def test_family_replacement_is_whole_instance_but_inactive_owner_is_remembered():
    patch = club_patch()
    clock = FakeClock()
    engine = EffectEngine(patch, TempoClock(clock=clock))
    engine.start("group", EffectType.RAINBOW, 0, ["moving-head-1", "moving-head-2"])
    engine.start("other", EffectType.TWO_COLOUR, 0, ["moving-head-1"])
    assert engine.running("group", EffectType.RAINBOW) is None
    assert engine.running("other", EffectType.RAINBOW).kind is EffectType.TWO_COLOUR
    assert "group" in engine.notice and "moving-head-1" in engine.notice
    engine.start("dimmer", EffectType.CHASE, 0, ["moving-head-1"])
    engine.start("movement", EffectType.CIRCLE, 0, ["moving-head-1"])
    assert len(engine.all_running()) == 3
    assert engine.running("other", EffectType.TWO_COLOUR) == engine.running("other", EffectType.RAINBOW)

    engine.start("remembered", EffectType.CIRCLE, 0, ["moving-head-1"])
    engine.set_active("remembered", False)
    engine.start("new", EffectType.FIGURE8, 0, ["moving-head-1"])
    assert engine.running("remembered", EffectType.CIRCLE) is not None
    assert not engine.running("remembered", EffectType.CIRCLE).active
    engine.set_active("remembered", True)
    assert engine.running("new", EffectType.CIRCLE) is None
    assert engine.running("remembered", EffectType.FIGURE8).kind is EffectType.CIRCLE


def test_selection_order_and_snapshot_contract():
    patch = club_patch()
    engine = EffectEngine(patch, TempoClock(clock=FakeClock()))
    definition = FxDefinition(
        name="Odd roster", kind=EffectType.CHASE,
        params=params(order=["dj-spot-2", "led-bar-1"], selection="odd"),
    )
    engine.start(
        "owner", EffectType.CHASE, 0,
        ["led-bar-1", "dj-spot-1", "dj-spot-2"], definition=definition,
    )
    record = engine.running("owner", EffectType.CHASE)
    assert record.fixtures == ("dj-spot-2", "dj-spot-1")
    assert engine.all_running() == [("owner", EffectType.CHASE, record)]
    snapshot = engine.all_running()[0][2]
    snapshot.params.palette.append("#00ff00")
    assert len(engine.running("owner", EffectType.CHASE).params.palette) == 2
    assert engine.active_definitions()[0].name == "Odd roster"


def test_even_selection_keeps_original_order_index_for_phase_and_conflicts():
    clock = FakeClock()
    engine = EffectEngine(club_patch(), TempoClock(clock=clock, bpm=120))
    definition = FxDefinition(
        name="Even", kind=EffectType.CHASE,
        params=params(selection="even", spread=1.0, softness=0.0),
    )
    engine.start(
        "owner", EffectType.CHASE, 0,
        ["led-bar-1", "dj-spot-1", "dj-spot-2"], definition=definition,
    )
    effect = engine.running("owner", EffectType.CHASE)
    assert effect.fixtures == ("dj-spot-1",)
    assert effect.positions == (1,) and effect.participant_count == 3
    clock.now += 0.25
    values = engine.render("dj-spot-1", {"intensity": 1.0})
    assert values.values["intensity"] == pytest.approx(0.0)
    assert engine.render("led-bar-1", {"intensity": 1.0}).values["intensity"] == 1.0


def test_snapshot_includes_live_and_changed_preview_definitions():
    engine = EffectEngine(club_patch(), TempoClock(clock=FakeClock()))
    engine.start("owner", EffectType.CHASE, 0, ["led-bar-1"])
    engine.start_preview()
    assert len(engine.all_running()) == 1
    engine.set_param("owner", EffectType.CHASE, "size", 0.25)
    assert len(engine.all_running()) == 2
    assert engine.all_running(preview=False)[0][2].params.size == 1.0
    assert engine.all_running(preview=True)[0][2].params.size == 0.25
    engine.stop("owner", EffectType.CHASE)
    assert len(engine.all_running()) == 1
    assert engine.all_running(preview=True) == []


def test_seconds_rate_edits_preserve_current_phase():
    clock = FakeClock()
    engine = EffectEngine(club_patch(), TempoClock(clock=clock))
    definition = FxDefinition(
        name="Slow", kind=EffectType.CHASE, params=params(timing="seconds", seconds=8.0)
    )
    engine.start("owner", EffectType.CHASE, 0, ["led-bar-1"], definition=definition)
    clock.now += 2.0
    assert engine.set_param("owner", EffectType.CHASE, "speed", 0.5)
    effect = engine.running("owner", EffectType.CHASE)
    assert effect.params.seconds == pytest.approx(math.sqrt(30.0))
    assert engine._phase(effect, clock.now) == pytest.approx(0.25)
    clock.now += 0.25
    assert engine._phase(effect, clock.now) == pytest.approx(0.25 + 0.25 / math.sqrt(30.0))


def test_target_tour_speed_controls_travel_and_preserves_stage_progress():
    patch = club_patch()
    rig = make_rig(patch)
    clock = FakeClock()
    engine = EffectEngine(patch, TempoClock(clock=clock), rig=rig)
    definition = FxDefinition(
        name="Tour", kind=EffectType.CIRCLE,
        params=params(timing="seconds", target_ids=["in-zone"], pattern="none", travel=8, hold=4),
    )
    engine.start("tour", EffectType.CIRCLE, 0, [HEAD], definition=definition)
    clock.now += 4.0
    assert engine.set_param("tour", EffectType.CIRCLE, "speed", 0.5)
    effect = engine.running("tour", EffectType.CIRCLE)
    assert effect.params.travel == pytest.approx(math.sqrt(30.0))
    assert engine._tour_elapsed(effect, clock.now) == pytest.approx(effect.params.travel * 0.5)
    assert effect.params.hold == 4.0 and effect.params.seconds == 4.0


def test_target_tour_validates_reachability_and_static_hold_contract():
    patch = club_patch()
    rig = make_rig(patch)
    rig.save_zone(INCLUDE)
    rig.set_assignments({HEAD: HeadAssignment(include_zones=[INCLUDE.id])})
    clock = FakeClock()
    engine = EffectEngine(patch, TempoClock(clock=clock), rig=rig)
    stationary = FxDefinition(
        name="Target Hold", kind=EffectType.CIRCLE,
        params=params(
            timing="seconds", target_ids=["in-zone"], pattern="none",
            size=1.0, travel=1.0, hold=2.0,
        ),
    )
    assert engine.validate_start(EffectType.CIRCLE, [HEAD], stationary) == (HEAD,)
    engine.start("tour", EffectType.CIRCLE, 0, [HEAD], definition=stationary)
    base = {"pan": 0.5, "tilt": 0.5, "speed": 0.0, "intensity": 1.0}
    assert engine.render(HEAD, base).dark_movement
    clock.now += 1.0
    hold = engine.render(HEAD, base)
    assert not hold.dark_movement and hold.static_target
    assert engine.stationary_target(HEAD, hold.values)
    tampered = dict(hold.values, pan=hold.values["pan"] + 0.001)
    assert not engine.stationary_target(HEAD, tampered)
    clock.now += 2.0
    assert engine.render(HEAD, base).dark_movement


def test_patterned_target_tour_requires_included_target_center():
    patch = club_patch()
    rig = make_rig(patch)
    engine = EffectEngine(patch, TempoClock(clock=FakeClock()), rig=rig)
    patterned = FxDefinition(
        name="Pattern", kind=EffectType.CIRCLE,
        params=params(target_ids=["clear"], pattern="circle", size=0.5),
    )
    with pytest.raises(ValueError, match="included areas"):
        engine.validate_start(EffectType.CIRCLE, [HEAD], patterned)
    stationary = patterned.model_copy(
        update={"params": patterned.params.model_copy(update={"pattern": "none"})}
    )
    assert engine.validate_start(EffectType.CIRCLE, [HEAD], stationary) == (HEAD,)


def test_frame_freezes_render_and_stationary_protection_across_hold_boundary():
    patch = club_patch()
    rig = make_rig(patch)
    clock = FakeClock()
    engine = EffectEngine(patch, TempoClock(clock=clock), rig=rig)
    definition = FxDefinition(
        name="Hold", kind=EffectType.CIRCLE,
        params=params(timing="seconds", target_ids=["in-zone"], pattern="none", travel=1, hold=2),
    )
    engine.start("tour", EffectType.CIRCLE, 0, [HEAD], definition=definition)
    clock.now += 2.9
    with engine.frame():
        hold = engine.render(HEAD, {"pan": 0.5, "tilt": 0.5, "intensity": 1.0})
        assert hold.static_target
        clock.now += 0.2
        with engine.frame():
            assert engine.stationary_target(HEAD, hold.values)
        assert engine.stationary_target(HEAD, hold.values)
    assert not engine.stationary_target(HEAD, hold.values)


def test_frame_restores_clock_sampling_after_exception():
    clock = FakeClock()
    engine = EffectEngine(club_patch(), TempoClock(clock=clock, bpm=120))
    with pytest.raises(RuntimeError):
        with engine.frame():
            now = engine._now()
            clock.now += 1.0
            assert engine._now() == now
            assert engine._beats(clock.now) == 0.0
            raise RuntimeError("abort")
    assert engine._now() == clock.now
    assert engine._beats(clock.now) == pytest.approx(2.0)


ZONE_B = INCLUDE.model_copy(update={
    "id": "floor-b", "name": "Floor B", "corner": {"x": 3.5, "y": 5.0}, "length": 1.5, "width": 2.0,
})


def zone_rig():
    patch = club_patch()
    rig = make_rig(patch)
    rig.save_zone(ZONE_B)
    rig.set_assignments({HEAD: HeadAssignment(include_zones=[INCLUDE.id, ZONE_B.id])})
    return patch, rig


def centre_aim(rig, patch, zone, reference=0.5):
    from lichtwerk.positioning import aim_at
    from test_zones import SIZE

    return aim_at(rig.head(HEAD), patch.get(HEAD).definition.motion, zone.point_at(0.5, 0.5, SIZE), reference)


def test_move_route_modes_are_explicit_and_exclusive():
    assert params().stops == [] and not params().touring and not params().single_point
    single = params(zone_ids=["a"])
    assert single.single_point and not single.touring
    assert params(target_ids=["a"], pattern="none").touring
    assert params(zone_ids=["a", "b"], pattern="circle").touring
    with pytest.raises(ValidationError, match="either named targets or named zones"):
        params(target_ids=["a"], zone_ids=["b"])
    with pytest.raises(ValidationError, match="duplicate"):
        params(zone_ids=["a", "a"])


def test_zone_route_requires_included_zone():
    patch, rig = zone_rig()
    rig.set_assignments({HEAD: HeadAssignment(include_zones=[INCLUDE.id])})
    engine = EffectEngine(patch, TempoClock(clock=FakeClock()), rig=rig)
    definition = FxDefinition(name="Zone", kind=EffectType.CIRCLE, params=params(zone_ids=[ZONE_B.id]))
    with pytest.raises(ValueError, match="excluded"):
        engine.validate_start(EffectType.CIRCLE, [HEAD], definition)
    excluded = HeadAssignment(include_zones=[INCLUDE.id, ZONE_B.id], exclude_zones=[ZONE_B.id])
    rig.set_assignments({HEAD: excluded})
    with pytest.raises(ValueError, match="excluded"):
        engine.validate_start(EffectType.CIRCLE, [HEAD], definition)


def test_single_zone_anchors_continuous_pattern_without_dark_travel():
    patch, rig = zone_rig()
    clock = FakeClock()
    engine = EffectEngine(patch, TempoClock(clock=clock), rig=rig)
    definition = FxDefinition(
        name="Anchor", kind=EffectType.CIRCLE,
        params=params(timing="seconds", seconds=4.0, zone_ids=[ZONE_B.id], size=0.0),
    )
    assert engine.validate_start(EffectType.CIRCLE, [HEAD], definition) == (HEAD,)
    engine.start("a", EffectType.CIRCLE, 0, [HEAD], definition=definition)
    base = {"pan": 0.1, "tilt": 0.1, "intensity": 1.0}
    frame = engine.render(HEAD, base)
    centre = centre_aim(rig, patch, ZONE_B, base["pan"])
    assert not frame.dark_movement and not frame.static_target
    assert frame.values["pan"] == pytest.approx(centre.pan)
    assert frame.values["tilt"] == pytest.approx(centre.tilt)

    moving = definition.model_copy(update={"params": definition.params.model_copy(update={"size": 0.8})})
    engine.start("a", EffectType.CIRCLE, 0, [HEAD], definition=moving)
    samples = []
    for _ in range(4):
        clock.now += 1.0
        frame = engine.render(HEAD, base)
        assert not frame.dark_movement
        samples.append((round(frame.values["pan"], 6), round(frame.values["tilt"], 6)))
    assert len(set(samples)) > 1


def test_single_target_anchor_is_static_hold_at_size_zero():
    patch = club_patch()
    rig = make_rig(patch)
    rig.set_assignments({HEAD: HeadAssignment(include_zones=[INCLUDE.id])})
    engine = EffectEngine(patch, TempoClock(clock=FakeClock()), rig=rig)
    definition = FxDefinition(
        name="Anchor", kind=EffectType.CIRCLE, params=params(target_ids=["clear"], size=0.0),
    )
    engine.start("a", EffectType.CIRCLE, 0, [HEAD], definition=definition)
    frame = engine.render(HEAD, {"pan": 0.5, "tilt": 0.5, "intensity": 1.0})
    assert not frame.dark_movement and frame.static_target
    assert engine.stationary_target(HEAD, frame.values)


def test_zone_tour_travels_dark_and_holds_at_each_zone_centre():
    patch, rig = zone_rig()
    clock = FakeClock()
    engine = EffectEngine(patch, TempoClock(clock=clock), rig=rig)
    definition = FxDefinition(
        name="Zones", kind=EffectType.CIRCLE,
        params=params(timing="seconds", zone_ids=[INCLUDE.id, ZONE_B.id], pattern="none", travel=1, hold=2),
    )
    engine.start("z", EffectType.CIRCLE, 0, [HEAD], definition=definition)
    base = {"pan": 0.5, "tilt": 0.5, "intensity": 1.0}
    assert engine.render(HEAD, base).dark_movement
    for elapsed, zone, dark in ((1.5, INCLUDE, False), (3.5, None, True), (4.5, ZONE_B, False),
                                (6.5, None, True), (7.5, INCLUDE, False)):
        clock.now = 100.0 + elapsed
        frame = engine.render(HEAD, base)
        assert frame.dark_movement is dark, elapsed
        assert not frame.static_target
        if zone is not None:
            centre = centre_aim(rig, patch, zone)
            assert frame.values["pan"] == pytest.approx(centre.pan)
            assert frame.values["tilt"] == pytest.approx(centre.tilt)
    clock.now = 103.0
    assert engine.set_param("z", EffectType.CIRCLE, "speed", 0.5)
    assert engine.running("z", EffectType.CIRCLE).params.travel != 1
