"""Jobs service — unified query across job_jobsdb + job_linkedin."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

# Ordered display columns (label → normalised name)
ALL_DISPLAY_COLS: list[tuple[str, str]] = [
    ("Title",           "title"),
    ("Company",         "company"),
    ("Location",        "location"),
    ("Work type",       "work_type"),
    ("Arrangement",     "work_arrangement"),
    ("Salary",          "salary"),
    ("Date posted",     "date_posted"),
    ("Source",          "source"),
    ("Classification",  "classification"),
    ("Subcat.",         "subclassification"),
    ("Teaser",          "teaser"),
    ("URL",             "url"),
    ("First seen",      "first_seen_at"),
]
DEFAULT_COLS = ["title", "company", "location", "work_type", "salary", "date_posted", "source"]


def _union_sql() -> str:
    return """
    SELECT
        job_id, title, company, location,
        work_types      AS work_type,
        work_arrangement,
        salary_label    AS salary,
        listing_date_utc AS date_posted,
        classification,
        subclassification,
        teaser,
        description_text,
        description_html,
        url,
        first_seen_at,
        detail_fetched_at,
        'jobsdb'        AS source
    FROM job_jobsdb

    UNION ALL

    SELECT
        job_id, title, company, location,
        employment_type AS work_type,
        NULL            AS work_arrangement,
        NULL            AS salary,
        date_posted,
        NULL            AS classification,
        NULL            AS subclassification,
        NULL            AS teaser,
        description_text,
        description_html,
        url,
        first_seen_at,
        detail_fetched_at,
        'linkedin_guest' AS source
    FROM job_linkedin
    """


def get_filter_options(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Return distinct non-null values for each filterable column, for dropdown population."""
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
                    clean = part.strip()
                    if clean:
                        # normalise: collapse hyphen/space variants case-insensitively
                        key = clean.lower().replace("-", " ").replace("  ", " ")
                        if key not in seen:
                            seen[key] = clean  # keep first real capitalisation
        return sorted(seen.values(), key=str.casefold)

    union = _union_sql()
    return {
        "source":         _distinct(f"SELECT DISTINCT source FROM ({union})"),
        "work_type":      _split_distinct(f"SELECT DISTINCT work_type FROM ({union})"),
        "arrangement":    _distinct(f"SELECT DISTINCT work_arrangement FROM ({union})"),
        "classification": _distinct(f"SELECT DISTINCT classification FROM ({union})"),
    }


SORTABLE: set[str] = {
    "title", "company", "location", "work_type", "salary",
    "date_posted", "source", "classification", "first_seen_at",
}


def list_jobs(
    conn: sqlite3.Connection,
    *,
    keyword: str = "",
    source: str = "",
    date_from: str = "",
    date_to: str = "",
    work_type: list[str] | None = None,
    arrangement: str = "",
    company: str = "",
    location_kw: str = "",
    classification: str = "",
    triage: str = "",        # "saved" | "dismissed" | "new" | ""
    sort_by: str = "",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 50,
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
    if arrangement:
        where.append("LOWER(j.work_arrangement) LIKE ?")
        params.append(f"%{arrangement.lower()}%")
    if company:
        where.append("LOWER(j.company) LIKE ?")
        params.append(f"%{company.lower()}%")
    if location_kw:
        where.append("LOWER(j.location) LIKE ?")
        params.append(f"%{location_kw.lower()}%")
    if classification:
        where.append("LOWER(COALESCE(j.classification,'')) LIKE ?")
        params.append(f"%{classification.lower()}%")
    if keyword:
        where.append("(LOWER(j.title) LIKE ? OR LOWER(j.company) LIKE ? OR LOWER(COALESCE(j.description_text,'')) LIKE ?)")
        kw = f"%{keyword.lower()}%"
        params.extend([kw, kw, kw])
    if triage == "saved":
        where.append("js.status = 'saved'")
    elif triage == "dismissed":
        where.append("js.status = 'dismissed'")
    elif triage == "new":
        where.append("js.status IS NULL")

    clause = "WHERE " + " AND ".join(where) if where else ""

    base = f"""
        FROM ({_union_sql()}) j
        LEFT JOIN job_status js ON js.source = j.source AND js.job_id = j.job_id
        {clause}
    """
    total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]

    offset = (page - 1) * page_size
    rows = conn.execute(
        f"""
        SELECT j.*, COALESCE(js.status, 'new') AS triage_status
        {base}
        ORDER BY {f'j.{sort_by} {"ASC" if sort_dir == "asc" else "DESC"}' if sort_by in SORTABLE else 'j.date_posted DESC, j.first_seen_at DESC'}
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    ).fetchall()

    return [dict(r) for r in rows], total


def get_job(conn: sqlite3.Connection, source: str, job_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"""
        SELECT j.*, COALESCE(js.status, 'new') AS triage_status
        FROM ({_union_sql()}) j
        LEFT JOIN job_status js ON js.source = j.source AND js.job_id = j.job_id
        WHERE j.source = ? AND j.job_id = ?
        """,
        [source, job_id],
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def mark_job(conn: sqlite3.Connection, source: str, job_id: str, status: str) -> None:
    """Set triage status ('saved'|'dismissed'); 'new' removes the row."""
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
