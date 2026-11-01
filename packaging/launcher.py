"""Entry point of the LichtWerk.app bundle: sets the data folders, then starts the desktop app."""
import os
import sys
from pathlib import Path

DATA_DIR = Path.home() / "Library" / "Application Support" / "LichtWerk"
os.environ.setdefault("LICHTWERK_CONFIG_DIR", str(DATA_DIR / "config"))
os.environ.setdefault("LICHTWERK_LOG_DIR", str(DATA_DIR / "logs"))

from lichtwerk.desktop import main  # noqa: E402  (must come after the environment is set)

if __name__ == "__main__":
    sys.exit(main(DATA_DIR))
