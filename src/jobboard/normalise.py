"""Convert a raw JobSearchV6 job dict into a flat record suitable for SQLite."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JobRecord:
    id: str
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


def _first(items: list[dict] | None, *path: str) -> Any | None:
    if not items:
        return None
    cur: Any = items[0]
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def normalise(raw: dict[str, Any]) -> JobRecord:
    job_id = str(raw["id"])
    cls = raw.get("classifications") or []
    cls_desc = _first(cls, "classification", "description")
    sub_desc = _first(cls, "subclassification", "description")

    locs = raw.get("locations") or []
    location = locs[0].get("label") if locs and isinstance(locs[0], dict) else None

    work_arr = raw.get("workArrangements") or {}
    work_arrangement = work_arr.get("displayText") if isinstance(work_arr, dict) else None

    work_types = raw.get("workTypes") or []
    work_types_str = ", ".join(work_types) if work_types else None

    listing = raw.get("listingDate") or {}
    listing_utc = listing.get("dateTimeUtc") if isinstance(listing, dict) else None
    listing_label = listing.get("label") if isinstance(listing, dict) else None

    bullets = raw.get("bulletPoints") or []

    return JobRecord(
        id=job_id,
        title=raw.get("title") or "",
        company=raw.get("companyName"),
        location=location,
        classification=cls_desc,
        subclassification=sub_desc,
        work_types=work_types_str,
        work_arrangement=work_arrangement,
        salary_label=(raw.get("salaryLabel") or None),
        teaser=raw.get("teaser"),
        bullet_points_json=json.dumps(bullets, ensure_ascii=False),
        listing_date_utc=listing_utc,
        listing_date_label=listing_label,
        url=f"https://hk.jobsdb.com/job/{job_id}",
        raw_json=json.dumps(raw, ensure_ascii=False),
    )
