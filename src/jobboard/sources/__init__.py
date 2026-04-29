"""Source adapters: one per job board (or aggregator like JobSpy)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import SourceAdapter

if TYPE_CHECKING:
    from ..config import Config


# Names that the CLI / config recognises. Keep in sync with schema.sql's
# `source` registry table.
KNOWN_SOURCES = (
    "jobsdb",
    "jobspy_linkedin",
    "jobspy_indeed",
    "jobspy_glassdoor",
    "jobspy_ziprecruiter",
)


def build_adapter(name: str, cfg: "Config") -> SourceAdapter:
    """Instantiate the adapter for ``name`` from the given config."""
    section = cfg.sources.get(name)
    if section is None:
        raise KeyError(f"No config section for source '{name}'")
    if name == "jobsdb":
        from .jobsdb_adapter import JobsDBAdapter
        return JobsDBAdapter(section, cfg.scraper)
    if name.startswith("jobspy_"):
        from .jobspy_adapter import JobSpyAdapter
        site = name[len("jobspy_"):]
        return JobSpyAdapter(name=name, site=site, cfg=section)
    raise KeyError(f"Unknown source '{name}'")


__all__ = ["SourceAdapter", "KNOWN_SOURCES", "build_adapter"]
