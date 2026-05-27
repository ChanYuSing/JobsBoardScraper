"""Configuration loader. Reads config.yaml into typed pydantic models."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Shared lock used by all services that read-modify-write config.yaml.
# A single lock object ensures concurrent writes from different services
# are serialised correctly.
config_write_lock = threading.Lock()


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
    hours_old: int | None = 720
    job_type: str | None = None
    is_remote: bool | str | None = None
    experience_level: int | list[int] | None = None
    easy_apply: bool | None = None
    sort_by_date: bool | None = None
    geo_id: str | None = None
    industry_id: int | list[int] | None = None
    job_function_id: str | list[str] | None = None

    @field_validator("keywords", mode="before")
    @classmethod
    def coerce_keywords(cls, v: object) -> list[str]:
        """Accept plain string from YAML (e.g. 'machine learning') and coerce to list."""
        if v is None:
            return []
        if isinstance(v, str):
            parts = [p.strip() for p in v.replace("\r", "").replace("\n", ",").split(",") if p.strip()]
            return parts
        return v  # already a list



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


class SchedulerCfg(BaseModel):
    enabled: bool = False            # set True to activate cron; False = paused
    cron: str | None = None          # single cron for all enabled sources e.g. "0 1 * * *"
    order: list[str] | None = None   # explicit run order; null = use config.yaml source order


class FieldCfg(BaseModel):
    name: str
    type: str = "int"
    description: str = ""


class AiCfg(BaseModel):
    provider: str = "ollama"     # openai | ollama | lmstudio | grok | gemini | openai_compat
    model: str = "llama3.2"
    base_url: str = ""           # empty = use provider default (ollama: http://localhost:11434/v1)
    api_key: str = ""            # fallback if AI_API_KEY env var not set
    api_keys: dict[str, str] = Field(default_factory=dict)   # per-provider keys
    models: dict[str, str] = Field(default_factory=dict)     # per-provider last-used model
    base_urls: dict[str, str] = Field(default_factory=dict)  # per-provider last-used base_url
    temperature: float | None = None       # None = provider default
    max_tokens: int | None = None          # None = provider default
    reasoning_effort: str | None = None   # None | "low" | "medium" | "high" | "max"
    thinking_enabled: bool | None = None  # None = provider default; True/False = force on/off
    provider_params: dict[str, dict] = Field(default_factory=dict)  # per-provider param overrides
    system_prompt: str = ""
    cv: str = ""
    auto_score: bool = False
    auto_score_preset_names: list[str] = Field(default_factory=list)
    fields: list[FieldCfg] = Field(default_factory=list)
    prompt_fields: list[str] = Field(default_factory=lambda: [
        "title", "company", "location", "description_text", "bullet_points",
        "work_types", "work_arrangement", "salary_label",
        "listing_date_label", "date_posted", "employment_type", "seniority_level",
    ])


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------
class Config(BaseModel):
    """Multi-source config: one entry per source under ``sources:``."""

    sources: dict[str, SourceCfgBase] = Field(default_factory=dict)
    scraper: ScraperCfg = Field(default_factory=ScraperCfg)
    storage: StorageCfg = Field(default_factory=StorageCfg)
    scheduler: SchedulerCfg = Field(default_factory=SchedulerCfg)
    ai: AiCfg = Field(default_factory=AiCfg)

    def enabled_sources(self) -> list[str]:
        return [n for n, s in self.sources.items() if s.enabled]


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
