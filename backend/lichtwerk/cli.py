"""Command-line startup for the local development server."""

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from .api import create_app
from .apc40_pads import PadStore
from .config import (
    DEFAULT_APC40_PATH,
    DEFAULT_CONFIG_PATH,
    DEFAULT_FIXTURES_DIR,
    DEFAULT_PATCH_PATH,
    DEFAULT_HEADS_PATH,
    DEFAULT_MAP_PATH,
    DEFAULT_MIDI_PATH,
    DEFAULT_ROOM_PATH,
    DEFAULT_SCENES_PATH,
    load_config,
)
from .fixtures import load_fixture_library
from .patch import load_patch
from .midi_mapping import MidiMappingStore
from .room import RigStore
from .room_map import MapStore
from .scenes import SceneStore
from .sequences import SequenceStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def configure_logging() -> None:
    log_directory = PROJECT_ROOT / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_directory / "lichtwerk.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(), file_handler],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LichtWerk local prototype.")
    parser.add_argument("command", choices=["serve"], help="Start the local API server.")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (localhost only by default).",
    )
    parser.add_argument("--port", type=int, default=8010, help="HTTP port.")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the JSON application configuration.",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURES_DIR,
        help="Folder containing fixture definition JSON files.",
    )
    parser.add_argument(
        "--patch",
        type=Path,
        default=DEFAULT_PATCH_PATH,
        help="Path to the JSON fixture patch.",
    )
    parser.add_argument(
        "--scenes",
        type=Path,
        default=DEFAULT_SCENES_PATH,
        help="Path to the JSON scene file (created on the first save).",
    )
    parser.add_argument(
        "--room",
        type=Path,
        default=DEFAULT_ROOM_PATH,
        help="Path to the JSON room file (targets, zones).",
    )
    parser.add_argument(
        "--heads",
        type=Path,
        default=DEFAULT_HEADS_PATH,
        help="Path to the JSON moving-head setup file.",
    )
    parser.add_argument(
        "--midi",
        type=Path,
        default=DEFAULT_MIDI_PATH,
        help="Path to the JSON MIDI mapping file (created when the first control is learned).",
    )
    parser.add_argument(
        "--apc40",
        type=Path,
        default=DEFAULT_APC40_PATH,
        help="Path to the JSON APC40 scene-pad file (created when the first pad is assigned).",
    )
    parser.add_argument(
        "--map",
        type=Path,
        default=DEFAULT_MAP_PATH,
        help="Path to the JSON room-map file (created when the first fixture is placed).",
    )
    args = parser.parse_args()

    configure_logging()
    settings = load_config(args.config)
    library = load_fixture_library(args.fixtures)
    patch = load_patch(args.patch, library, settings.universes)
    scenes = SceneStore.load(args.scenes, patch)
    sequences = SequenceStore.load(args.scenes.with_name("sequences.json"), scenes)
    rig = RigStore.load(args.room, args.heads, patch)
    midi = MidiMappingStore.load(args.midi, patch)
    apc40_pads = PadStore.load(args.apc40)
    map_store = MapStore.load(args.map)
    app = create_app(
        settings,
        patch=patch,
        fixture_library=library,
        scene_store=scenes,
        sequence_store=sequences,
        rig_store=rig,
        midi_store=midi,
        apc40_pads=apc40_pads,
        map_store=map_store,
        name_path=args.patch.with_name("fixture-names.json"),
        fx_path=args.patch.with_name("fx-presets.json"),
    )
    uvicorn.run(app, host=args.host, port=args.port, log_config=None)
