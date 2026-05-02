"""Source adapters: one per job board."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Config


KNOWN_SOURCES = (
    "jobsdb",
    "linkedin_guest",
)


def build_adapter(name: str, cfg: "Config"):
    """Instantiate the adapter for ``name`` from the given config."""
    section = cfg.sources.get(name)
    if section is None:
        raise KeyError(f"No config section for source '{name}'")
    if name == "jobsdb":
        from .jobsdb.adapter import JobsDBAdapter
        return JobsDBAdapter(section, cfg.scraper)
    if name == "linkedin_guest":
        from .linkedin.adapter import LinkedInAdapter
        return LinkedInAdapter(section, cfg.scraper)
    raise KeyError(f"Unknown source '{name}'")


__all__ = ["KNOWN_SOURCES", "build_adapter"]
