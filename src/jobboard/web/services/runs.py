"""Runs service — queries against scheduler_run table."""
from __future__ import annotations

import sqlite3
from typing import Any

from ...scheduler_db import purge_all_runs, delete_runs_by_ids  # re-export for routes

__all__ = ["list_runs", "distinct_sources", "purge_all_runs", "delete_runs_by_ids"]


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
        SELECT id, source, phase, scope, started_at, finished_at,
               status,
               COALESCE(jobs_found,
                   (SELECT COUNT(*) FROM scheduler_run_job srj WHERE srj.run_id = sr.id)
               ) AS jobs_found,
               jobs_total, error,
               CASE WHEN started_at IS NULL THEN NULL
                    ELSE ROUND((JULIANDAY(COALESCE(finished_at, DATETIME('now')))
                                - JULIANDAY(started_at)) * 86400)
               END AS duration_sec
        FROM scheduler_run sr
        {clause}
        ORDER BY id DESC
        LIMIT ?
        """,
        params + [limit],
    ).fetchall()
    return [dict(r) for r in rows]


def distinct_sources(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT source FROM scheduler_run ORDER BY source"
    ).fetchall()
    return [r[0] for r in rows]
