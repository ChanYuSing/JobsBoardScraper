"""JobsDB-specific data models.

Frozen dataclasses: immutable by construction, safe to pass across layers.
Fields correspond exactly to what JobsDB GraphQL returns — nothing invented,
nothing shared with LinkedIn.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JobsDBCard:
    """One search-result card from the JobsDB GraphQL jobSearchV6 query.

    Populated during ``jobboard fetch --source jobsdb``.
    """

    job_id: str
    url: str
    title: str
    company: str | None
    location: str | None
    classification: str | None      # e.g. "Information & Communication Technology"
    subclassification: str | None   # e.g. "Developers/Programmers"
    work_types: str | None          # comma-separated e.g. "Full time"
    work_arrangement: str | None    # "Remote" | "Hybrid" | "On-site"
    salary_label: str | None        # e.g. "HK$40,000 – HK$65,000 per month"
    teaser: str | None
    bullet_points: str              # JSON-encoded list[str]
    listing_date_utc: str | None
    listing_date_label: str | None  # "6d ago" etc.
    raw_card_json: str              # full JSON of the raw GraphQL entry


@dataclass(frozen=True)
class JobsDBDetail:
    """Enriched fields from the JobsDB GraphQL jobDetails query.

    Populated during ``jobboard enrich --source jobsdb``.
    """

    description_html: str | None
    description_text: str | None
    abstract: str | None
    expires_at_utc: str | None
    is_expired: bool | None
    raw_detail_json: str            # full JSON of the raw jobDetails payload
