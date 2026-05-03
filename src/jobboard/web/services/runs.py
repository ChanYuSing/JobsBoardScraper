"""Runs service — queries against scheduler_run table."""
from __future__ import annotations

import sqlite3
from typing import Any


def list_runs(
    conn: sqlite3.Connection,
    *,
    source: str = "",
    status: str = "",
    date_from: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    where, params = [], []
    if source:
        where.append("source = ?")
        params.append(source)
    if status:
        where.append("status = ?")
        params.append(status)
    if date_from:
        where.append("started_at >= ?")
        params.append(date_from)

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""
        SELECT id, source, phase, started_at, finished_at,
               status, jobs_found, error,
               ROUND((JULIANDAY(COALESCE(finished_at, DATETIME('now')))
                      - JULIANDAY(started_at)) * 86400) AS duration_sec
        FROM scheduler_run
        {clause}
        ORDER BY started_at DESC
        LIMIT ?
        """,
        params + [limit],
    ).fetchall()
    cols = ["id", "source", "phase", "started_at", "finished_at",
            "status", "jobs_found", "error", "duration_sec"]
    return [dict(zip(cols, r)) for r in rows]


def distinct_sources(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT source FROM scheduler_run ORDER BY source"
    ).fetchall()
    return [r[0] for r in rows]
