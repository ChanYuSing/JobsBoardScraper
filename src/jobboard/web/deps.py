"""Shared FastAPI dependencies."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Generator

from ..config import Config, load_config
from ..db import connect

# ── paths ──────────────────────────────────────────────────────────────────
CONFIG_PATH = str(Path(__file__).parents[3] / "config.yaml")   # repo root


def get_config() -> Config:
    return load_config(CONFIG_PATH)


def get_db() -> Generator[sqlite3.Connection, None, None]:
    cfg = get_config()
    conn = connect(cfg.storage.sqlite_path)
    try:
        yield conn
    finally:
        conn.close()
