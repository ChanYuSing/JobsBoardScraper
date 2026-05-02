"""Configuration loader. Reads config.yaml into typed pydantic models."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Per-source sections
# ---------------------------------------------------------------------------
class SourceCfgBase(BaseModel):
    """Common to every source section."""
    model_config = ConfigDict(extra="allow")
    enabled: bool = True


class JobsDBSourceCfg(SourceCfgBase):
    keywords: str | list[str] | None = None   # str or list joined with space (AND); null = all jobs
    location: str | None = "Hong Kong SAR"
    daterange: int | None = None               # days old: 1 | 3 | 7 | 14 | 31 | None = all time
    work_arrangement: str | list[str] | None = None  # on-site | hybrid | remote
    work_type: str | list[str] | None = None         # full-time | part-time | contract | casual | internship
    classification: int | list[int] | None = None    # industry ID (e.g. 6281=ICT)
    subclassification: int | list[int] | None = None
    salary_range: str | None = None            # e.g. "30000-60000" (monthly HKD)
    salary_type: str | None = None
    sort_mode: str | None = None               # ListDate = most recent | Relevance = default
    page_size: int = 32
    # CLI overrides land here so the adapter sees a single source of truth.
    start_page: int = 1
    max_pages: int = 0                         # 0 = no cap


class LinkedInCfg(SourceCfgBase):
    location: str = "Hong Kong"
    keywords: list[str] = Field(default_factory=list)
    hours_old: int | None = 720          # f_TPR filter — None disables the filter
    job_type: str | None = None          # fulltime | parttime | contract | internship
    is_remote: bool | str | None = None           # True=remote(f_WT=2) | False=on-site(f_WT=1) | "hybrid"(f_WT=3) | None=all
    experience_level: int | list[int] | None = None  # 1=internship..6=executive; list for multi-level e.g. [2,3,4]
    easy_apply: bool | None = None                   # True = LinkedIn Easy Apply only
    sort_by_date: bool | None = None                 # True = f_SB2=R (most-recent first)
    geo_id: str | None = None                        # LinkedIn numeric geoId (overrides location text for precision)
    industry_id: int | None = None                   # LinkedIn industry code, e.g. 96=technology, 4=software



# ---------------------------------------------------------------------------
# Cross-cutting sections
# ---------------------------------------------------------------------------
class ScraperCfg(BaseModel):
    request_timeout_seconds: int = 20
    retries: int = 3
    jitter_ms: tuple[int, int] = (500, 1500)
    user_agent: str = "Mozilla/5.0"


class StorageCfg(BaseModel):
    sqlite_path: str = "data/jobs.sqlite"


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------
class Config(BaseModel):
    """Multi-source config: one entry per source under ``sources:``."""

    sources: dict[str, Any] = Field(default_factory=dict)
    scraper: ScraperCfg = Field(default_factory=ScraperCfg)
    storage: StorageCfg = Field(default_factory=StorageCfg)

    def enabled_sources(self) -> list[str]:
        return [n for n, s in self.sources.items() if getattr(s, "enabled", True)]


def _coerce_source(name: str, raw: dict[str, Any]):
    if name == "jobsdb":
        return JobsDBSourceCfg.model_validate(raw)
    if name == "linkedin_guest":
        return LinkedInCfg.model_validate(raw)
    return SourceCfgBase.model_validate(raw)


def load_config(path: str | Path = "config.yaml") -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    sources_raw = raw.get("sources") or {}
    sources_typed = {n: _coerce_source(n, s or {}) for n, s in sources_raw.items()}
    raw["sources"] = sources_typed
    return Config.model_validate(raw)
