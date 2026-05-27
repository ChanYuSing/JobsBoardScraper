"""Shared FastAPI dependencies."""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from fastapi.templating import Jinja2Templates

from ..config import Config, load_config
from ..db import connect

# ── paths ──────────────────────────────────────────────────────────────────
def _data_dir() -> Path:
    """Resolve the data directory (config.yaml + SQLite) at startup.

    Resolution order:
      1. JOBBOARD_DATA_DIR env var  — set by the standalone launcher
      2. ~/JobBoardScraper/         — frozen (PyInstaller) exe without env var
      3. repo root                  — dev / Docker mode
    """
    if env := os.environ.get("JOBBOARD_DATA_DIR"):
        return Path(env)
    if getattr(sys, "frozen", False):      # running as a PyInstaller bundle
        return Path.home() / "JobBoardScraper"
    return Path(__file__).parents[3]       # src/jobboard/web/deps.py → repo root


DATA_DIR = _data_dir()
CONFIG_PATH = str(DATA_DIR / "config.yaml")

# ── shared Jinja2 environment ───────────────────────────────────────────────
_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _localdt(value: str | None) -> str:
    """Convert a UTC ISO string (e.g. '2026-05-04T11:23:00Z') to local time 'YYYY-MM-DD HH:MM'."""
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        local = dt.astimezone()          # convert to system local timezone
        return local.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value[:16]


templates.env.filters["localdt"] = _localdt


def get_config() -> Config:
    cfg = load_config(CONFIG_PATH)
    # Anchor sqlite_path to DATA_DIR when it is relative so the app works
    # regardless of the process working directory (Docker, exe, dev).
    if not Path(cfg.storage.sqlite_path).is_absolute():
        cfg.storage.sqlite_path = str(DATA_DIR / cfg.storage.sqlite_path)
    return cfg


def get_db() -> Generator[sqlite3.Connection, None, None]:
    cfg = get_config()
    conn = connect(cfg.storage.sqlite_path)
    try:
        yield conn
    finally:
        conn.close()
