"""
Parse a JobsDB search URL into the GraphQL `params` dict used by JobSearchV6.

Supported URL shapes (examples):
    https://hk.jobsdb.com/jobs-in-information-communication-technology/in-Hong-Kong-SAR/hybrid?daterange=14&worktype=242,245
    https://hk.jobsdb.com/jobs?keywords=python&worktype=242
    https://hk.jobsdb.com/jobs/remote?daterange=14

Path segments recognised:
    jobs-in-<classification-slug>     -> classification (slug -> id via small map; unknown slugs are kept as label only)
    in-<location-slug>                -> where (humanised string)
    hybrid | remote | on-site         -> workArrangement
    /remote (as last segment)         -> workArrangement = ["3"]

Query params recognised:
    keywords, daterange, worktype (csv), workarrangement (csv),
    salaryrange (e.g. "30000-60000"), salarytype, classification (csv),
    subclassification (csv), sortmode, page
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

# Known classification slugs -> ids. Extend as we encounter more.
# (We only *need* this for filters that came in via the URL path.
#  The site also accepts ?classification=<id> directly, which we pass through.)
CLASSIFICATION_SLUG_TO_ID: dict[str, int] = {
    "information-communication-technology": 6281,
}

ARRANGEMENT_SLUG_TO_ID: dict[str, str] = {
    "on-site": "1",
    "onsite": "1",
    "hybrid": "2",
    "remote": "3",
}


def parse_search_url(url: str) -> dict[str, Any]:
    """Return the GraphQL `variables.params` dict for JobSearchV6."""
    parsed = urlparse(url)
    params: dict[str, Any] = {
        "siteKey": "HK",
        "locale": "en-HK",
        "channel": "web",
        "source": "FE_SERP",
    }

    # ---- path ----
    segments = [s for s in parsed.path.split("/") if s]
    for seg in segments:
        seg_l = seg.lower()
        if seg_l == "jobs":
            continue
        if seg_l.startswith("jobs-in-"):
            slug = seg_l[len("jobs-in-"):]
            cid = CLASSIFICATION_SLUG_TO_ID.get(slug)
            if cid is not None:
                params["classification"] = [cid]
            # else: leave it; user can pass ?classification=<id> instead
            continue
        if seg_l.startswith("in-"):
            # "in-Hong-Kong-SAR" -> "Hong Kong SAR"
            params["where"] = unquote(seg[len("in-"):]).replace("-", " ")
            continue
        if seg_l in ARRANGEMENT_SLUG_TO_ID:
            params["workArrangement"] = [ARRANGEMENT_SLUG_TO_ID[seg_l]]
            continue

    # ---- query ----
    q = parse_qs(parsed.query, keep_blank_values=False)

    def _csv_list(name: str) -> list[str] | None:
        if name not in q:
            return None
        # query string can be ?worktype=242,245 or ?worktype=242&worktype=245
        out: list[str] = []
        for v in q[name]:
            out.extend([x for x in v.split(",") if x])
        return out or None

    if (kw := q.get("keywords")):
        params["keywords"] = kw[0]
    if (dr := q.get("daterange")):
        try:
            params["dateRange"] = int(dr[0])
        except ValueError:
            pass
    if (wt := _csv_list("worktype")):
        params["workType"] = wt
    if (wa := _csv_list("workarrangement")):
        params["workArrangement"] = wa
    if (cls := _csv_list("classification")):
        try:
            params["classification"] = [int(x) for x in cls]
        except ValueError:
            pass
    if (sub := _csv_list("subclassification")):
        try:
            params["subclassification"] = [int(x) for x in sub]
        except ValueError:
            pass
    if (sr := q.get("salaryrange")):
        params["salaryRange"] = sr[0]
    if (st := q.get("salarytype")):
        params["salaryType"] = st[0]
    if (sm := q.get("sortmode")):
        params["sortMode"] = sm[0]

    return params
