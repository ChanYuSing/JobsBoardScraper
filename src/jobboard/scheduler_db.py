"""Minimal read/write helpers for the scheduler_run table."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_run_start(conn: sqlite3.Connection, source: str, phase: str) -> int:
    """Insert an in-progress scheduler_run row; return its id."""
    cur = conn.execute(
        "INSERT INTO scheduler_run (source, phase, started_at, status) VALUES (?, ?, ?, 'running')",
        (source, phase, _now_iso()),
    )
    conn.commit()
    return int(cur.lastrowid)


def log_run_finish(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,                   # ok | error | exhausted
    jobs_found: int | None = None,
    error: str | None = None,
) -> None:
    """Close out a scheduler_run row."""
    conn.execute(
        """
        UPDATE scheduler_run
           SET finished_at = ?,
               status      = ?,
               jobs_found  = ?,
               error       = ?
         WHERE id = ?
        """,
        (_now_iso(), status, jobs_found, error, run_id),
    )
    conn.commit()
