"""Jobs service — unified query across job_jobsdb + job_linkedin."""
from __future__ import annotations

import re
import sqlite3
from typing import Any

# Ordered display columns (label → normalised name)
ALL_DISPLAY_COLS: list[tuple[str, str]] = [
    ("Title",           "title"),
    ("Company",         "company"),
    ("Location",        "location"),
    ("Work type",       "work_type"),
    ("Date posted",     "date_posted"),
    ("Source",          "source"),
    ("Classification",  "classification"),
    ("Subcat.",         "subclassification"),
    ("URL",             "url"),
    ("First seen",      "first_seen_at"),
]
DEFAULT_COLS = ["title", "company", "location", "work_type", "date_posted", "source"]



_filter_opts_cache: dict | None = None


def invalidate_filter_options_cache() -> None:
    """Clear the filter options cache. Call after any scrape that may add new values."""
    global _filter_opts_cache
    _filter_opts_cache = None


def get_filter_options(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Return distinct non-null values for each filterable column, for dropdown population."""
    global _filter_opts_cache
    if _filter_opts_cache is not None:
        return _filter_opts_cache

    def _distinct(sql: str) -> list[str]:
        rows = conn.execute(sql).fetchall()
        return sorted({r[0] for r in rows if r[0]})

    def _split_distinct(sql: str) -> list[str]:
        """Split comma-separated values, normalize hyphens/spaces, deduplicate."""
        rows = conn.execute(sql).fetchall()
        seen: dict[str, str] = {}   # normalized_key → display_value
        for (raw,) in rows:
            if raw:
                for part in str(raw).split(","):
                    clean = re.sub(r'^and\s+', '', part.strip(), flags=re.IGNORECASE)
                    if clean:
                        # normalise: collapse hyphen/space variants case-insensitively
                        key = clean.lower().replace("-", " ").replace("  ", " ")
                        if key not in seen:
                            seen[key] = clean  # keep first real capitalisation
        return sorted(seen.values(), key=str.casefold)

    result = {
        "source":         _distinct("SELECT DISTINCT source FROM job_all"),
        "work_type":      _split_distinct("SELECT DISTINCT work_type FROM job_all"),
        "classification": _split_distinct("SELECT DISTINCT classification FROM job_all"),
    }
    _filter_opts_cache = result
    return result


SORTABLE: set[str] = {
    "title", "company", "location", "work_type",
    "date_posted", "source", "classification", "subclassification", "first_seen_at",
}


def list_jobs(
    conn: sqlite3.Connection,
    *,
    keyword: str = "",
    source: str = "",
    date_from: str = "",
    date_to: str = "",
    work_type: list[str] | None = None,
    company: str = "",
    location_kw: str = "",
    classification: str = "",
    subclassification_kw: str = "",
    first_seen_from: str = "",
    first_seen_to: str = "",
    triage: str = "",        # "saved" | "dismissed" | "new" | ""
    sort_by: str = "",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 50,
    score_field_names: set[str] | None = None,
    score_filters: dict[str, int] | None = None,
    run_id: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return (rows, total_count)."""
    where, params = [], []

    if source:
        where.append("j.source = ?")
        params.append(source)
    if date_from:
        where.append("j.date_posted >= ?")
        params.append(date_from)
    if date_to:
        where.append("j.date_posted <= ?")
        params.append(date_to)
    if work_type:
        clauses = " OR ".join("LOWER(j.work_type) LIKE ?" for _ in work_type)
        where.append(f"({clauses})")
        params.extend(f"%{wt.lower()}%" for wt in work_type)
    if company:
        where.append("LOWER(j.company) LIKE ?")
        params.append(f"%{company.lower()}%")
    if location_kw:
        where.append("LOWER(j.location) LIKE ?")
        params.append(f"%{location_kw.lower()}%")
    if classification:
        where.append("LOWER(COALESCE(j.classification,'')) LIKE ?")
        params.append(f"%{classification.lower()}%")
    if subclassification_kw:
        where.append("LOWER(COALESCE(j.subclassification,'')) LIKE ?")
        params.append(f"%{subclassification_kw.lower()}%")
    if first_seen_from:
        where.append("date(j.first_seen_at) >= ?")
        params.append(first_seen_from)
    if first_seen_to:
        where.append("date(j.first_seen_at) <= ?")
        params.append(first_seen_to)
    if keyword:
        where.append("LOWER(j.title) LIKE ?")
        params.append(f"%{keyword.lower()}%")
    if triage == "saved":
        where.append("js.status = 'saved'")
    elif triage == "dismissed":
        where.append("js.status = 'dismissed'")
    elif triage == "new":
        where.append("js.status IS NULL")
    if score_filters:
        for fname, min_val in score_filters.items():
            where.append(
                "EXISTS (SELECT 1 FROM job_analysis ja"
                " JOIN field_def fd ON fd.id = ja.field_id"
                " WHERE ja.source = j.source AND ja.job_id = j.job_id"
                " AND fd.name = ? AND ja.value_int >= ?)"
            )
            params.extend([fname, min_val])

    clause = "WHERE " + " AND ".join(where) if where else ""

    run_join = (
        "JOIN scheduler_run_job srj"
        " ON srj.source = j.source AND srj.job_id = j.job_id AND srj.run_id = ?"
        if run_id else ""
    )
    run_params: list = [run_id] if run_id else []

    sort_by_score = bool(sort_by and score_field_names and sort_by in score_field_names)

    count_base = f"""
        FROM job_all j
        LEFT JOIN job_status js ON js.source = j.source AND js.job_id = j.job_id
        {run_join}
        {clause}
    """
    total = conn.execute(f"SELECT COUNT(*) {count_base}", run_params + params).fetchone()[0]

    offset = (page - 1) * page_size
    if sort_by_score:
        data_base = f"""
            FROM job_all j
            LEFT JOIN job_status js ON js.source = j.source AND js.job_id = j.job_id
            {run_join}
            LEFT JOIN (
                SELECT ja.source, ja.job_id, ja.value_int AS _score_sort
                FROM job_analysis ja
                JOIN field_def fd ON fd.id = ja.field_id
                WHERE fd.name = ?
            ) ss ON ss.source = j.source AND ss.job_id = j.job_id
            {clause}
        """
        order_clause = f"ss._score_sort {'ASC' if sort_dir == 'asc' else 'DESC'} NULLS LAST"
        data_params = [sort_by] + run_params + params + [page_size, offset]
    else:
        data_base = count_base
        order_clause = (
            f"j.{sort_by} {'ASC' if sort_dir == 'asc' else 'DESC'}"
            if sort_by in SORTABLE
            else "j.date_posted DESC, j.first_seen_at DESC"
        )
        data_params = run_params + params + [page_size, offset]

    rows = conn.execute(
        f"""
        SELECT j.*, COALESCE(js.status, 'new') AS triage_status
        {data_base}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
        """,
        data_params,
    ).fetchall()

    return [dict(r) for r in rows], total


def get_job(conn: sqlite3.Connection, source: str, job_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"""
        SELECT j.*, COALESCE(js.status, 'new') AS triage_status
        FROM job_all j
        LEFT JOIN job_status js ON js.source = j.source AND js.job_id = j.job_id
        WHERE j.source = ? AND j.job_id = ?
        """,
        [source, job_id],
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def get_job_scores(
    conn: sqlite3.Connection,
    jobs: list[dict[str, Any]],
    field_defs: list[dict[str, Any]],
) -> None:
    """Merge job_analysis score values into each job dict in-place."""
    if not jobs or not field_defs:
        return
    job_ids = list({j["job_id"] for j in jobs})
    placeholders = ",".join("?" * len(job_ids))
    rows = conn.execute(
        f"""
        SELECT ja.source, ja.job_id, fd.name, fd.type, ja.value_int, ja.value_str
        FROM job_analysis ja
        JOIN field_def fd ON fd.id = ja.field_id
        WHERE ja.job_id IN ({placeholders})
        """,
        job_ids,
    ).fetchall()
    scores: dict[tuple, dict] = {}
    for r in rows:
        key = (r["source"], r["job_id"])
        if key not in scores:
            scores[key] = {}
        scores[key][r["name"]] = r["value_int"] if r["type"] == "int" else r["value_str"]
    for job in jobs:
        key = (job["source"], job["job_id"])
        if key in scores:
            job.update(scores[key])


_VALID_STATUSES = {"saved", "dismissed", "new"}


def mark_job(conn: sqlite3.Connection, source: str, job_id: str, status: str) -> None:
    """Set triage status ('saved'|'dismissed'); 'new' removes the row."""
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}': must be saved, dismissed, or new.")
    if status == "new":
        conn.execute(
            "DELETE FROM job_status WHERE source = ? AND job_id = ?",
            [source, job_id],
        )
    else:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO job_status(source, job_id, status, marked_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source, job_id) DO UPDATE SET status=excluded.status, marked_at=excluded.marked_at
            """,
            [source, job_id, status, now],
        )
    conn.commit()
