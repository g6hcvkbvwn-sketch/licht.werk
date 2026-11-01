"""Desktop app: runs olad (if available), the backend and the built UI in a native window."""

import logging
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn

from .cli import build_app, configure_logging, parse_args
from .ola_runtime import find_olad, seed_ola_config, start_olad, stop_olad

logger = logging.getLogger(__name__)
SKIP_SUFFIXES = (".bak", ".tmp")


def resource_dir() -> Path:
    """Folder with the bundled UI, default config and OLA templates."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


def seed_config(template: Path, target: Path) -> None:
    """Copy bundled default config files that are missing; existing user files stay untouched."""
    for source in template.rglob("*"):
        if source.is_dir() or source.name.startswith(".") or source.name.endswith(SKIP_SUFFIXES):
            continue
        destination = target / source.relative_to(template)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def mount_frontend(app, dist: Path) -> None:
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")


def main(data_dir: Path) -> int:
    import webview

    resources = resource_dir()
    config_dir = data_dir / "config"
    seed_config(resources / "config", config_dir)
    configure_logging()

    ola_dir = data_dir / "ola"
    seed_ola_config(resources / "packaging" / "ola", ola_dir)
    olad = start_olad(ola_dir) if find_olad() else None

    port = free_port()
    app = build_app(parse_args(["serve"]))
    mount_frontend(app, resources / "frontend" / "dist")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)

    webview.create_window("LichtWerk", f"http://127.0.0.1:{port}", width=1440, height=900, min_size=(1100, 700))
    webview.start()
    server.should_exit = True
    stop_olad(olad)
    return 0
