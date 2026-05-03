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
    conn = sqlite3.connect(p, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply shared schema, per-source schemas, and sweep orphaned runs."""
    _migrate_analysis_schema(conn)
    conn.executescript(_SCHEMA_SQL)
    _sweep_orphan_runs(conn)
    from .sources.linkedin import db as li_db
    li_db.init_schema(conn)
    from .sources.jobsdb import db as jdb_db
    jdb_db.init_schema(conn)
    conn.commit()


def sync_fields_upsert(conn: sqlite3.Connection, fields: list) -> None:
    """Upsert fields from config into field_def without deleting anything.

    Safe to call at startup — never removes existing rows or scores.
    ``fields`` is a list of FieldCfg objects (or any object with .name/.type/.description).
    """
    for i, f in enumerate(fields):
        conn.execute(
            """
            INSERT INTO field_def (name, type, description, sort_order)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                type        = excluded.type,
                description = excluded.description,
                sort_order  = excluded.sort_order
            """,
            (f.name, f.type, f.description, i),
        )
    conn.commit()


def sync_fields_full(conn: sqlite3.Connection, fields: list) -> list[str]:
    """Full sync: upsert all given fields, delete any not present.

    Returns list of deleted field names (those with existing scores are still deleted —
    caller must confirm first).
    ``fields`` is a list of FieldCfg objects.
    """
    incoming_names = {f.name for f in fields}
    existing = conn.execute("SELECT id, name FROM field_def").fetchall()
    deleted = []
    for row in existing:
        if row["name"] not in incoming_names:
            conn.execute("DELETE FROM field_def WHERE id = ?", (row["id"],))
            deleted.append(row["name"])
    for i, f in enumerate(fields):
        conn.execute(
            """
            INSERT INTO field_def (name, type, description, sort_order)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                type        = excluded.type,
                description = excluded.description,
                sort_order  = excluded.sort_order
            """,
            (f.name, f.type, f.description, i),
        )
    conn.commit()
    return deleted


def _migrate_analysis_schema(conn: sqlite3.Connection) -> None:
    """Drop analysis tables that have an outdated schema."""
    ja_cols = {r[1] for r in conn.execute("PRAGMA table_info(job_analysis)").fetchall()}
    fd_cols = {r[1] for r in conn.execute("PRAGMA table_info(field_def)").fetchall()}
    # Drop if job_analysis predates EAV, or if field_def still has the old label column
    if (ja_cols and "field_id" not in ja_cols) or "label" in fd_cols:
        conn.execute("DROP TABLE IF EXISTS job_analysis")
        conn.execute("DROP TABLE IF EXISTS field_def")
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

