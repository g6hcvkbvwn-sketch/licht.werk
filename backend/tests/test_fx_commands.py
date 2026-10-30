"""Verify Target Tour permissions through the complete logical output pipeline."""

import pytest

from lichtwerk.effect_engine import EffectEngine
from lichtwerk.effects import EffectParams, EffectType, FxDefinition
from lichtwerk.fx_presets import FxPresetStore
from lichtwerk.fx_service import FxService
from lichtwerk.position_service import PositionService
from lichtwerk.tempo import TempoClock
from conftest import FakeClock
from test_zones import HEAD, make_controller, make_patch, make_rig


def test_tour_stationary_exception_never_lights_travel_and_stop_reveals_base():
    patch = make_patch()
    rig = make_rig(patch)
    clock = FakeClock()
    controller, _ = make_controller(patch, rig, clock)
    positions = PositionService(controller, rig)
    engine = EffectEngine(patch, TempoClock(clock=clock), rig=rig)
    controller.set_effects(engine)
    service = FxService(controller, positions, engine, FxPresetStore(patch, rig), 40)
    controller.set_values(HEAD, {"intensity": 0.7, "red": 1.0, "pan": 0.5, "tilt": 0.5})
    base = controller.state(HEAD)
    definition = FxDefinition(
        name="Stationary tour", kind=EffectType.CIRCLE,
        params=EffectParams(
            beats=4.0, size=0.3, spread=0.0, softness=0.0, timing="seconds",
            seconds=4.0, target_ids=["in-zone", "clear"], travel=1.0, hold=1.0, pattern="none",
        ),
    )
    service.start("test", HEAD, EffectType.CIRCLE, 0, definition)
    clock.now += 0.5
    controller.hold("flash", {HEAD: {"intensity": 1.0}})
    assert controller.output_state(HEAD)["intensity"] == 0.0
    controller.release("flash")
    clock.now += 0.6
    assert controller.output_state(HEAD)["intensity"] == pytest.approx(0.7)
    assert controller.state(HEAD) == base
    clock.now += 1.4
    assert controller.output_state(HEAD)["intensity"] == 0.0
    engine.stop("test", EffectType.CIRCLE)
    assert controller.state(HEAD) == base
    assert controller.output_state(HEAD)["pan"] == base["pan"]


def test_rejected_patterned_tour_does_not_replace_current_fx():
    patch = make_patch()
    rig = make_rig(patch)
    clock = FakeClock()
    controller, _ = make_controller(patch, rig, clock)
    engine = EffectEngine(patch, TempoClock(clock=clock), rig=rig)
    controller.set_effects(engine)
    service = FxService(controller, PositionService(controller, rig), engine, FxPresetStore(patch, rig), 40)
    params = EffectParams(beats=4.0, size=0.5, spread=0.0, softness=0.0, target_ids=["in-zone"], pattern="none")
    service.start("test", HEAD, EffectType.CIRCLE, 0, FxDefinition(name="Good", kind=EffectType.CIRCLE, params=params))
    bad = FxDefinition(name="Bad", kind=EffectType.CIRCLE, params=params.model_copy(update={"pattern": "circle"}))
    with pytest.raises(ValueError, match="included area"):
        service.start("test", HEAD, EffectType.CIRCLE, 1, bad)
    assert engine.running("test", EffectType.CIRCLE).name == "Good"
