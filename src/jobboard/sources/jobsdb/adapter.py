"""JobsDB adapter: wraps the SEEK GraphQL API.

Phase 1 — fetch
    POST /graphql  (jobSearchV6) — paginated card data.
    Cloudflare blocks are surfaced as CloudflareBlockedError so the CLI can
    record the last good page and suggest a ``--start-page`` resume command.
    Yields one JobsDBCard per search result.

Phase 2 — enrich
    POST /graphql  (jobDetails) — full description per job ID.
    Triggered by ``jobboard enrich --source jobsdb``.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
import random
import time
from html.parser import HTMLParser
from typing import Any, Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .models import JobsDBCard, JobsDBDetail
from .url_parser import parse_search_url
from .queries import JOB_SEARCH_V6, JOB_DETAILS

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://hk.jobsdb.com/graphql"

# Defaults the SEEK backend expects but that aren't user-meaningful.
_DEFAULT_INCLUDE       = ["seoData", "gptTargeting", "relatedSearches"]
_DEFAULT_QUERY_HINTS   = ["spellingCorrection"]


# ---------------------------------------------------------------------------
# Exceptions (re-exported so cli.py can catch them without reaching into client)
# ---------------------------------------------------------------------------

class CloudflareBlockedError(RuntimeError):
    """Cloudflare challenge/block (HTTP 403 + challenge body)."""


class TransientServerError(RuntimeError):
    """Retryable upstream error (HTTP 5xx, gateway timeouts)."""


class RateLimitedError(RuntimeError):
    """SEEK's GraphQL replied with RATE_LIMITED (HTTP 200 + errors body)."""


def _looks_like_cloudflare_block(status: int, body: str, headers) -> bool:
    if status != 403:
        return False
    if "cf-mitigated" in {k.lower() for k in headers.keys()}:
        return True
    snippet = body[:1000].lower()
    return "just a moment" in snippet or "challenges.cloudflare.com" in snippet


# ---------------------------------------------------------------------------
# HTML → plain text (needed for description_text)
# ---------------------------------------------------------------------------

class _Stripper(HTMLParser):
    BLOCK_TAGS = {
        "p", "div", "br", "li", "ul", "ol",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "tr", "td", "th", "section", "article",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag.lower() == "li":
            self._chunks.append("\n- ")
        elif tag.lower() in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def text(self) -> str:
        joined = "".join(self._chunks)
        joined = re.sub(r"[ \t]+", " ", joined)
        joined = re.sub(r"\n[ \t]+", "\n", joined)
        joined = re.sub(r"\n{3,}", "\n\n", joined)
        return joined.strip()


def _html_to_text(html: str) -> str:
    p = _Stripper()
    p.feed(html)
    p.close()
    return p.text()


# ---------------------------------------------------------------------------
# Internal GraphQL client (self-contained — no top-level client.py dependency)
# ---------------------------------------------------------------------------

class _Client:
    def __init__(
        self,
        user_agent: str,
        timeout_seconds: int,
        retries: int,
        jitter_ms: tuple[int, int],
    ) -> None:
        self._jitter_ms = jitter_ms
        self._session_id = str(uuid.uuid4())
        self._visitor_id = str(uuid.uuid4())
        self._sol_id     = str(uuid.uuid4())
        self._http = httpx.Client(
            timeout=timeout_seconds,
            headers={
                "User-Agent":            user_agent,
                "Accept":                "*/*",
                "Accept-Language":       "en-US,en;q=0.9",
                "Content-Type":          "application/json",
                "Origin":                "https://hk.jobsdb.com",
                "Referer":               "https://hk.jobsdb.com/",
                "seek-request-brand":    "jobsdb",
                "seek-request-country":  "HK",
                "x-seek-site":           "chalice",
                "x-custom-features":     "application/features.seek.all+json",
                "x-seek-ec-sessionid":   self._session_id,
                "x-seek-ec-visitorid":   self._visitor_id,
            },
        )
        self._post = retry(
            reraise=True,
            stop=stop_after_attempt(retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(
                (httpx.HTTPError, httpx.TimeoutException, TransientServerError)
            ),
        )(self._post_raw)

    def __enter__(self) -> "_Client":
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.close()

    def _post_raw(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._http.post(GRAPHQL_URL, json=payload)
        if r.status_code >= 400:
            if _looks_like_cloudflare_block(r.status_code, r.text, r.headers):
                raise CloudflareBlockedError(
                    f"Cloudflare challenge (HTTP {r.status_code}); back off and retry later."
                )
            if r.status_code in (500, 502, 503, 504):
                raise TransientServerError(
                    f"HTTP {r.status_code}: transient upstream error"
                )
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        if "errors" in data:
            errs = data["errors"]
            codes = {
                (e.get("extensions") or {}).get("code")
                for e in errs if isinstance(e, dict)
            }
            if "RATE_LIMITED" in codes:
                raise RateLimitedError(f"GraphQL RATE_LIMITED: {errs}")
            raise RuntimeError(f"GraphQL errors: {errs}")
        return data

    def search_page(self, params: dict[str, Any], page: int) -> dict[str, Any]:
        page_params: dict[str, Any] = {
            "include":                  _DEFAULT_INCLUDE,
            "queryHints":               _DEFAULT_QUERY_HINTS,
            "relatedSearchesCount":     12,
            "eventCaptureSessionId":    self._session_id,
            "eventCaptureUserId":       self._visitor_id,
            "userSessionId":            self._session_id,
            "solId":                    self._sol_id,
            "userQueryId":              str(uuid.uuid4()),
            **params,
            "page": page,
        }
        payload = {
            "operationName": "JobSearchV6",
            "query": JOB_SEARCH_V6,
            "variables": {
                "params": page_params,
                "locale": params.get("locale", "en-HK"),
                "timezone": "Asia/Hong_Kong",
            },
        }
        log.debug("POST JobSearchV6 page=%d", page)
        return self._post(payload)["data"]["jobSearchV6"]

    def iter_all(
        self,
        params: dict[str, Any],
        page_size: int,
        start_page: int,
        max_pages: int | None,
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        """Yield (page_number, jobSearchV6_payload) for every page."""
        if start_page < 1:
            raise ValueError("start_page must be >= 1")
        params = {**params, "pageSize": page_size}
        first = self.search_page(params, start_page)
        yield start_page, first
        yielded = 1
        if len(first.get("data") or []) < page_size:
            return
        if max_pages is not None and yielded >= max_pages:
            return
        total = int(first.get("totalCount") or 0)
        last_page = max(start_page, -(-total // page_size))  # ceil div
        for page in range(start_page + 1, last_page + 1):
            self.sleep_jitter()
            payload = self.search_page(params, page)
            yield page, payload
            yielded += 1
            if len(payload.get("data") or []) < page_size:
                return
            if max_pages is not None and yielded >= max_pages:
                return

    def fetch_job_detail(self, job_id: str) -> dict[str, Any]:
        variables = {
            "jobId":                          str(job_id),
            "jobDetailsViewedCorrelationId":  str(uuid.uuid4()),
            "sessionId":                      self._session_id,
            "zone":                           "asia-1",
            "locale":                         "en-HK",
            "languageCode":                   "en",
            "countryCode":                    "HK",
            "timezone":                       "Asia/Hong_Kong",
            "visitorId":                      self._visitor_id,
            "isAuthenticated":                False,
            "enableJdvBadge":                 True,
        }
        payload = {
            "operationName": "jobDetails",
            "query": JOB_DETAILS,
            "variables": variables,
        }
        log.debug("POST jobDetails id=%s", job_id)
        return self._post(payload)["data"]["jobDetails"]

    def sleep_jitter(self) -> None:
        lo, hi = self._jitter_ms
        time.sleep(random.uniform(lo, hi) / 1000.0)


# ---------------------------------------------------------------------------
# Card normalisation helper
# ---------------------------------------------------------------------------

def _first(items: list[dict] | None, *path: str) -> Any | None:
    if not items:
        return None
    cur: Any = items[0]
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _normalise_card(raw: dict[str, Any]) -> JobsDBCard:
    job_id = str(raw["id"])
    cls    = raw.get("classifications") or []
    locs   = raw.get("locations") or []
    listing = raw.get("listingDate") or {}
    work_arr = raw.get("workArrangements") or {}
    work_types = raw.get("workTypes") or []
    bullets = raw.get("bulletPoints") or []

    return JobsDBCard(
        job_id=job_id,
        url=f"https://hk.jobsdb.com/job/{job_id}",
        title=raw.get("title") or "",
        company=raw.get("companyName"),
        location=locs[0].get("label") if locs and isinstance(locs[0], dict) else None,
        classification=_first(cls, "classification", "description"),
        subclassification=_first(cls, "subclassification", "description"),
        work_types=", ".join(work_types) if work_types else None,
        work_arrangement=work_arr.get("displayText") if isinstance(work_arr, dict) else None,
        salary_label=raw.get("salaryLabel") or None,
        teaser=raw.get("teaser"),
        bullet_points=json.dumps(bullets, ensure_ascii=False),
        listing_date_utc=listing.get("dateTimeUtc") if isinstance(listing, dict) else None,
        listing_date_label=listing.get("label") if isinstance(listing, dict) else None,
        raw_card_json=json.dumps(raw, ensure_ascii=False),
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class JobsDBAdapter:
    name = "jobsdb"
    enrich_inline = False  # description requires a separate jobDetails call

    def __init__(self, cfg, scraper_cfg) -> None:
        self._cfg = cfg
        self._params = parse_search_url(cfg.url)
        if cfg.daterange:
            self._params["dateRange"] = cfg.daterange
        self._page_size  = cfg.page_size
        self._start_page = max(1, cfg.start_page or 1)
        self._max_pages  = cfg.max_pages or None
        self._client = _Client(
            user_agent=scraper_cfg.user_agent,
            timeout_seconds=scraper_cfg.request_timeout_seconds,
            retries=scraper_cfg.retries,
            jitter_ms=tuple(scraper_cfg.jitter_ms),
        )

    def __enter__(self) -> "JobsDBAdapter":
        self._client.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.__exit__(*exc)

    def sleep_jitter(self) -> None:
        self._client.sleep_jitter()

    # ------------------------------------------------------------------
    # Phase 1 — search
    # ------------------------------------------------------------------

    def search(self) -> Iterator[JobsDBCard]:
        for _, payload in self._client.iter_all(
            self._params, self._page_size, self._start_page, self._max_pages
        ):
            for raw in payload.get("data") or []:
                yield _normalise_card(raw)

    def search_paginated(
        self,
    ) -> Iterator[tuple[int, list[JobsDBCard], int | None]]:
        """Yield (page_number, cards, total_count) for CLI progress logging."""
        for page, payload in self._client.iter_all(
            self._params, self._page_size, self._start_page, self._max_pages
        ):
            cards = [_normalise_card(raw) for raw in (payload.get("data") or [])]
            yield page, cards, payload.get("totalCount")

    # ------------------------------------------------------------------
    # Phase 2 — enrich
    # ------------------------------------------------------------------

    def fetch_detail(self, job_id: str) -> dict[str, Any]:
        return self._client.fetch_job_detail(job_id) or {}

    def parse_detail(self, payload: dict[str, Any]) -> JobsDBDetail:
        job = (payload or {}).get("job") or {}
        html = job.get("content")
        expires_at = job.get("expiresAt") or {}
        return JobsDBDetail(
            description_html=html,
            description_text=_html_to_text(html) if html else None,
            abstract=job.get("abstract"),
            expires_at_utc=(
                expires_at.get("dateTimeUtc")
                if isinstance(expires_at, dict) else None
            ),
            is_expired=job.get("isExpired"),
            raw_detail_json=json.dumps(payload, ensure_ascii=False),
        )
