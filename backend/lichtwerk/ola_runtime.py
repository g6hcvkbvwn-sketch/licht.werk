"""Finding, configuring and starting the OLA daemon (`olad`) for the desktop app.

OLA stays an external program (Homebrew). The app only starts it when it is installed and not
already running, with its own config folder so the user's `~/.ola` is never touched.
"""

import logging
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

OLAD_CANDIDATES = (
    "/opt/homebrew/bin/olad",
    "/opt/homebrew/opt/ola/bin/olad",
    "/usr/local/bin/olad",
    "/usr/local/opt/ola/bin/olad",
)
OLA_HTTP_PORT = 9090
OLA_DOWNLOAD_URL = "https://www.openlighting.org/ola/getting-started/downloads/"
OLA_INSTALL_COMMAND = "brew install ola"


def find_olad(candidates: tuple[str, ...] = OLAD_CANDIDATES) -> Optional[str]:
    """Path of the `olad` program, or None. Dock apps have a minimal PATH, so check fixed places."""
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return shutil.which("olad")


def olad_running(port: int = OLA_HTTP_PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def seed_ola_config(template_dir: Path, target_dir: Path) -> None:
    """Copy the bundled OLA config files that do not exist yet; never overwrite user edits."""
    target_dir.mkdir(parents=True, exist_ok=True)
    for source in template_dir.glob("*.conf"):
        destination = target_dir / source.name
        if not destination.exists():
            shutil.copy2(source, destination)


def start_olad(config_dir: Path, olad: Optional[str] = None, wait_seconds: float = 6.0) -> Optional[subprocess.Popen]:
    """Start olad unless one is already running. Returns the child process we own, if any."""
    if olad_running():
        logger.info("olad already running on port %s; using it.", OLA_HTTP_PORT)
        return None
    olad = olad or find_olad()
    if olad is None:
        logger.warning("olad is not installed; DMX output stays unavailable.")
        return None
    log_file = open(config_dir / "olad.log", "ab")
    process = subprocess.Popen(
        [olad, "-c", str(config_dir), "--no-http-quit", "--no-register-with-dns-sd"],
        stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline and process.poll() is None:
        if olad_running():
            logger.info("Started olad (pid %s) with config %s.", process.pid, config_dir)
            return process
        time.sleep(0.1)
    logger.error("olad did not become ready (exit code %s). See %s.", process.poll(), config_dir / "olad.log")
    return process if process.poll() is None else None


def stop_olad(process: Optional[subprocess.Popen]) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()


class OlaSetupStatus(BaseModel):
    installed: bool
    running: bool
    install_command: str = OLA_INSTALL_COMMAND
    download_url: str = OLA_DOWNLOAD_URL


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/ola/setup", response_model=OlaSetupStatus)
    def ola_setup() -> OlaSetupStatus:
        return OlaSetupStatus(installed=find_olad() is not None, running=olad_running())

    return router
