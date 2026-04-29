"""JobsDB adapter: wraps the SEEK GraphQL client."""
from __future__ import annotations

import json
from typing import Any, Iterator

from ..client import JobsDBClient
from ..detail import parse_job_detail
from ..normalise import JobRecord
from ..url_parser import parse_search_url


def _first(items: list[dict] | None, *path: str) -> Any | None:
    if not items:
        return None
    cur: Any = items[0]
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _normalise(raw: dict[str, Any]) -> JobRecord:
    """Convert one raw JobSearchV6 entry into a ``JobRecord``."""
    job_id = str(raw["id"])
    cls = raw.get("classifications") or []
    cls_desc = _first(cls, "classification", "description")
    sub_desc = _first(cls, "subclassification", "description")

    locs = raw.get("locations") or []
    location = locs[0].get("label") if locs and isinstance(locs[0], dict) else None

    work_arr = raw.get("workArrangements") or {}
    work_arrangement = (
        work_arr.get("displayText") if isinstance(work_arr, dict) else None
    )

    work_types = raw.get("workTypes") or []
    work_types_str = ", ".join(work_types) if work_types else None

    listing = raw.get("listingDate") or {}
    listing_utc = listing.get("dateTimeUtc") if isinstance(listing, dict) else None
    listing_label = listing.get("label") if isinstance(listing, dict) else None

    bullets = raw.get("bulletPoints") or []

    return JobRecord(
        source="jobsdb",
        external_id=job_id,
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


class JobsDBAdapter:
    name = "jobsdb"
    enrich_inline = False  # description requires a separate jobDetails call

    def __init__(self, cfg, scraper_cfg) -> None:
        self._cfg = cfg
        self._params = parse_search_url(cfg.url)
        if cfg.daterange:
            self._params["dateRange"] = cfg.daterange
        self._page_size = cfg.page_size
        self._start_page = max(1, cfg.start_page or 1)
        self._max_pages = cfg.max_pages or None
        self._client = JobsDBClient(
            user_agent=scraper_cfg.user_agent,
            timeout_seconds=scraper_cfg.request_timeout_seconds,
            retries=scraper_cfg.retries,
            jitter_ms=tuple(scraper_cfg.jitter_ms),
        )

    # context manager support so the CLI can use ``with build_adapter(...)``.
    def __enter__(self) -> "JobsDBAdapter":
        self._client.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.__exit__(*exc)

    def search(self) -> Iterator[JobRecord]:
        for _, payload in self._client.iter_all(
            self._params,
            page_size=self._page_size,
            start_page=self._start_page,
            max_pages=self._max_pages,
        ):
            for raw in payload.get("data") or []:
                yield _normalise(raw)

    def search_paginated(self) -> Iterator[tuple[int, list[JobRecord], int | None]]:
        """Yield ``(page_number, records, total_count)`` so the CLI can log
        page-level progress and resume on Cloudflare blocks."""
        for page, payload in self._client.iter_all(
            self._params,
            page_size=self._page_size,
            start_page=self._start_page,
            max_pages=self._max_pages,
        ):
            recs = [_normalise(raw) for raw in (payload.get("data") or [])]
            yield page, recs, payload.get("totalCount")

    def fetch_detail(self, external_id: str) -> dict[str, Any]:
        return self._client.fetch_job_detail(external_id) or {}

    def parse_detail(self, payload: dict[str, Any]):
        return parse_job_detail(payload)

    def sleep_jitter(self) -> None:
        self._client._sleep_jitter()  # noqa: SLF001

