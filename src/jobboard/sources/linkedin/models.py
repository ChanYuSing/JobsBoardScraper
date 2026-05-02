"""LinkedIn-specific data models.

Frozen dataclasses: immutable by construction, safe to pass across layers.
No shared supertype — these fields are exactly what the LinkedIn guest API
returns, nothing more, nothing less.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LinkedInCard:
    """One search-result card from the LinkedIn guest search API.

    Populated during ``jobboard fetch --source linkedin_guest``.
    """

    job_id: str
    title: str
    company: str | None
    company_url: str | None
    company_logo_url: str | None
    location: str | None
    date_posted: str | None          # ISO-8601 date from <time datetime="...">
    url: str | None
    benefit_text: str | None         # e.g. "Be an early applicant" (~50% present)
    raw_card_json: str               # full JSON of the raw card payload


@dataclass(frozen=True)
class LinkedInDetail:
    """Enriched fields from the LinkedIn guest job-posting detail page.

    Populated during ``jobboard enrich --source linkedin_guest``.
    """

    seniority_level: str | None
    employment_type: str | None
    job_function: str | None
    industries: str | None
    num_applicants: str | None       # free-text e.g. "Be among the first 25 applicants"
    company_url: str | None          # more reliable than card version
    company_logo_url: str | None
    description_html: str | None
    description_text: str | None
    raw_detail_json: str             # full JSON of the raw detail payload
