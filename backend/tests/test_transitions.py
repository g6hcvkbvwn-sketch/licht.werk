import pytest

from lichtwerk.fixture_service import FixtureController
from lichtwerk.output import SimulatedDmxOutput
from lichtwerk.service import LightingService
from lichtwerk.transitions import Fade, TransitionRunner


def make(patch, clock):
    lighting = LightingService(SimulatedDmxOutput(), (5,))
    return FixtureController(patch, lighting, clock=clock), lighting


def test_fade_interpolates_linearly_and_clamps_to_target() -> None:
    fade = Fade(start_value=0.2, target=1.0, start_time=10.0, duration=4.0)

    assert fade.value_at(9.0) == 0.2
    assert fade.value_at(12.0) == pytest.approx(0.6)
    assert fade.value_at(14.0) == 1.0
    assert fade.value_at(99.0) == 1.0
    assert not fade.finished(13.9)
    assert fade.finished(14.0)


def test_zero_duration_fade_is_immediate() -> None:
    assert Fade(0.0, 0.7, 5.0, 0.0).value_at(5.0) == 0.7


def test_scene_style_fade_progresses_with_ticks(patch, clock) -> None:
    controller, lighting = make(patch, clock)
    controller.apply({"wash-1": {"intensity": 1.0, "red": 1.0}}, fade_seconds=2.0)

    assert controller.transition_active()
    assert lighting.read_output(5)[9:11] == (0, 0)
    clock.now += 1.0
    controller.tick()
    assert controller.state("wash-1")["intensity"] == pytest.approx(0.5)
    assert lighting.read_output(5)[9:11] == (128, 128)
    clock.now += 1.5
    assert controller.tick() is False
    assert controller.state("wash-1")["intensity"] == 1.0
    assert not controller.transition_active()


def test_new_fade_starts_from_current_mid_fade_value(patch, clock) -> None:
    controller, _ = make(patch, clock)
    controller.apply({"wash-1": {"intensity": 1.0}}, fade_seconds=2.0)
    clock.now += 1.0
    controller.tick()
    controller.apply({"wash-1": {"intensity": 0.0}}, fade_seconds=1.0)
    clock.now += 0.5
    controller.tick()

    assert controller.state("wash-1")["intensity"] == pytest.approx(0.25)


def test_direct_command_cancels_only_the_fades_it_touches(patch, clock) -> None:
    controller, _ = make(patch, clock)
    controller.apply({"wash-1": {"intensity": 1.0, "red": 1.0}}, fade_seconds=2.0)
    clock.now += 1.0
    controller.tick()
    controller.set_values("wash-1", {"red": 0.1})
    clock.now += 1.0
    controller.tick()

    assert controller.state("wash-1") == pytest.approx(
        {"intensity": 1.0, "red": 0.1, "green": 0.0, "blue": 0.0}
    )


def test_attributes_not_in_a_command_keep_their_value(patch, clock) -> None:
    controller, _ = make(patch, clock)
    controller.set_values("bar-1", {"intensity": 1.0, "green": 1.0})
    controller.apply({"bar-1": {"red": 1.0}}, fade_seconds=0.0)

    assert controller.state("bar-1") == {"intensity": 1.0, "red": 1.0, "green": 1.0, "blue": 0.0}


def test_master_scales_output_but_not_live_state(patch, clock) -> None:
    controller, lighting = make(patch, clock)
    controller.set_values("bar-1", {"intensity": 1.0, "red": 1.0})
    controller.set_values("wash-1", {"intensity": 1.0, "blue": 1.0})
    controller.set_master(0.5)

    out = lighting.read_output(5)
    assert out[0:3] == (128, 0, 0)  # virtual dimmer scales the colors
    assert out[9:13] == (128, 0, 0, 255)  # real dimmer channel is scaled, colors are not
    assert controller.state("bar-1")["intensity"] == 1.0
    controller.set_master(1.0)
    assert lighting.read_output(5)[0] == 255


def test_blackout_hides_running_fade_and_release_shows_current_value(patch, clock) -> None:
    controller, lighting = make(patch, clock)
    controller.apply({"wash-1": {"intensity": 1.0}}, fade_seconds=2.0)
    lighting.set_blackout(True)
    clock.now += 1.0
    controller.tick()
    assert set(lighting.read_output(5)) == {0}
    lighting.set_blackout(False)

    assert lighting.read_output(5)[9] == 128


def test_invalid_fade_time_and_master_are_rejected(patch, clock) -> None:
    controller, _ = make(patch, clock)

    with pytest.raises(ValueError):
        controller.apply({"bar-1": {"red": 1.0}}, fade_seconds=-1.0)
    with pytest.raises(ValueError):
        controller.set_master(1.5)


def test_group_command_skips_members_without_the_attribute_and_disabled_ones(clock) -> None:
    from conftest import rig_patch

    controller, _ = make(rig_patch(wash_enabled=False), clock)
    changed = controller.set_group_values("all", {"red": 1.0})

    assert changed == ["bar-1", "bar-2"]
    assert controller.state("wash-1")["red"] == 0.0


def test_runner_ticks_in_background_and_survives_errors() -> None:
    calls = []

    def tick() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    runner = TransitionRunner(tick, rate_hz=100)
    runner.start()
    import time

    deadline = time.monotonic() + 2.0
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    runner.stop()

    assert len(calls) >= 3


def test_runner_rejects_unreasonable_rate() -> None:
    with pytest.raises(ValueError):
        TransitionRunner(lambda: None, rate_hz=0)
