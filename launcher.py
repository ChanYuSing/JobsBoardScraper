"""Standalone launcher — entry point for the PyInstaller executable.

Usage:
    python launcher.py          # dev / testing
    JobBoardScraper.exe         # distributed build (Windows)
    ./JobBoardScraper-macos     # distributed build (macOS)
    ./JobBoardScraper-linux     # distributed build (Linux)

On first run, copies config.template.yaml → ~/JobBoardScraper/config.yaml.
Sets JOBBOARD_DATA_DIR so the web app reads config and writes SQLite there.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import webbrowser
from pathlib import Path

# ---------------------------------------------------------------------------
# Data directory — persists config.yaml and jobs.sqlite across runs
# ---------------------------------------------------------------------------
DATA_DIR = Path.home() / "JobBoardScraper"

# Dev-mode safety net: if jobboard isn't installed, add src/ to sys.path so
# `uvicorn.run("jobboard.web.main:app")` can find the package.
if not getattr(sys, "frozen", False):
    _src = Path(__file__).parent / "src"
    if _src.exists() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))


def _bundle_root() -> Path:
    """Return the directory where bundled resources (config.template.yaml) live."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)   # PyInstaller extracts files here at runtime
    return Path(__file__).parent    # dev: project root (alongside launcher.py)


def _first_run_setup() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    config_dest = DATA_DIR / "config.yaml"
    if not config_dest.exists():
        template = _bundle_root() / "config.template.yaml"
        if template.exists():
            shutil.copy(template, config_dest)
            print(f"[JobBoardScraper] Created default config at {config_dest}")
            print("[JobBoardScraper] Open the web UI → Sources to configure your search.")
        else:
            print(
                f"[JobBoardScraper] Warning: config.template.yaml not found.\n"
                f"  Create {config_dest} manually before using the app."
            )


# ---------------------------------------------------------------------------
# Browser opener
# ---------------------------------------------------------------------------
def _open_browser() -> None:
    time.sleep(2.5)
    webbrowser.open("http://localhost:8001")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    _first_run_setup()
    os.environ["JOBBOARD_DATA_DIR"] = str(DATA_DIR)

    print(f"[JobBoardScraper] Data directory : {DATA_DIR}")
    print("[JobBoardScraper] Starting server at http://localhost:8001 ...")
    print("[JobBoardScraper] Close this window to stop the app.")

    threading.Thread(target=_open_browser, daemon=True).start()

    import uvicorn
    from jobboard.web.main import app  # import directly so PyInstaller's frozen importer handles it
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8001,
        log_level="warning",
    )
