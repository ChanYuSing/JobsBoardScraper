"""SQLite access: connect, init, run lifecycle, upsert, detail enrichment."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Literal

from .normalise import JobRecord

# Schema lives next to the package (see [tool.setuptools.package-data] in pyproject.toml).
_SCHEMA_SQL = files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Connect / init / migrate
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
    # Migrate any pre-existing legacy `job` table BEFORE running the schema
    # script, because the script creates indexes on columns added in step 2.
    _migrate_legacy_job(conn)
    conn.executescript(_SCHEMA_SQL)
    _sweep_orphan_runs(conn)
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


def _migrate_legacy_job(conn: sqlite3.Connection) -> None:
    """Add columns introduced in later steps to pre-existing job tables.

    Old columns from removed features (closed_at, params_hash, etc.) are left
    in place: SQLite cannot easily drop columns, and unused columns are inert.
    """
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='job'"
    ).fetchone()
    if not table_exists:
        return
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(job)")}
    if "first_seen_run_id" not in cols:
        conn.execute("ALTER TABLE job ADD COLUMN first_seen_run_id INTEGER")
    if "last_seen_run_id" not in cols:
        conn.execute("ALTER TABLE job ADD COLUMN last_seen_run_id INTEGER")
    # detail-fetch columns
    for name, sql_type in (
        ("description_html",  "TEXT"),
        ("description_text",  "TEXT"),
        ("abstract",          "TEXT"),
        ("expires_at_utc",    "TEXT"),
        ("is_expired",        "INTEGER"),
        ("detail_raw",        "TEXT"),
        ("detail_fetched_at", "TEXT"),
        ("detail_error",      "TEXT"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE job ADD COLUMN {name} {sql_type}")
    # Backfill: any pre-step-2 row should at least pretend it was first seen
    # on whatever its most recent run was. Harmless if no-op.
    conn.execute(
        "UPDATE job SET first_seen_run_id = last_seen_run_id "
        "WHERE first_seen_run_id IS NULL AND last_seen_run_id IS NOT NULL"
    )


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------
def start_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO run (started_at, status) VALUES (?, 'running')",
        (_now_iso(),),
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


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
UpsertOutcome = Literal["inserted", "updated"]


def upsert_job(
    conn: sqlite3.Connection, rec: JobRecord, run_id: int
) -> UpsertOutcome:
    """Insert a new job or update an existing one. Returns the outcome."""
    now = _now_iso()
    existing = conn.execute("SELECT 1 FROM job WHERE id = ?", (rec.id,)).fetchone()
    params = {**_record_params(rec), "now": now, "run_id": run_id}

    if existing is None:
        conn.execute(
            """
            INSERT INTO job (
                id, title, company, location, classification, subclassification,
                work_types, work_arrangement, salary_label, teaser, bullet_points,
                listing_date_utc, listing_date_label, url, raw,
                first_seen_at, last_seen_at,
                first_seen_run_id, last_seen_run_id
            ) VALUES (
                :id, :title, :company, :location, :classification, :subclassification,
                :work_types, :work_arrangement, :salary_label, :teaser, :bullet_points,
                :listing_date_utc, :listing_date_label, :url, :raw,
                :now, :now,
                :run_id, :run_id
            )
            """,
            params,
        )
        return "inserted"

    conn.execute(
        """
        UPDATE job SET
            title              = :title,
            company            = :company,
            location           = :location,
            classification     = :classification,
            subclassification  = :subclassification,
            work_types         = :work_types,
            work_arrangement   = :work_arrangement,
            salary_label       = :salary_label,
            teaser             = :teaser,
            bullet_points      = :bullet_points,
            listing_date_utc   = :listing_date_utc,
            listing_date_label = :listing_date_label,
            url                = :url,
            raw                = :raw,
            last_seen_at       = :now,
            last_seen_run_id   = :run_id
         WHERE id = :id
        """,
        params,
    )
    return "updated"


def _record_params(rec: JobRecord) -> dict:
    return {
        "id": rec.id,
        "title": rec.title,
        "company": rec.company,
        "location": rec.location,
        "classification": rec.classification,
        "subclassification": rec.subclassification,
        "work_types": rec.work_types,
        "work_arrangement": rec.work_arrangement,
        "salary_label": rec.salary_label,
        "teaser": rec.teaser,
        "bullet_points": rec.bullet_points_json,
        "listing_date_utc": rec.listing_date_utc,
        "listing_date_label": rec.listing_date_label,
        "url": rec.url,
        "raw": rec.raw_json,
    }


# ---------------------------------------------------------------------------
# Job-detail enrichment
# ---------------------------------------------------------------------------
def jobs_needing_detail(
    conn: sqlite3.Connection,
    *,
    stale_days: int | None = None,
    limit: int | None = None,
) -> list[str]:
    """Return job ids that have no detail yet, or whose detail is older than
    ``stale_days`` days."""
    where = ["(detail_fetched_at IS NULL"]
    args: list = []
    if stale_days is not None:
        cutoff = _now_minus_days(stale_days)
        where.append("OR detail_fetched_at < ?")
        args.append(cutoff)
    where_sql = " ".join(where) + ")"
    sql = f"SELECT id FROM job WHERE {where_sql} ORDER BY first_seen_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
    return [row["id"] for row in conn.execute(sql, args)]


def update_job_detail(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    description_html: str | None,
    description_text: str | None,
    abstract: str | None,
    expires_at_utc: str | None,
    is_expired: bool | None,
    detail_raw: str,
) -> None:
    conn.execute(
        """
        UPDATE job SET
            description_html  = :description_html,
            description_text  = :description_text,
            abstract          = :abstract,
            expires_at_utc    = :expires_at_utc,
            is_expired        = :is_expired,
            detail_raw        = :detail_raw,
            detail_fetched_at = :now,
            detail_error      = NULL
         WHERE id = :id
        """,
        {
            "id": job_id,
            "description_html": description_html,
            "description_text": description_text,
            "abstract": abstract,
            "expires_at_utc": expires_at_utc,
            "is_expired": 1 if is_expired else 0 if is_expired is not None else None,
            "detail_raw": detail_raw,
            "now": _now_iso(),
        },
    )


def record_detail_error(
    conn: sqlite3.Connection,
    job_id: str,
    error: str,
    *,
    transient: bool = False,
) -> None:
    """Store the error message for a failed detail fetch.

    For ``transient`` errors (Cloudflare blocks, 5xx) we deliberately leave
    ``detail_fetched_at`` untouched so the next enrich run re-attempts the
    job immediately. For permanent errors (404, parse failure, etc.) we set
    ``detail_fetched_at`` so the job is skipped until ``--stale-days``.
    """
    if transient:
        conn.execute(
            "UPDATE job SET detail_error = ? WHERE id = ?",
            (error[:500], job_id),
        )
    else:
        conn.execute(
            "UPDATE job SET detail_error = ?, detail_fetched_at = ? WHERE id = ?",
            (error[:500], _now_iso(), job_id),
        )


def _now_minus_days(days: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

