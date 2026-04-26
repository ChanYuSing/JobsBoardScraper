"""HTTP client for the JobsDB GraphQL endpoint."""
from __future__ import annotations

import logging
import random
import time
import uuid
from typing import Any, Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .queries import JOB_SEARCH_V6, JOB_DETAILS

GRAPHQL_URL = "https://hk.jobsdb.com/graphql"

# Defaults the SEEK backend expects but that aren't user-meaningful.
DEFAULT_INCLUDE = ["seoData", "gptTargeting", "relatedSearches"]
DEFAULT_QUERY_HINTS = ["spellingCorrection"]

log = logging.getLogger(__name__)


class CloudflareBlockedError(RuntimeError):
    """Raised when Cloudflare returns a challenge/block (HTTP 403 + challenge body)."""


class TransientServerError(RuntimeError):
    """Raised on retryable upstream errors (HTTP 5xx, gateway timeouts)."""


class RateLimitedError(RuntimeError):
    """Raised when SEEK's GraphQL replies with RATE_LIMITED (HTTP 200 + errors body)."""


def _looks_like_cloudflare_block(status: int, body: str, headers) -> bool:
    if status != 403:
        return False
    if "cf-mitigated" in {k.lower() for k in headers.keys()}:
        return True
    snippet = body[:1000].lower()
    return "just a moment" in snippet or "challenges.cloudflare.com" in snippet


class JobsDBClient:
    def __init__(
        self,
        user_agent: str,
        timeout_seconds: int = 20,
        retries: int = 3,
        jitter_ms: tuple[int, int] = (500, 1500),
    ) -> None:
        self._jitter_ms = jitter_ms
        # Stable per-instance ids — SEEK requires them in headers and params.
        # Real browsers use distinct visitor (long-lived) and session (per-tab) ids.
        self._session_id = str(uuid.uuid4())
        self._visitor_id = str(uuid.uuid4())
        self._sol_id = str(uuid.uuid4())
        self._client = httpx.Client(
            timeout=timeout_seconds,
            headers={
                "User-Agent": user_agent,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Content-Type": "application/json",
                "Origin": "https://hk.jobsdb.com",
                "Referer": "https://hk.jobsdb.com/",
                "seek-request-brand": "jobsdb",
                "seek-request-country": "HK",
                "x-seek-site": "chalice",
                "x-custom-features": "application/features.seek.all+json",
                "x-seek-ec-sessionid": self._session_id,
                "x-seek-ec-visitorid": self._visitor_id,
            },
        )
        # apply retry decorator dynamically so retries count is configurable.
        # Retries httpx network errors AND transient upstream 5xx; never
        # retries CloudflareBlockedError (we want to fail fast and resume).
        self._post = retry(
            reraise=True,
            stop=stop_after_attempt(retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(
                (httpx.HTTPError, httpx.TimeoutException, TransientServerError)
            ),
        )(self._post_raw)

    def __enter__(self) -> "JobsDBClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def _post_raw(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._client.post(GRAPHQL_URL, json=payload)
        if r.status_code >= 400:
            if _looks_like_cloudflare_block(r.status_code, r.text, r.headers):
                raise CloudflareBlockedError(
                    f"Cloudflare challenge (HTTP {r.status_code}); back off and retry later."
                )
            if r.status_code in (500, 502, 503, 504):
                raise TransientServerError(
                    f"HTTP {r.status_code} from {GRAPHQL_URL}: transient upstream error"
                )
            raise RuntimeError(
                f"HTTP {r.status_code} from {GRAPHQL_URL}: {r.text[:1000]}"
            )
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
            # SEEK-required defaults. User-supplied params override these.
            "include": DEFAULT_INCLUDE,
            "queryHints": DEFAULT_QUERY_HINTS,
            "relatedSearchesCount": 12,
            "eventCaptureSessionId": self._session_id,
            "eventCaptureUserId": self._visitor_id,
            "userSessionId": self._session_id,
            "solId": self._sol_id,
            "userQueryId": str(uuid.uuid4()),
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
        page_size: int = 32,
        *,
        start_page: int = 1,
        max_pages: int | None = None,
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        """Yield (page_number, jobSearchV6_payload) for every page.

        Stops as soon as either the computed last page is reached *or* a page
        returns fewer than ``page_size`` jobs (whichever comes first), so we're
        resilient to a stale ``totalCount``.

        ``start_page`` resumes pagination from the given 1-based page number
        (useful after a Cloudflare block). ``max_pages`` caps how many pages
        this iteration yields (useful to take a controlled chunk per session).
        """
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
        last_page = max(start_page, -(-total // page_size))  # ceil
        for page in range(start_page + 1, last_page + 1):
            self._sleep_jitter()
            payload = self.search_page(params, page)
            yield page, payload
            yielded += 1
            if len(payload.get("data") or []) < page_size:
                return
            if max_pages is not None and yielded >= max_pages:
                return

    # ------------------------------------------------------------------
    # Job-detail call
    # ------------------------------------------------------------------
    def fetch_job_detail(self, job_id: str) -> dict[str, Any]:
        """Return the full ``jobDetails`` payload for one job."""
        variables = {
            "jobId": str(job_id),
            "jobDetailsViewedCorrelationId": str(uuid.uuid4()),
            "sessionId": self._session_id,
            "zone": "asia-1",
            "locale": "en-HK",
            "languageCode": "en",
            "countryCode": "HK",
            "timezone": "Asia/Hong_Kong",
            "visitorId": self._visitor_id,
            "isAuthenticated": False,
            "enableJdvBadge": True,
        }
        payload = {
            "operationName": "jobDetails",
            "query": JOB_DETAILS,
            "variables": variables,
        }
        log.debug("POST jobDetails id=%s", job_id)
        return self._post(payload)["data"]["jobDetails"]

    def _sleep_jitter(self) -> None:
        lo, hi = self._jitter_ms
        time.sleep(random.uniform(lo, hi) / 1000.0)
