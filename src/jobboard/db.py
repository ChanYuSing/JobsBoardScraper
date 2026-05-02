"""SQLite: connect, init, run lifecycle."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Literal


# Schema lives next to the package (see [tool.setuptools.package-data] in pyproject.toml).
_SCHEMA_SQL = files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Connect / init
# ---------------------------------------------------------------------------
def connect(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply shared schema, per-source schemas, and sweep orphaned runs."""
    conn.executescript(_SCHEMA_SQL)
    _sweep_orphan_runs(conn)
    from .sources.linkedin import db as li_db
    li_db.init_schema(conn)
    from .sources.jobsdb import db as jdb_db
    jdb_db.init_schema(conn)
    conn.commit()


def _sweep_orphan_runs(conn: sqlite3.Connection) -> None:
    """Mark any leftover 'running' runs as 'error' (process crashed/was killed)."""
    conn.execute(
        """
        UPDATE run
           SET status = 'error',
               finished_at = COALESCE(finished_at, ?),
               error = COALESCE(error, 'process did not finish cleanly')
         WHERE status = 'running'
        """,
        (_now_iso(),),
    )


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------
def start_run(conn: sqlite3.Connection, source: str) -> int:
    cur = conn.execute(
        "INSERT INTO run (source, started_at, status) VALUES (?, ?, 'running')",
        (source, _now_iso()),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: Literal["ok", "error"],
    total_seen: int = 0,
    inserted: int = 0,
    updated: int = 0,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE run
           SET finished_at = ?,
               status      = ?,
               total_seen  = ?,
               inserted    = ?,
               updated     = ?,
               error       = ?
         WHERE id = ?
        """,
        (_now_iso(), status, total_seen, inserted, updated, error, run_id),
    )
    conn.commit()

