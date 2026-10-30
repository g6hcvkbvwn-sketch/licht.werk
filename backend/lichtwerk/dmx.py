"""DMX universe buffers and validation."""

from dataclasses import dataclass, field

DMX_SLOT_COUNT = 512
DMX_VALUE_MIN = 0
DMX_VALUE_MAX = 255
DMX_UNIVERSE_MIN = 0
DMX_UNIVERSE_MAX = 7


def validate_universe(universe: int) -> None:
    if type(universe) is not int or not DMX_UNIVERSE_MIN <= universe <= DMX_UNIVERSE_MAX:
        raise ValueError(
            f"Universe must be an integer from {DMX_UNIVERSE_MIN} "
            f"to {DMX_UNIVERSE_MAX}."
        )


def validate_channel(channel: int) -> None:
    if type(channel) is not int or not 1 <= channel <= DMX_SLOT_COUNT:
        raise ValueError(f"DMX channel must be an integer from 1 to {DMX_SLOT_COUNT}.")


def validate_value(value: int) -> None:
    if type(value) is not int or not DMX_VALUE_MIN <= value <= DMX_VALUE_MAX:
        raise ValueError(f"DMX value must be an integer from 0 to {DMX_VALUE_MAX}.")


@dataclass
class UniverseBuffer:
    """A mutable 512-slot buffer using user-facing, one-based channel numbers."""

    universe: int
    _slots: list[int] = field(default_factory=lambda: [0] * DMX_SLOT_COUNT, repr=False)

    def __post_init__(self) -> None:
        validate_universe(self.universe)
        if len(self._slots) != DMX_SLOT_COUNT:
            raise ValueError(f"A DMX universe must contain exactly {DMX_SLOT_COUNT} slots.")
        for value in self._slots:
            validate_value(value)

    def set_channel(self, channel: int, value: int) -> None:
        validate_channel(channel)
        validate_value(value)
        self._slots[channel - 1] = value

    def snapshot(self) -> tuple[int, ...]:
        return tuple(self._slots)
