"""JobSpy adapter: scrapes LinkedIn / Indeed / Glassdoor / ZipRecruiter via
the ``python-jobspy`` library and converts each row into a ``JobRecord``.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Iterator

from ..normalise import JobRecord

log = logging.getLogger(__name__)


def _clean(v: Any) -> Any | None:
    """Return ``None`` for NaN / empty / pandas-NA-ish values."""
    if v is None:
        return None
    # pandas inserts float NaN for missing cells.
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return v


def _str(v: Any) -> str | None:
    v = _clean(v)
    return None if v is None else str(v)


class JobSpyAdapter:
    """Adapter for one JobSpy site (linkedin / indeed / glassdoor / zip_recruiter).

    JobSpy fetches descriptions inline (esp. with ``linkedin_fetch_description=True``),
    so :attr:`enrich_inline` is ``True`` and the ``enrich`` CLI command skips this
    source. ``fetch_detail`` raises ``NotImplementedError``.
    """

    enrich_inline = True

    # Map our config name to the site key JobSpy expects.
    _SITE_KEY = {
        "linkedin":     "linkedin",
        "indeed":       "indeed",
        "glassdoor":    "glassdoor",
        "ziprecruiter": "zip_recruiter",
    }

    def __init__(self, *, name: str, site: str, cfg) -> None:
        if site not in self._SITE_KEY:
            raise ValueError(f"Unsupported jobspy site: {site}")
        self.name = name
        self._site = self._SITE_KEY[site]
        self._cfg = cfg

    def __enter__(self) -> "JobSpyAdapter":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def search(self) -> Iterator[JobRecord]:
        # Imported lazily so the package still imports without python-jobspy
        # installed (e.g. on machines that only run the JobsDB source).
        from jobspy import scrape_jobs  # type: ignore[import-not-found]

        cfg = self._cfg
        keywords: list[str] = list(cfg.keywords or [])
        if not keywords:
            # Most JobSpy sites need a non-empty term. Use a single empty pass
            # only for sites that tolerate it; warn loudly otherwise.
            log.warning(
                "[%s] no keywords configured; doing a single empty-term search "
                "(may return zero results on LinkedIn).",
                self.name,
            )
            keywords = [""]

        for term in keywords:
            log.info("[%s] scraping site=%s term=%r location=%r results_wanted=%d",
                     self.name, self._site, term, cfg.location, cfg.results_wanted)
            kwargs: dict[str, Any] = {
                "site_name": [self._site],
                "search_term": term or None,
                "location": cfg.location,
                "results_wanted": cfg.results_wanted,
                "hours_old": cfg.hours_old,
                "linkedin_fetch_description": cfg.fetch_description,
            }
            # JobSpy needs an explicit country for Indeed/Glassdoor.
            if self._site in ("indeed", "glassdoor"):
                kwargs["country_indeed"] = cfg.country or "Hong Kong"
            try:
                df = scrape_jobs(**kwargs)
            except Exception as exc:  # noqa: BLE001
                log.error("[%s] scrape_jobs failed for term=%r: %s",
                          self.name, term, exc)
                continue

            if df is None or len(df) == 0:
                log.info("[%s] term=%r: 0 jobs", self.name, term)
                continue

            for raw in df.to_dict(orient="records"):
                rec = self._row_to_record(raw)
                if rec is not None:
                    yield rec

    def fetch_detail(self, external_id: str) -> dict[str, Any]:
        raise NotImplementedError(
            f"{self.name}: descriptions are fetched inline during search()"
        )

    def parse_detail(self, payload: dict[str, Any]):
        raise NotImplementedError(
            f"{self.name}: descriptions are fetched inline during search()"
        )

    # ------------------------------------------------------------------
    # Row -> JobRecord
    # ------------------------------------------------------------------
    def _row_to_record(self, raw: dict[str, Any]) -> JobRecord | None:
        ext_id = _str(raw.get("id")) or _str(raw.get("job_url"))
        if not ext_id:
            return None
        title = _str(raw.get("title")) or ""
        if not title:
            return None

        salary = _build_salary_label(raw)
        work_arrangement = _str(raw.get("location_type")) or _str(raw.get("job_type"))
        is_remote = raw.get("is_remote")
        if is_remote and not work_arrangement:
            work_arrangement = "Remote"
        listing_utc = _str(raw.get("date_posted"))

        # JobSpy returns description as plain text (possibly HTML for some sites).
        description = _str(raw.get("description"))

        return JobRecord(
            source=self.name,
            external_id=ext_id,
            title=title,
            company=_str(raw.get("company")),
            location=_str(raw.get("location")),
            classification=_str(raw.get("job_function")),
            subclassification=_str(raw.get("job_level")),
            work_types=_str(raw.get("job_type")),
            work_arrangement=work_arrangement,
            salary_label=salary,
            teaser=None,
            bullet_points_json="[]",
            listing_date_utc=listing_utc,
            listing_date_label=None,
            url=_str(raw.get("job_url")) or _str(raw.get("job_url_direct")),
            raw_json=json.dumps(_serialisable(raw), ensure_ascii=False, default=str),
            description_html=None,  # JobSpy returns text; leave HTML empty.
            description_text=description,
            abstract=None,
            expires_at_utc=None,
            is_expired=None,
            detail_raw=None,
        )


def _build_salary_label(raw: dict[str, Any]) -> str | None:
    lo = _clean(raw.get("min_amount"))
    hi = _clean(raw.get("max_amount"))
    cur = _clean(raw.get("currency"))
    interval = _clean(raw.get("interval"))
    if lo is None and hi is None:
        return None
    parts: list[str] = []
    if cur:
        parts.append(str(cur))
    if lo is not None and hi is not None and lo != hi:
        parts.append(f"{int(lo):,} - {int(hi):,}")
    else:
        v = lo if lo is not None else hi
        parts.append(f"{int(v):,}")
    if interval:
        parts.append(f"/ {interval}")
    return " ".join(parts)


def _serialisable(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop pandas NaNs so the JSON dump stays compact and round-trippable."""
    out: dict[str, Any] = {}
    for k, v in raw.items():
        cv = _clean(v)
        if cv is not None:
            out[k] = cv
    return out
