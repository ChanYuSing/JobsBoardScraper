"""GraphQL query strings for the SEEK/JobsDB API.

Queries are shipped verbatim from the captured HAR; the SEEK backend rejects
trimmed-down queries with UNSTABLE_QUERY_ERROR.
"""
from __future__ import annotations

from pathlib import Path

JOB_SEARCH_V6 = (Path(__file__).with_name("job_search_v6.graphql")).read_text(encoding="utf-8")
JOB_DETAILS   = (Path(__file__).with_name("job_details.graphql")).read_text(encoding="utf-8")
