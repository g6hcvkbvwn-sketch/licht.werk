import pytest

from lichtwerk.output import SimulatedDmxOutput
from lichtwerk.service import LightingService


def test_channel_command_updates_simulated_universe() -> None:
    output = SimulatedDmxOutput()
    service = LightingService(output, (6,))

    service.set_channel(universe=6, channel=1, value=255)

    slots = output.read_universe(6)
    assert slots[0] == 255
    assert slots[1:3] == (0, 0)
    assert len(slots) == 512


def test_blackout_zeros_output_without_destroying_desired_values() -> None:
    output = SimulatedDmxOutput()
    service = LightingService(output, (6,))
    service.set_channel(universe=6, channel=2, value=127)

    service.set_blackout(True)
    assert set(output.read_universe(6)) == {0}
    assert service.blackout_active()

    service.set_blackout(False)
    assert output.read_universe(6)[1] == 127
    assert not service.blackout_active()


def test_channel_updates_during_blackout_remain_hidden_until_release() -> None:
    output = SimulatedDmxOutput()
    service = LightingService(output, (6,))
    service.set_blackout(True)

    service.set_channel(universe=6, channel=3, value=64)

    assert set(output.read_universe(6)) == {0}
    service.set_blackout(False)
    assert output.read_universe(6)[2] == 64


def test_rejects_unconfigured_universe() -> None:
    service = LightingService(SimulatedDmxOutput(), (6,))

    with pytest.raises(ValueError, match="not configured"):
        service.set_channel(universe=5, channel=1, value=1)
