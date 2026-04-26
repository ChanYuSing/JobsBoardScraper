"""Configuration loader. Reads config.yaml; env vars (JOBBOARD_*) can override."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class SearchCfg(BaseModel):
    url: str
    page_size: int = 32


class ScraperCfg(BaseModel):
    request_timeout_seconds: int = 20
    retries: int = 3
    jitter_ms: tuple[int, int] = (500, 1500)
    user_agent: str = "Mozilla/5.0"


class StorageCfg(BaseModel):
    sqlite_path: str = "data/jobs.sqlite"


class Config(BaseModel):
    search: SearchCfg
    scraper: ScraperCfg = Field(default_factory=ScraperCfg)
    storage: StorageCfg = Field(default_factory=StorageCfg)


def load_config(path: str | Path = "config.yaml") -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Config.model_validate(raw)
