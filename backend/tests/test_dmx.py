import pytest

from lichtwerk.dmx import DMX_SLOT_COUNT, UniverseBuffer


def test_universe_starts_with_512_zeroed_slots() -> None:
    buffer = UniverseBuffer(6)

    assert len(buffer.snapshot()) == DMX_SLOT_COUNT
    assert set(buffer.snapshot()) == {0}


def test_channel_numbers_are_one_based() -> None:
    buffer = UniverseBuffer(6)

    buffer.set_channel(1, 17)
    buffer.set_channel(512, 255)

    slots = buffer.snapshot()
    assert slots[0] == 17
    assert slots[511] == 255
    assert slots[1:511] == (0,) * 510


@pytest.mark.parametrize(
    ("channel", "value"),
    [(0, 10), (513, 10), (1, -1), (1, 256), (True, 10), (1, True)],
)
def test_rejects_invalid_channel_or_value(channel: int, value: int) -> None:
    buffer = UniverseBuffer(6)

    with pytest.raises(ValueError):
        buffer.set_channel(channel, value)


@pytest.mark.parametrize("universe", [-1, 8, True, "6"])
def test_rejects_invalid_universe(universe: int) -> None:
    with pytest.raises(ValueError):
        UniverseBuffer(universe)
