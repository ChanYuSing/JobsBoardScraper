"""Runs service â€” queries against scheduler_run table."""
from __future__ import annotations

from typing import Any

from ...scheduler_db import purge_all_runs, delete_runs_by_ids  # re-export for routes

__all__ = ["list_runs", "distinct_sources", "purge_all_runs", "delete_runs_by_ids"]


def list_runs(
    conn: object,
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
               status, jobs_found, jobs_total, error,
               CASE WHEN started_at IS NULL THEN NULL
                    ELSE ROUND(EXTRACT(EPOCH FROM (
                                COALESCE(finished_at::timestamptz, NOW())
                              - started_at::timestamptz)))
               END AS duration_sec
        FROM scheduler_run
        {clause}
        ORDER BY id DESC
        LIMIT ?
        """,
        params + [limit],
    ).fetchall()
    return [dict(r) for r in rows]


def distinct_sources(conn: object) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT source FROM scheduler_run ORDER BY source"
    ).fetchall()
    return [r[0] for r in rows]
