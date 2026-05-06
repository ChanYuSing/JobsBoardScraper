"""Shared FastAPI dependencies."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from fastapi.templating import Jinja2Templates

from ..config import Config, load_config
from ..db import pool_conn

# â”€â”€ paths â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
CONFIG_PATH = str(Path(__file__).parents[3] / "config.yaml")   # repo root

# â”€â”€ shared Jinja2 environment â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _localdt(value: str | None) -> str:
    """Convert a UTC ISO string (e.g. '2026-05-04T11:23:00Z') to local time 'YYYY-MM-DD HH:MM'."""
    if not value:
        return "â€”"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        local = dt.astimezone()          # convert to system local timezone
        return local.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value[:16]


templates.env.filters["localdt"] = _localdt


def get_config() -> Config:
    return load_config(CONFIG_PATH)


def get_db() -> Generator[object, None, None]:
    conn = pool_conn()
    try:
        yield conn
    finally:
        conn.close()  # returns to pool
