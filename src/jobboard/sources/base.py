"""Common interface every source adapter implements."""
from __future__ import annotations

from typing import Any, Iterator, Protocol, runtime_checkable

from ..normalise import JobRecord


@runtime_checkable
class SourceAdapter(Protocol):
    """Contract for one job source.

    Attributes
    ----------
    name : str
        Stable identifier matching ``schema.sql``'s ``source`` registry
        (e.g. ``"jobsdb"``, ``"jobspy_linkedin"``).
    enrich_inline : bool
        ``True`` if :meth:`search` already populates description fields on the
        ``JobRecord``. The ``enrich`` CLI command skips such sources.
    """

    name: str
    enrich_inline: bool

    def search(self) -> Iterator[JobRecord]:
        """Yield every job for one search run, normalised into ``JobRecord``."""
        ...

    def fetch_detail(self, external_id: str) -> dict[str, Any]:
        """Fetch raw detail payload. Adapters with ``enrich_inline=True`` may
        raise :class:`NotImplementedError`."""
        ...

    def parse_detail(self, payload: dict[str, Any]) -> Any:
        """Normalise the raw detail payload into a ``JobDetail`` instance.
        Same exemption as :meth:`fetch_detail` for inline-detail adapters."""
        ...
