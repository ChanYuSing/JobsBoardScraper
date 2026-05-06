"""Minimal read/write helpers for the scheduler_run table."""
from __future__ import annotations

from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_run_start(conn, source: str, phase: str) -> int:
    """Insert an in-progress scheduler_run row; return its id."""
    cur = conn.execute(
        "INSERT INTO scheduler_run (source, phase, started_at, status) "
        "VALUES (?, ?, ?, 'running') RETURNING id",
        (source, phase, _now_iso()),
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    return int(new_id)


def log_run_queued(conn, source: str, phase: str) -> int:
    """Insert a queued scheduler_run row (not yet started); return its id."""
    cur = conn.execute(
        "INSERT INTO scheduler_run (source, phase, status) VALUES (?, ?, 'queued') RETURNING id",
        (source, phase),
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    return int(new_id)


def mark_run_active(conn: object, run_id: int) -> bool:
    """Transition a 'queued' row to 'running'. Returns False if already gone/cancelled."""
    cur = conn.execute(
        "UPDATE scheduler_run SET started_at = ?, status = 'running' WHERE id = ? AND status = 'queued'",
        (_now_iso(), run_id),
    )
    conn.commit()
    return cur.rowcount == 1


def update_run_jobs_found(conn: object, run_id: int, count: int) -> None:
    """Write partial progress to jobs_found while a run is still active."""
    conn.execute(
        "UPDATE scheduler_run SET jobs_found = ? WHERE id = ? AND status = 'running'",
        (count, run_id),
    )
    conn.commit()


def set_run_jobs_total(conn: object, run_id: int, total: int) -> None:
    """Store the total number of items to process (used for enrich phase)."""
    conn.execute(
        "UPDATE scheduler_run SET jobs_total = ? WHERE id = ?",
        (total, run_id),
    )
    conn.commit()


def log_run_finish(
    conn: object,
    run_id: int,
    *,
    status: str,                   # ok | error | cancelled
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


def cancel_running_rows(conn: object) -> int:
    """Mark all 'running' rows as 'cancelled' (user-initiated kill).

    Returns the number of rows updated.
    """
    cur = conn.execute(
        """
        UPDATE scheduler_run
           SET finished_at = ?, status = 'cancelled',
               error = 'cancelled by user'
         WHERE status = 'running'
        """,
        (_now_iso(),),
    )
    conn.commit()
    return cur.rowcount


def cancel_run_by_id(conn: object, run_id: int) -> int:
    """Mark a specific run row as 'cancelled'.

    Returns 1 if the row was updated, 0 if not found or already finished.
    """
    cur = conn.execute(
        """
        UPDATE scheduler_run
           SET finished_at = CASE WHEN status = 'running' THEN ? ELSE finished_at END,
               status = 'cancelled',
               error = 'cancelled by user'
         WHERE id = ? AND status IN ('running', 'queued')
        """,
        (_now_iso(), run_id),
    )
    conn.commit()
    return cur.rowcount


def purge_all_runs(conn: object) -> int:
    """Delete every run record that is not currently 'running'.

    Returns the number of rows deleted.
    """
    cur = conn.execute("DELETE FROM scheduler_run WHERE status != 'running'")
    conn.commit()
    return cur.rowcount


def delete_runs_by_ids(conn: object, ids: list[int]) -> int:
    """Delete specific run records by id. Running rows are protected.

    Returns the number of rows deleted.
    """
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(
        f"DELETE FROM scheduler_run WHERE id IN ({placeholders}) AND status != 'running'",
        ids,
    )
    conn.commit()
    return cur.rowcount

