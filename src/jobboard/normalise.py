"""The cross-source ``JobRecord`` dataclass.

Every source adapter normalises its raw payload into this shape. Source-
specific normalisers live next to their adapter (e.g. ``sources/jobsdb_adapter``).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JobRecord:
    # Identity (composite PK in storage).
    source: str
    external_id: str
    # Core search-result fields.
    title: str
    company: str | None
    location: str | None
    classification: str | None
    subclassification: str | None
    work_types: str | None
    work_arrangement: str | None
    salary_label: str | None
    teaser: str | None
    bullet_points_json: str  # JSON-encoded list[str]
    listing_date_utc: str | None
    listing_date_label: str | None
    url: str | None
    raw_json: str  # full JSON of the raw entry
    # Optional inline-detail fields. Populated only by adapters that fetch the
    # full description during search (e.g. JobSpy with linkedin_fetch_description).
    description_html: str | None = None
    description_text: str | None = None
    abstract: str | None = None
    expires_at_utc: str | None = None
    is_expired: bool | None = None
    detail_raw: str | None = None

