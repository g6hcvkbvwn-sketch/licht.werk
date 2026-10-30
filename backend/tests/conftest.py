"""Shared test data: a small simulated rig with groups (no real hardware involved)."""

import pytest

from lichtwerk.config import DEFAULT_FIXTURES_DIR
from lichtwerk.fixtures import FixtureDefinition, load_fixture_library
from lichtwerk.patch import Patch, PatchedFixture, build_patch

# Test-only profile with a real dimmer channel; not a real fixture.
DIMMER_RGB = FixtureDefinition.model_validate(
    {
        "id": "test-dimmer-rgb",
        "manufacturer": "Test",
        "model": "Dimmer RGB",
        "fixture_type": "wash",
        "source": "test only",
        "modes": [
            {
                "name": "4ch",
                "channel_count": 4,
                "channels": [
                    {"attribute": "intensity"},
                    {"attribute": "red"},
                    {"attribute": "green"},
                    {"attribute": "blue"},
                ],
            }
        ],
    }
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def rig_patch(*, wash_enabled: bool = True) -> Patch:
    """bar-1 and bar-2 (Thunder Wash, virtual dimmer) plus wash-1 (dimmer RGB) on universe 5."""
    library = {**load_fixture_library(DEFAULT_FIXTURES_DIR), DIMMER_RGB.id: DIMMER_RGB}

    def fixture(fixture_id: str, definition: str, mode: str, start: int, groups, enabled=True):
        return PatchedFixture(
            id=fixture_id,
            name=fixture_id,
            definition=definition,
            mode=mode,
            universe=5,
            start_address=start,
            groups=groups,
            enabled=enabled,
        )

    entries = [
        fixture("bar-1", "cameo-thunder-wash-100-rgb", "3Ch3", 1, ["bars", "all"]),
        fixture("bar-2", "cameo-thunder-wash-100-rgb", "3Ch3", 4, ["bars", "all"]),
        fixture("wash-1", "test-dimmer-rgb", "4ch", 10, ["all"], wash_enabled),
    ]
    return build_patch(entries, library, [5])


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def patch() -> Patch:
    return rig_patch()
