"""Extract description fields from a ``jobDetails`` GraphQL payload.

The payload shape (interesting bits):
    data.jobDetails = {
        "job": {
            "abstract": str | None,
            "content": str (HTML),
            "isExpired": bool,
            "expiresAt": {"dateTimeUtc": str} | None,
            ...
        },
        ...
    }
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any


@dataclass(frozen=True)
class JobDetail:
    description_html: str | None
    description_text: str | None
    abstract: str | None
    expires_at_utc: str | None
    is_expired: bool | None
    raw_json: str


def parse_job_detail(payload: dict[str, Any]) -> JobDetail:
    """Take the value at ``data.jobDetails`` and extract description fields."""
    job = (payload or {}).get("job") or {}
    html = job.get("content")
    expires_at = job.get("expiresAt") or {}
    return JobDetail(
        description_html=html,
        description_text=html_to_text(html) if html else None,
        abstract=job.get("abstract"),
        expires_at_utc=expires_at.get("dateTimeUtc") if isinstance(expires_at, dict) else None,
        is_expired=job.get("isExpired"),
        raw_json=json.dumps(payload, ensure_ascii=False),
    )


# ---------------------------------------------------------------------------
# Tiny HTML -> plain text. Good enough for LLM ingestion; not bulletproof.
# ---------------------------------------------------------------------------
class _Stripper(HTMLParser):
    BLOCK_TAGS = {
        "p", "div", "br", "li", "ul", "ol",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "tr", "td", "th", "section", "article",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs):  # noqa: ANN001
        if tag.lower() == "li":
            self._chunks.append("\n- ")
        elif tag.lower() in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def text(self) -> str:
        joined = "".join(self._chunks)
        # collapse runs of whitespace / blank lines
        joined = re.sub(r"[ \t]+", " ", joined)
        joined = re.sub(r"\n[ \t]+", "\n", joined)
        joined = re.sub(r"\n{3,}", "\n\n", joined)
        return joined.strip()


def html_to_text(html: str) -> str:
    p = _Stripper()
    p.feed(html)
    p.close()
    return p.text()
