"""LinkedIn guest adapter.

Phase 1 — fetch
    GET /jobs-guest/jobs/api/seeMoreJobPostings/search
    Paginate start=0, 10, 20 … until empty page or HTTP 400.
    Hard ceiling: start >= 1000 returns HTTP 400.
    Yields one LinkedInCard per card (basic fields only, no description).

Phase 2 — enrich
    GET /jobs-guest/jobs/api/jobPosting/{job_id}
    Returns seniority, employment type, job function, industries, full description.
    Triggered by ``jobboard enrich --source linkedin_guest``.
"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Iterator

import httpx
from bs4 import BeautifulSoup

from .models import LinkedInCard, LinkedInDetail

log = logging.getLogger(__name__)

_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
_PAGE_SIZE   = 10          # LinkedIn always returns exactly 10 cards per call
_DELAY       = (2.0, 3.5)  # random seconds between calls
_RETRY_WAIT  = 30          # seconds to wait after a 429
_MAX_429     = 3           # give up after this many consecutive 429s


def _headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept-Language": "en-US,en;q=0.9",
    }


class LinkedInAdapter:
    """No-auth LinkedIn adapter using the public guest endpoints."""

    name = "linkedin_guest"
    enrich_inline = False  # search() gives card-only data; enrich command fetches details

    def __init__(self, cfg, scraper_cfg) -> None:
        self._cfg = cfg
        self._ua = scraper_cfg.user_agent
        self._timeout = scraper_cfg.request_timeout_seconds
        self._client = httpx.Client(timeout=self._timeout, follow_redirects=True)

    def __enter__(self) -> "LinkedInAdapter":
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def sleep_jitter(self) -> None:
        """Rate-limiting pause between enrich requests."""
        time.sleep(random.uniform(*_DELAY))

    # ------------------------------------------------------------------
    # Phase 1 — search
    # ------------------------------------------------------------------

    def search(self) -> Iterator[LinkedInCard]:
        cfg = self._cfg
        keywords: list[str] = list(cfg.keywords or [])
        if not keywords:
            keywords = [""]

        for term in keywords:
            log.info("[%s] searching term=%r location=%r", self.name, term, cfg.location)
            yield from self._search_term(term)

    def _search_term(self, term: str) -> Iterator[LinkedInCard]:
        cfg = self._cfg
        params: dict[str, Any] = {"location": cfg.location, "start": 0}

        if term:
            params["keywords"] = term
        if cfg.hours_old:
            params["f_TPR"] = f"r{cfg.hours_old * 3600}"
        if cfg.job_type:
            _JT = {
                "fulltime": "F", "parttime": "P", "contract": "C",
                "temporary": "T", "temp": "T",
                "volunteer": "V",
                "internship": "I",
                "other": "O",
            }
            code = _JT.get(cfg.job_type.lower())
            if code:
                params["f_JT"] = code
        if cfg.is_remote is True:
            params["f_WT"] = "2"
        elif cfg.is_remote is False:
            params["f_WT"] = "1"
        elif isinstance(cfg.is_remote, str) and cfg.is_remote.lower() == "hybrid":
            params["f_WT"] = "3"
        lvl = getattr(cfg, "experience_level", None)
        if lvl is not None:
            params["f_E"] = (
                ",".join(str(x) for x in lvl) if isinstance(lvl, list) else str(lvl)
            )
        if getattr(cfg, "easy_apply", None) is True:
            params["f_EA"] = "true"
        if getattr(cfg, "sort_by_date", None) is True:
            params["f_SB2"] = "R"
        geo = getattr(cfg, "geo_id", None)
        if geo:
            params["geoId"] = str(geo)
        ind = getattr(cfg, "industry_id", None)
        if ind is not None:
            params["f_I"] = (
                ",".join(str(x) for x in ind) if isinstance(ind, list) else str(ind)
            )
        fn = getattr(cfg, "job_function_id", None)
        if fn is not None:
            params["f_F"] = (
                ",".join(fn) if isinstance(fn, list) else fn
            )

        fetched = 0
        consecutive_empty = 0
        client = self._client
        while True:
            params["start"] = fetched
            r = self._get_with_retry(client, _SEARCH_URL, params)
            if r is None:
                break

            cards = BeautifulSoup(r.text, "html.parser").find_all(
                "div", class_="base-search-card"
            )
            got = len(cards)
            log.info("[%s] term=%r start=%d: %d cards", self.name, term, fetched, got)

            if got == 0:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    break
                # Wait and retry once before giving up on this term.
                time.sleep(random.uniform(*_DELAY))
                continue

            consecutive_empty = 0
            for card in cards:
                rec = self._parse_card(card)
                if rec is not None:
                    yield rec

            fetched += got
            time.sleep(random.uniform(*_DELAY))

        log.info("[%s] term=%r: %d total cards fetched", self.name, term, fetched)

    def _get_with_retry(
        self, client: httpx.Client, url: str, params: dict
    ) -> httpx.Response | None:
        for attempt in range(_MAX_429 + 1):
            try:
                r = client.get(url, params=params, headers=_headers(self._ua))
            except httpx.HTTPError as exc:
                log.error("[%s] request failed: %s", self.name, exc)
                return None
            if r.status_code == 429:
                if attempt < _MAX_429:
                    log.warning(
                        "[%s] 429 rate-limited — waiting %ds (attempt %d/%d)",
                        self.name, _RETRY_WAIT, attempt + 1, _MAX_429,
                    )
                    time.sleep(_RETRY_WAIT)
                    continue
                log.error("[%s] 429 retries exhausted — stopping", self.name)
                return None
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as exc:
                log.error("[%s] HTTP %d: %s", self.name, r.status_code, exc)
                return None
            return r

    def _parse_card(self, card) -> LinkedInCard | None:
        urn = card.get("data-entity-urn", "")
        job_id = urn.split(":")[-1] if urn else None
        if not job_id:
            return None

        title_tag    = card.find("h3",   class_="base-search-card__title")
        company_tag  = card.find("h4",   class_="base-search-card__subtitle")
        location_tag = card.find("span", class_="job-search-card__location")
        date_tag     = card.find("time")
        url_tag      = card.find("a",    class_="base-card__full-link")
        co_link_tag  = card.find("a",    class_="hidden-nested-link")
        logo_tag     = card.find("img",  class_="artdeco-entity-image")
        benefit_tag  = card.find("span", class_="job-posting-benefits__text")

        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            return None

        raw = {
            "job_id":           job_id,
            "title":            title,
            "company":          company_tag.get_text(strip=True) if company_tag else None,
            "location":         location_tag.get_text(strip=True) if location_tag else None,
            "date_posted":      date_tag.get("datetime") if date_tag else None,
            "url":              url_tag.get("href") if url_tag else None,
            "company_url":      co_link_tag.get("href") if co_link_tag else None,
            "company_logo_url": logo_tag.get("data-delayed-url") if logo_tag else None,
            "benefit_text":     benefit_tag.get_text(strip=True) if benefit_tag else None,
        }

        return LinkedInCard(
            job_id=job_id,
            title=title,
            company=raw["company"],
            company_url=raw["company_url"],
            company_logo_url=raw["company_logo_url"],
            location=raw["location"],
            date_posted=raw["date_posted"],
            url=raw["url"],
            benefit_text=raw["benefit_text"],
            raw_card_json=json.dumps(raw, ensure_ascii=False),
        )

    # ------------------------------------------------------------------
    # Phase 2 — enrich
    # ------------------------------------------------------------------

    def fetch_detail(self, job_id: str) -> dict[str, Any]:
        """Fetch the detail page for a single job. Returns raw payload dict."""
        url = _DETAIL_URL.format(job_id=job_id)
        r = self._client.get(url, headers=_headers(self._ua))
        r.raise_for_status()
        return {"job_id": job_id, "html": r.text}

    def parse_detail(self, payload: dict[str, Any]) -> LinkedInDetail:
        """Parse a raw detail payload into a LinkedInDetail."""
        html_body = payload.get("html", "")
        soup = BeautifulSoup(html_body, "html.parser")

        # Structured criteria block: Seniority level, Employment type, etc.
        criteria: dict[str, str] = {}
        for li in soup.find_all("li", class_="description__job-criteria-item"):
            label = li.find("h3")
            value = li.find("span")
            if label and value:
                criteria[label.get_text(strip=True)] = value.get_text(strip=True)

        # Full job description
        desc_div = soup.find("div", class_="description__text")
        desc_html = str(desc_div) if desc_div else None
        desc_text = desc_div.get_text(separator="\n", strip=True) if desc_div else None

        # Applicant count (e.g. "Be among the first 25 applicants")
        num_tag = soup.find("figcaption", class_="num-applicants__caption")
        num_applicants = num_tag.get_text(strip=True) if num_tag else None

        # Company link and logo
        co_link = soup.find("a", class_="topcard__org-name-link")
        company_url = co_link.get("href") if co_link else None
        logo_tag = soup.find("img", class_="artdeco-entity-image")
        company_logo_url = logo_tag.get("data-delayed-url") if logo_tag else None

        raw = {
            "job_id":           payload.get("job_id"),
            "seniority_level":  criteria.get("Seniority level"),
            "employment_type":  criteria.get("Employment type"),
            "job_function":     criteria.get("Job function"),
            "industries":       criteria.get("Industries"),
            "num_applicants":   num_applicants,
            "company_url":      company_url,
            "company_logo_url": company_logo_url,
        }

        return LinkedInDetail(
            seniority_level=criteria.get("Seniority level"),
            employment_type=criteria.get("Employment type"),
            job_function=criteria.get("Job function"),
            industries=criteria.get("Industries"),
            num_applicants=num_applicants,
            company_url=company_url,
            company_logo_url=company_logo_url,
            description_html=desc_html,
            description_text=desc_text,
            raw_detail_json=json.dumps(raw, ensure_ascii=False),
        )
