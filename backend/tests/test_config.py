import json

import pytest
from pydantic import ValidationError

from lichtwerk.config import AppConfig, load_config


def test_rejects_physical_output_without_ola_mode() -> None:
    with pytest.raises(ValueError, match="requires output_mode"):
        AppConfig.validate_configuration(
            {
                "output_mode": "simulated",
                "physical_output_enabled": True,
                "universes": [6],
            }
        )


def test_rejects_duplicate_or_out_of_range_universes() -> None:
    with pytest.raises(ValueError, match="unique"):
        AppConfig.validate_configuration({"universes": [6, 6]})

    with pytest.raises(ValueError, match="between 0 and 7"):
        AppConfig.validate_configuration({"universes": [8]})


def test_rejects_unknown_configuration_fields() -> None:
    with pytest.raises(ValidationError):
        AppConfig.validate_configuration({"universes": [6], "hardware": "ola"})


def test_load_config_reports_invalid_json(tmp_path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON"):
        load_config(path)


def test_load_config_validates_json(tmp_path) -> None:
    path = tmp_path / "app.json"
    path.write_text(
        json.dumps(
            {
                "output_mode": "simulated",
                "physical_output_enabled": False,
                "universes": [6],
            }
        ),
        encoding="utf-8",
    )

    assert load_config(path).universes == [6]
