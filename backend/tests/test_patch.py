import json
from pathlib import Path

import pytest

from lichtwerk.config import DEFAULT_FIXTURES_DIR, DEFAULT_PATCH_PATH, load_config
from lichtwerk.fixtures import FixtureDefinition, load_fixture_library
from lichtwerk.patch import PatchedFixture, build_patch, load_patch

RGB = FixtureDefinition.model_validate(
    {
        "id": "rgb",
        "manufacturer": "Test",
        "model": "RGB",
        "fixture_type": "led_bar",
        "source": "test",
        "modes": [
            {
                "name": "3ch",
                "channel_count": 3,
                "channels": [{"attribute": "red"}, {"attribute": "green"}, {"attribute": "blue"}],
            }
        ],
    }
)
LIBRARY = {"rgb": RGB}


def entry(fixture_id: str, start: int, universe: int = 5, **extra) -> PatchedFixture:
    return PatchedFixture(
        id=fixture_id,
        name=fixture_id,
        definition=extra.pop("definition", "rgb"),
        mode=extra.pop("mode", "3ch"),
        universe=universe,
        start_address=start,
        **extra,
    )


def test_valid_patch_resolves_definition_and_address_range() -> None:
    patch = build_patch([entry("a", 1), entry("b", 4)], LIBRARY, [5])

    assert patch.get("b").end_address == 6


def test_adjacent_fixtures_on_other_universes_do_not_collide() -> None:
    build_patch([entry("a", 1, universe=4), entry("b", 1, universe=5)], LIBRARY, [4, 5])


def test_overlapping_addresses_are_rejected() -> None:
    with pytest.raises(ValueError, match="Address collision in universe 5"):
        build_patch([entry("a", 1), entry("b", 3)], LIBRARY, [5])


def test_disabled_fixtures_still_reserve_their_addresses() -> None:
    with pytest.raises(ValueError, match="collision"):
        build_patch([entry("a", 1, enabled=False), entry("b", 2)], LIBRARY, [5])


def test_collision_with_non_neighbouring_fixture_is_detected() -> None:
    wide = FixtureDefinition.model_validate(
        {
            "id": "wide",
            "manufacturer": "Test",
            "model": "Wide",
            "fixture_type": "wash",
            "source": "test",
            "modes": [
                {
                    "name": "6ch",
                    "channel_count": 6,
                    "channels": [
                        {"attribute": "intensity"},
                        {"attribute": "intensity", "resolution": "fine"},
                        {"attribute": "red"},
                        {"attribute": "green"},
                        {"attribute": "blue"},
                        {"attribute": "white"},
                    ],
                }
            ],
        }
    )
    library = {**LIBRARY, "wide": wide}
    # wide uses 1-6, b 2-4, d 5-7. d does not touch its sorted neighbour b but overlaps wide.
    entries = [
        entry("wide", 1, definition="wide", mode="6ch"),
        entry("b", 2),
        entry("d", 5),
    ]
    with pytest.raises(ValueError) as error:
        build_patch(entries, library, [5])

    assert "'wide' (1-6) overlaps 'b'" in str(error.value)
    assert "'wide' (1-6) overlaps 'd'" in str(error.value)


def test_universe_overflow_is_rejected() -> None:
    with pytest.raises(ValueError, match="510-512|511-513"):
        build_patch([entry("a", 511)], LIBRARY, [5])


def test_unknown_definition_mode_duplicate_and_universe_are_all_reported() -> None:
    entries = [
        entry("a", 1, definition="missing"),
        entry("b", 10, mode="nope"),
        entry("c", 20),
        entry("c", 30),
        entry("d", 40, universe=3),
    ]
    with pytest.raises(ValueError) as error:
        build_patch(entries, LIBRARY, [5])

    message = str(error.value)
    assert "unknown fixture definition 'missing'" in message
    assert "no mode 'nope'" in message
    assert "Duplicate fixture id 'c'" in message
    assert "universe 3 is not a configured universe" in message


def test_patch_rejects_out_of_range_address_and_universe() -> None:
    with pytest.raises(ValueError):
        entry("a", 0)
    with pytest.raises(ValueError):
        entry("a", 1, universe=8)


def test_patch_file_errors_name_the_file(tmp_path: Path) -> None:
    path = tmp_path / "patch.json"
    path.write_text(json.dumps({"fixtures": [{"id": "a"}]}), encoding="utf-8")

    with pytest.raises(ValueError, match="patch.json"):
        load_patch(path, LIBRARY, [5])


def test_bundled_patch_places_led_bar_on_universe_5_address_1() -> None:
    settings = load_config()
    patch = load_patch(
        DEFAULT_PATCH_PATH, load_fixture_library(DEFAULT_FIXTURES_DIR), settings.universes
    )
    bar = patch.get("led-bar-1")

    assert (bar.patch.universe, bar.patch.start_address, bar.mode.name) == (5, 1, "6Ch")


def test_bundled_patch_has_no_overlaps_and_uses_chosen_modes() -> None:
    settings = load_config()
    patch = load_patch(
        DEFAULT_PATCH_PATH, load_fixture_library(DEFAULT_FIXTURES_DIR), settings.universes
    )
    spans = {
        f.patch.id: (f.mode.name, f.patch.start_address, f.end_address)
        for f in patch.fixtures
    }

    assert spans == {
        "led-bar-1": ("6Ch", 1, 6),
        "moving-head-1": ("37ch", 7, 43),
        "moving-head-2": ("37ch", 44, 80),
        "hazer-1": ("2ch", 99, 100),
        "dj-spot-1": ("9CH", 81, 89),
        "dj-spot-2": ("9CH", 90, 98),
    }
