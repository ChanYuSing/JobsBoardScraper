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
    url: str
    page_size: int = 32
    # CLI overrides land here so the adapter sees a single source of truth.
    daterange: int = 0           # 0 = use whatever the URL specifies
    start_page: int = 1
    max_pages: int = 0           # 0 = no cap


class JobSpySourceCfg(SourceCfgBase):
    location: str = "Hong Kong"
    keywords: list[str] = Field(default_factory=list)
    hours_old: int = 72
    results_wanted: int = 200
    fetch_description: bool = True
    country: str | None = "Hong Kong"  # used by indeed/glassdoor


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
    if name.startswith("jobspy_"):
        return JobSpySourceCfg.model_validate(raw)
    return SourceCfgBase.model_validate(raw)


def load_config(path: str | Path = "config.yaml") -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    sources_raw = raw.get("sources") or {}
    sources_typed = {n: _coerce_source(n, s or {}) for n, s in sources_raw.items()}
    raw["sources"] = sources_typed
    return Config.model_validate(raw)
