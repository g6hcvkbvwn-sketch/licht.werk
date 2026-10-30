import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from lichtwerk.config import DEFAULT_FIXTURES_DIR
from lichtwerk.fixtures import (
    FixtureDefinition,
    FixtureMode,
    load_fixture_library,
    render_mode,
    to_8bit,
    to_16bit,
)


def mode(*channels: dict, name: str = "m") -> FixtureMode:
    return FixtureMode(name=name, channel_count=len(channels), channels=list(channels))


def test_8bit_conversion_rounds_and_clamps() -> None:
    assert to_8bit(0.0) == 0
    assert to_8bit(1.0) == 255
    assert to_8bit(0.5) == 128
    assert to_8bit(-0.3) == 0
    assert to_8bit(1.7) == 255


def test_16bit_conversion_splits_coarse_and_fine() -> None:
    assert to_16bit(0.0) == (0, 0)
    assert to_16bit(1.0) == (255, 255)
    assert to_16bit(0.5) == (128, 0)  # 32768 = 0x8000
    assert to_16bit(0.25) == (64, 0)  # 16384 = 0x4000
    assert to_16bit(2.0) == (255, 255)


def test_rgb_without_dimmer_uses_intensity_as_virtual_dimmer() -> None:
    rgb = mode({"attribute": "red"}, {"attribute": "green"}, {"attribute": "blue"})

    assert render_mode(rgb, {"red": 1.0, "green": 0.5, "blue": 0.0, "intensity": 1.0}) == [
        255,
        128,
        0,
    ]
    assert render_mode(rgb, {"red": 1.0, "intensity": 0.5}) == [128, 0, 0]
    assert render_mode(rgb, {"red": 1.0, "green": 1.0, "blue": 1.0}) == [0, 0, 0]


def test_real_dimmer_channel_does_not_scale_colors() -> None:
    dimmed = mode({"attribute": "intensity"}, {"attribute": "red"})

    assert render_mode(dimmed, {"intensity": 0.5, "red": 1.0}) == [128, 255]


def test_16bit_attribute_renders_coarse_and_fine_channels() -> None:
    fine = mode(
        {"attribute": "intensity", "resolution": "coarse"},
        {"attribute": "intensity", "resolution": "fine"},
    )

    assert render_mode(fine, {"intensity": 0.5}) == [128, 0]
    assert render_mode(fine, {"intensity": 1.0}) == [255, 255]


def test_mode_reports_intensity_even_without_dimmer_channel() -> None:
    rgb = mode({"attribute": "red"}, {"attribute": "green"}, {"attribute": "blue"})

    assert rgb.attributes() == ["intensity", "red", "green", "blue"]


@pytest.mark.parametrize(
    "values",
    [
        {"name": "m", "channel_count": 2, "channels": [{"attribute": "red"}]},
        {
            "name": "m",
            "channel_count": 2,
            "channels": [{"attribute": "red"}, {"attribute": "red"}],
        },
        {
            "name": "m",
            "channel_count": 1,
            "channels": [{"attribute": "red", "resolution": "fine"}],
        },
        {"name": "m", "channel_count": 1, "channels": [{"attribute": "sparkle"}]},
        {"name": "m", "channel_count": 1, "channels": [{"attribute": "red", "active_min": 5}]},
        {"name": "m", "channel_count": 1, "channels": [{"attribute": "red", "fixed_dmx": 0}]},
        {"name": "m", "channel_count": 1, "channels": [{"fixed_dmx": 0}]},
    ],
)
def test_invalid_modes_are_rejected(values: dict) -> None:
    with pytest.raises(ValidationError):
        FixtureMode.model_validate(values)


def test_definition_requires_source_and_unique_modes() -> None:
    base = {
        "id": "x",
        "manufacturer": "M",
        "model": "X",
        "fixture_type": "wash",
        "modes": [{"name": "a", "channel_count": 1, "channels": [{"attribute": "red"}]}],
    }
    with pytest.raises(ValidationError):
        FixtureDefinition.model_validate(base)
    with pytest.raises(ValidationError):
        FixtureDefinition.model_validate(
            {**base, "source": "manual", "modes": base["modes"] * 2}
        )
    assert FixtureDefinition.model_validate({**base, "source": "manual"}).mode("a")


def test_bundled_library_contains_thunder_wash_3ch_mode() -> None:
    library = load_fixture_library(DEFAULT_FIXTURES_DIR)
    thunder = library["cameo-thunder-wash-100-rgb"]

    assert [c.attribute for c in thunder.mode("3Ch3").channels] == ["red", "green", "blue"]


def test_library_loader_names_the_broken_file(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text(json.dumps({"id": "broken"}), encoding="utf-8")

    with pytest.raises(ValueError, match="broken.json"):
        load_fixture_library(tmp_path)
