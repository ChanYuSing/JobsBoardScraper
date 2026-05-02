"""JobsDB-specific DB operations.

Each function operates exclusively on the ``job_jobsdb`` table.
No cross-source code; no shared abstractions.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from .models import JobsDBCard, JobsDBDetail

_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_minus_days(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply the job_jobsdb table schema (idempotent)."""
    conn.executescript(_SCHEMA_SQL)


UpsertOutcome = Literal["inserted", "updated"]


def upsert_card(
    conn: sqlite3.Connection, card: JobsDBCard, run_id: int
) -> UpsertOutcome:
    """Insert a new JobsDB job or refresh an existing one. Returns outcome."""
    now = _now_iso()
    existing = conn.execute(
        "SELECT 1 FROM job_jobsdb WHERE job_id = :job_id",
        {"job_id": card.job_id},
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO job_jobsdb (
                job_id, url, title, company, location,
                classification, subclassification,
                work_types, work_arrangement, salary_label,
                teaser, bullet_points,
                listing_date_utc, listing_date_label,
                first_seen_at, last_seen_at,
                first_seen_run_id, last_seen_run_id,
                raw_card_json
            ) VALUES (
                :job_id, :url, :title, :company, :location,
                :classification, :subclassification,
                :work_types, :work_arrangement, :salary_label,
                :teaser, :bullet_points,
                :listing_date_utc, :listing_date_label,
                :now, :now,
                :run_id, :run_id,
                :raw_card_json
            )
            """,
            {
                "job_id":            card.job_id,
                "url":               card.url,
                "title":             card.title,
                "company":           card.company,
                "location":          card.location,
                "classification":    card.classification,
                "subclassification": card.subclassification,
                "work_types":        card.work_types,
                "work_arrangement":  card.work_arrangement,
                "salary_label":      card.salary_label,
                "teaser":            card.teaser,
                "bullet_points":     card.bullet_points,
                "listing_date_utc":  card.listing_date_utc,
                "listing_date_label": card.listing_date_label,
                "now":               now,
                "run_id":            run_id,
                "raw_card_json":     card.raw_card_json,
            },
        )
        return "inserted"

    conn.execute(
        """
        UPDATE job_jobsdb SET
            url                = :url,
            title              = :title,
            company            = :company,
            location           = :location,
            classification     = COALESCE(:classification,    classification),
            subclassification  = COALESCE(:subclassification, subclassification),
            work_types         = :work_types,
            work_arrangement   = :work_arrangement,
            salary_label       = COALESCE(:salary_label,      salary_label),
            teaser             = :teaser,
            bullet_points      = :bullet_points,
            listing_date_utc   = :listing_date_utc,
            listing_date_label = :listing_date_label,
            last_seen_at       = :now,
            last_seen_run_id   = :run_id,
            raw_card_json      = :raw_card_json
         WHERE job_id = :job_id
        """,
        {
            "job_id":            card.job_id,
            "url":               card.url,
            "title":             card.title,
            "company":           card.company,
            "location":          card.location,
            "classification":    card.classification,
            "subclassification": card.subclassification,
            "work_types":        card.work_types,
            "work_arrangement":  card.work_arrangement,
            "salary_label":      card.salary_label,
            "teaser":            card.teaser,
            "bullet_points":     card.bullet_points,
            "listing_date_utc":  card.listing_date_utc,
            "listing_date_label": card.listing_date_label,
            "now":               now,
            "run_id":            run_id,
            "raw_card_json":     card.raw_card_json,
        },
    )
    return "updated"


def jobs_needing_enrich(
    conn: sqlite3.Connection,
    *,
    stale_days: int | None = None,
    limit: int | None = None,
) -> list[str]:
    """Return job_ids with no detail yet, or whose detail is older than stale_days."""
    where = "(detail_fetched_at IS NULL"
    args: list = []
    if stale_days is not None:
        cutoff = _now_minus_days(stale_days)
        where += " OR detail_fetched_at < ?"
        args.append(cutoff)
    where += ")"
    sql = (
        f"SELECT job_id FROM job_jobsdb WHERE {where} "
        "ORDER BY first_seen_at DESC"
    )
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
    return [row[0] for row in conn.execute(sql, args)]


def upsert_detail(
    conn: sqlite3.Connection,
    job_id: str,
    detail: JobsDBDetail,
) -> None:
    """Write enrichment fields into an existing job_jobsdb row."""
    conn.execute(
        """
        UPDATE job_jobsdb SET
            description_html  = :description_html,
            description_text  = :description_text,
            abstract          = :abstract,
            expires_at_utc    = :expires_at_utc,
            is_expired        = :is_expired,
            detail_fetched_at = :now,
            detail_error      = NULL,
            raw_detail_json   = :raw_detail_json
         WHERE job_id = :job_id
        """,
        {
            "job_id":           job_id,
            "description_html": detail.description_html,
            "description_text": detail.description_text,
            "abstract":         detail.abstract,
            "expires_at_utc":   detail.expires_at_utc,
            "is_expired":       None if detail.is_expired is None else int(detail.is_expired),
            "now":              _now_iso(),
            "raw_detail_json":  detail.raw_detail_json,
        },
    )


def record_detail_error(
    conn: sqlite3.Connection,
    job_id: str,
    error: str,
) -> None:
    """Store the error message for a failed enrich. Keeps detail_fetched_at NULL
    so the job stays eligible for retry on the next enrich run."""
    conn.execute(
        "UPDATE job_jobsdb SET detail_error = ? WHERE job_id = ?",
        (error[:500], job_id),
    )
