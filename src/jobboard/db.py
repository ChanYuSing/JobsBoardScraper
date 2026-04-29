"""SQLite access: connect, init, run lifecycle, upsert, detail enrichment."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path
from typing import Literal

from .normalise import JobRecord


def _bool_to_int(b: bool | None) -> int | None:
    """Map ``True/False/None`` to ``1/0/None`` for SQLite storage."""
    return None if b is None else int(b)


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
    """Apply the (idempotent) schema script and tidy up orphaned runs."""
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


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
UpsertOutcome = Literal["inserted", "updated"]


def upsert_job(
    conn: sqlite3.Connection, rec: JobRecord, run_id: int
) -> UpsertOutcome:
    """Insert a new job or update an existing one. Returns the outcome.

    Adapters that fetch full details inline (e.g. JobSpy) populate the optional
    ``description_*`` / ``detail_raw`` fields on the record; we persist them on
    insert and use ``COALESCE`` on update so a later search-only refresh never
    blanks out a description we already have.
    """
    now = _now_iso()
    existing = conn.execute(
        "SELECT 1 FROM job WHERE source = ? AND external_id = ?",
        (rec.source, rec.external_id),
    ).fetchone()
    has_inline_detail = bool(rec.description_text or rec.description_html)
    params = {
        **_record_params(rec),
        "now": now,
        "run_id": run_id,
        "detail_fetched_at": now if has_inline_detail else None,
    }

    if existing is None:
        conn.execute(
            """
            INSERT INTO job (
                source, external_id, title, company, location,
                classification, subclassification,
                work_types, work_arrangement, salary_label, teaser, bullet_points,
                listing_date_utc, listing_date_label, url, raw,
                first_seen_at, last_seen_at,
                first_seen_run_id, last_seen_run_id,
                description_html, description_text, abstract,
                expires_at_utc, is_expired, detail_raw, detail_fetched_at
            ) VALUES (
                :source, :external_id, :title, :company, :location,
                :classification, :subclassification,
                :work_types, :work_arrangement, :salary_label, :teaser, :bullet_points,
                :listing_date_utc, :listing_date_label, :url, :raw,
                :now, :now,
                :run_id, :run_id,
                :description_html, :description_text, :abstract,
                :expires_at_utc, :is_expired, :detail_raw, :detail_fetched_at
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
            last_seen_run_id   = :run_id,
            description_html   = COALESCE(:description_html, description_html),
            description_text   = COALESCE(:description_text, description_text),
            abstract           = COALESCE(:abstract, abstract),
            expires_at_utc     = COALESCE(:expires_at_utc, expires_at_utc),
            is_expired         = COALESCE(:is_expired, is_expired),
            detail_raw         = COALESCE(:detail_raw, detail_raw),
            detail_fetched_at  = COALESCE(:detail_fetched_at, detail_fetched_at)
         WHERE source = :source AND external_id = :external_id
        """,
        params,
    )
    return "updated"


def _record_params(rec: JobRecord) -> dict:
    return {
        "source": rec.source,
        "external_id": rec.external_id,
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
        "description_html": rec.description_html,
        "description_text": rec.description_text,
        "abstract": rec.abstract,
        "expires_at_utc": rec.expires_at_utc,
        "is_expired": _bool_to_int(rec.is_expired),
        "detail_raw": rec.detail_raw,
    }


# ---------------------------------------------------------------------------
# Job-detail enrichment
# ---------------------------------------------------------------------------
def jobs_needing_detail(
    conn: sqlite3.Connection,
    *,
    source: str | None = None,
    stale_days: int | None = None,
    limit: int | None = None,
) -> list[tuple[str, str]]:
    """Return ``(source, external_id)`` pairs for jobs that have no detail yet,
    or whose detail is older than ``stale_days`` days.

    If ``source`` is given, restrict to that source.
    """
    where = ["(detail_fetched_at IS NULL"]
    args: list = []
    if stale_days is not None:
        cutoff = _now_minus_days(stale_days)
        where.append("OR detail_fetched_at < ?")
        args.append(cutoff)
    where_sql = " ".join(where) + ")"
    if source is not None:
        where_sql += " AND source = ?"
        args.append(source)
    sql = (
        f"SELECT source, external_id FROM job WHERE {where_sql} "
        "ORDER BY first_seen_at DESC"
    )
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
    return [(row["source"], row["external_id"]) for row in conn.execute(sql, args)]


def update_job_detail(
    conn: sqlite3.Connection,
    source: str,
    external_id: str,
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
         WHERE source = :source AND external_id = :external_id
        """,
        {
            "source": source,
            "external_id": external_id,
            "description_html": description_html,
            "description_text": description_text,
            "abstract": abstract,
            "expires_at_utc": expires_at_utc,
            "is_expired": _bool_to_int(is_expired),
            "detail_raw": detail_raw,
            "now": _now_iso(),
        },
    )


def record_detail_error(
    conn: sqlite3.Connection,
    source: str,
    external_id: str,
    error: str,
) -> None:
    """Store the error message for a failed detail fetch.

    We intentionally keep ``detail_fetched_at`` untouched so failed jobs stay
    eligible for retry on the next enrich run.
    """
    conn.execute(
        "UPDATE job SET detail_error = ? WHERE source = ? AND external_id = ?",
        (error[:500], source, external_id),
    )


def _now_minus_days(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

