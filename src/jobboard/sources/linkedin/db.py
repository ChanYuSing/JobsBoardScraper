"""LinkedIn-specific DB operations.

Each function operates exclusively on the ``job_linkedin`` table.
No cross-source code; no shared abstractions.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from .models import LinkedInCard, LinkedInDetail

_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_minus_days(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply the job_linkedin table schema (idempotent)."""
    conn.executescript(_SCHEMA_SQL)


UpsertOutcome = Literal["inserted", "updated"]


def upsert_card(
    conn: sqlite3.Connection, card: LinkedInCard, run_id: int
) -> UpsertOutcome:
    """Insert a new LinkedIn job or refresh an existing one. Returns outcome."""
    now = _now_iso()
    existing = conn.execute(
        "SELECT 1 FROM job_linkedin WHERE job_id = :job_id",
        {"job_id": card.job_id},
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO job_linkedin (
                job_id, url, title, company, company_url, company_logo_url,
                location, date_posted, benefit_text,
                first_seen_at, last_seen_at,
                first_seen_run_id, last_seen_run_id,
                raw_card_json
            ) VALUES (
                :job_id, :url, :title, :company, :company_url, :company_logo_url,
                :location, :date_posted, :benefit_text,
                :now, :now,
                :run_id, :run_id,
                :raw_card_json
            )
            """,
            {
                "job_id":           card.job_id,
                "url":              card.url,
                "title":            card.title,
                "company":          card.company,
                "company_url":      card.company_url,
                "company_logo_url": card.company_logo_url,
                "location":         card.location,
                "date_posted":      card.date_posted,
                "benefit_text":     card.benefit_text,
                "now":              now,
                "run_id":           run_id,
                "raw_card_json":    card.raw_card_json,
            },
        )
        return "inserted"

    conn.execute(
        """
        UPDATE job_linkedin SET
            url              = :url,
            title            = :title,
            company          = :company,
            company_url      = COALESCE(:company_url,      company_url),
            company_logo_url = COALESCE(:company_logo_url, company_logo_url),
            location         = :location,
            date_posted      = :date_posted,
            benefit_text     = COALESCE(:benefit_text,     benefit_text),
            last_seen_at     = :now,
            last_seen_run_id = :run_id,
            raw_card_json    = :raw_card_json
         WHERE job_id = :job_id
        """,
        {
            "job_id":           card.job_id,
            "url":              card.url,
            "title":            card.title,
            "company":          card.company,
            "company_url":      card.company_url,
            "company_logo_url": card.company_logo_url,
            "location":         card.location,
            "date_posted":      card.date_posted,
            "benefit_text":     card.benefit_text,
            "now":              now,
            "run_id":           run_id,
            "raw_card_json":    card.raw_card_json,
        },
    )
    return "updated"


def jobs_needing_enrich(
    conn: sqlite3.Connection,
    *,
    stale_days: int | None = None,
    limit: int | None = None,
) -> list[str]:
    """Return job_ids that have no detail yet, or whose detail is older than stale_days."""
    where = "(detail_fetched_at IS NULL"
    args: list = []
    if stale_days is not None:
        cutoff = _now_minus_days(stale_days)
        where += " OR detail_fetched_at < ?"
        args.append(cutoff)
    where += ")"
    sql = (
        f"SELECT job_id FROM job_linkedin WHERE {where} "
        "ORDER BY first_seen_at DESC"
    )
    if limit is not None:
        sql += " LIMIT ?"
        args.append(limit)
    return [row[0] for row in conn.execute(sql, args)]


def upsert_detail(
    conn: sqlite3.Connection,
    job_id: str,
    detail: LinkedInDetail,
) -> None:
    """Write enrichment fields into an existing job_linkedin row."""
    conn.execute(
        """
        UPDATE job_linkedin SET
            seniority_level   = :seniority_level,
            employment_type   = :employment_type,
            job_function      = :job_function,
            industries        = :industries,
            num_applicants    = :num_applicants,
            company_url       = COALESCE(:company_url,      company_url),
            company_logo_url  = COALESCE(:company_logo_url, company_logo_url),
            description_html  = :description_html,
            description_text  = :description_text,
            detail_fetched_at = :now,
            detail_error      = NULL,
            raw_detail_json   = :raw_detail_json
         WHERE job_id = :job_id
        """,
        {
            "job_id":           job_id,
            "seniority_level":  detail.seniority_level,
            "employment_type":  detail.employment_type,
            "job_function":     detail.job_function,
            "industries":       detail.industries,
            "num_applicants":   detail.num_applicants,
            "company_url":      detail.company_url,
            "company_logo_url": detail.company_logo_url,
            "description_html": detail.description_html,
            "description_text": detail.description_text,
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
        "UPDATE job_linkedin SET detail_error = ? WHERE job_id = ?",
        (error[:500], job_id),
    )
