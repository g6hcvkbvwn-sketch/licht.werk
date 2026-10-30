"""Validated application configuration."""

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "app.json"
DEFAULT_FIXTURES_DIR = PROJECT_ROOT / "config" / "fixtures"
DEFAULT_PATCH_PATH = PROJECT_ROOT / "config" / "patch.json"
DEFAULT_SCENES_PATH = PROJECT_ROOT / "config" / "scenes.json"
DEFAULT_ROOM_PATH = PROJECT_ROOT / "config" / "room.json"
DEFAULT_HEADS_PATH = PROJECT_ROOT / "config" / "heads.json"
DEFAULT_MIDI_PATH = PROJECT_ROOT / "config" / "midi.json"
DEFAULT_APC40_PATH = PROJECT_ROOT / "config" / "apc40.json"
DEFAULT_MAP_PATH = PROJECT_ROOT / "config" / "map.json"


class OlaConfig(BaseModel):
    """Connection to a local olad and the universes real output may touch."""

    model_config = ConfigDict(extra="forbid", strict=True)

    host: Literal["127.0.0.1", "localhost"] = "127.0.0.1"
    port: StrictInt = Field(default=9090, ge=1, le=65535)
    allowed_universes: list[StrictInt] = Field(min_length=1)


class MidiConfig(BaseModel):
    """MIDI controller: input, plus LED feedback to the same device."""

    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: StrictBool = False
    # Case-insensitive part of the MIDI device name to connect to.
    device: str = Field(default="XONE:K2", min_length=1)
    # MIDI channel the controller is set to (1-16; Xone:K2 factory default 15). Used by the
    # on-screen controller.
    channel: StrictInt = Field(default=15, ge=1, le=16)
    # Case-insensitive part of the AKAI APC40 MKII device name (fixed logic, see apc40.py).
    apc40_device: str = Field(default="APC40 mkII", min_length=1)


class AppConfig(BaseModel):
    """Application configuration. Real output stays off unless explicitly configured and armed."""

    model_config = ConfigDict(extra="forbid", strict=True)

    output_mode: Literal["simulated", "ola"] = "simulated"
    physical_output_enabled: StrictBool = False
    universes: list[StrictInt] = Field(default_factory=lambda: list(range(0, 8)))
    ola: Optional[OlaConfig] = None
    midi: Optional[MidiConfig] = None
    # How often running fades update the output (frames per second).
    fade_rate_hz: StrictInt = Field(default=40, ge=1, le=100)
    # Upper limit for native strobe (0.0-1.0 of the fixture's strobe range). Safety cap.
    max_strobe: float = Field(default=1.0, ge=0.0, le=1.0)

    @classmethod
    def validate_configuration(cls, values: object) -> "AppConfig":
        config = cls.model_validate(values)
        if config.physical_output_enabled and config.output_mode != "ola":
            raise ValueError("physical_output_enabled requires output_mode \"ola\".")
        if config.output_mode == "ola":
            if config.ola is None:
                raise ValueError("output_mode \"ola\" requires an \"ola\" section.")
            if not set(config.ola.allowed_universes) <= set(config.universes):
                raise ValueError("ola.allowed_universes must be configured universes.")
            if len(set(config.ola.allowed_universes)) != len(config.ola.allowed_universes):
                raise ValueError("ola.allowed_universes must be unique.")
        if not config.universes:
            raise ValueError("Configure at least one simulated DMX universe.")
        if any(not 0 <= universe <= 7 for universe in config.universes):
            raise ValueError("Configured universe numbers must be between 0 and 7.")
        if len(set(config.universes)) != len(config.universes):
            raise ValueError("Configured universe numbers must be unique.")
        return config


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Load JSON settings and report actionable file or validation errors."""
    try:
        with path.open("r", encoding="utf-8") as config_file:
            values = json.load(config_file)
    except FileNotFoundError as error:
        raise ValueError(f"Configuration file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in configuration file {path} at line {error.lineno}, "
            f"column {error.colno}: {error.msg}"
        ) from error
    try:
        return AppConfig.validate_configuration(values)
    except (ValidationError, ValueError) as error:
        raise ValueError(f"Invalid configuration in {path}: {error}") from error
