"""SQLite: connect, init, run lifecycle."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path


# Schema lives next to the package (see [tool.setuptools.package-data] in pyproject.toml).
_SCHEMA_SQL = files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Connect / init
# ---------------------------------------------------------------------------
def connect(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-65536;")  # 64 MB page cache
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply shared schema and per-source schemas.

    Does NOT sweep orphan runs — call sweep_orphan_runs() separately, once
    at process startup, so that in-flight queued rows created by this process
    are not immediately cancelled.
    """
    _migrate_analysis_schema(conn)
    _migrate_scheduler_run(conn)
    _migrate_drop_run_refs(conn)
    _migrate_scope_column(conn)
    conn.executescript(_SCHEMA_SQL)
    from .sources.linkedin import db as li_db
    li_db.init_schema(conn)
    from .sources.jobsdb import db as jdb_db
    jdb_db.init_schema(conn)
    conn.commit()
    _populate_job_all(conn)


def _populate_job_all(conn: sqlite3.Connection) -> None:
    """Backfill job_all from source tables if empty (first run or after reset)."""
    count = conn.execute("SELECT COUNT(*) FROM job_all").fetchone()[0]
    if count > 0:
        return
    conn.execute("""
        INSERT INTO job_all
            (source, job_id, title, company, location, work_type, work_arrangement,
             salary, date_posted, classification, subclassification, teaser,
             description_text, description_html, url, first_seen_at, detail_fetched_at)
        SELECT
            'jobsdb', job_id, title, company, location,
            work_types, work_arrangement, salary_label, listing_date_utc,
            classification, subclassification, teaser,
            description_text, description_html, url, first_seen_at, detail_fetched_at
        FROM job_jobsdb
        UNION ALL
        SELECT
            'linkedin_guest', job_id, title, company, location,
            employment_type, NULL, NULL, date_posted,
            industries, job_function, NULL,
            description_text, description_html, url, first_seen_at, detail_fetched_at
        FROM job_linkedin
    """)
    conn.commit()


def sync_fields_full(conn: sqlite3.Connection, fields: list) -> list[str]:
    """Full sync: upsert all given fields, delete any not present.

    Returns list of deleted field names (those with existing scores are still deleted —
    caller must confirm first).
    ``fields`` is a list of FieldCfg objects.
    """
    incoming_names = {f.name for f in fields}
    existing = conn.execute("SELECT id, name FROM field_def").fetchall()
    deleted = []
    for row in existing:
        if row["name"] not in incoming_names:
            conn.execute("DELETE FROM field_def WHERE id = ?", (row["id"],))
            deleted.append(row["name"])
    for i, f in enumerate(fields):
        conn.execute(
            """
            INSERT INTO field_def (name, type, description, sort_order)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                type        = excluded.type,
                description = excluded.description,
                sort_order  = excluded.sort_order
            """,
            (f.name, f.type, f.description, i),
        )
    conn.commit()
    return deleted


def _migrate_drop_run_refs(conn: sqlite3.Connection) -> None:
    """Drop the old run table and its FK columns from both job tables."""
    # job_jobsdb — rebuild without FK columns if they still exist
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_jobsdb)").fetchall()}
    if "first_seen_run_id" in cols or "last_seen_run_id" in cols:
        conn.executescript("""
            BEGIN;
            CREATE TABLE _job_jobsdb_new (
                job_id              TEXT PRIMARY KEY,
                url                 TEXT,
                title               TEXT NOT NULL,
                company             TEXT,
                location            TEXT,
                classification      TEXT,
                subclassification   TEXT,
                work_types          TEXT,
                work_arrangement    TEXT,
                salary_label        TEXT,
                teaser              TEXT,
                bullet_points       TEXT,
                listing_date_utc    TEXT,
                listing_date_label  TEXT,
                description_html    TEXT,
                description_text    TEXT,
                abstract            TEXT,
                expires_at_utc      TEXT,
                is_expired          INTEGER,
                first_seen_at       TEXT NOT NULL,
                last_seen_at        TEXT NOT NULL,
                detail_fetched_at   TEXT,
                detail_error        TEXT,
                raw_card_json       TEXT,
                raw_detail_json     TEXT
            );
            INSERT INTO _job_jobsdb_new
                SELECT job_id, url, title, company, location,
                       classification, subclassification, work_types, work_arrangement,
                       salary_label, teaser, bullet_points, listing_date_utc,
                       listing_date_label, description_html, description_text,
                       abstract, expires_at_utc, is_expired,
                       first_seen_at, last_seen_at,
                       detail_fetched_at, detail_error,
                       raw_card_json, raw_detail_json
                  FROM job_jobsdb;
            DROP TABLE job_jobsdb;
            ALTER TABLE _job_jobsdb_new RENAME TO job_jobsdb;
            COMMIT;
        """)

    # job_linkedin — rebuild without FK columns if they still exist
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job_linkedin)").fetchall()}
    if "first_seen_run_id" in cols or "last_seen_run_id" in cols:
        conn.executescript("""
            BEGIN;
            CREATE TABLE _job_linkedin_new (
                job_id              TEXT PRIMARY KEY,
                url                 TEXT,
                title               TEXT NOT NULL,
                company             TEXT,
                company_url         TEXT,
                company_logo_url    TEXT,
                location            TEXT,
                date_posted         TEXT,
                benefit_text        TEXT,
                seniority_level     TEXT,
                employment_type     TEXT,
                job_function        TEXT,
                industries          TEXT,
                num_applicants      TEXT,
                description_text    TEXT,
                description_html    TEXT,
                first_seen_at       TEXT NOT NULL,
                last_seen_at        TEXT NOT NULL,
                detail_fetched_at   TEXT,
                detail_error        TEXT,
                raw_card_json       TEXT,
                raw_detail_json     TEXT
            );
            INSERT INTO _job_linkedin_new
                SELECT job_id, url, title, company, company_url, company_logo_url,
                       location, date_posted, benefit_text,
                       seniority_level, employment_type, job_function, industries,
                       num_applicants, description_text, description_html,
                       first_seen_at, last_seen_at,
                       detail_fetched_at, detail_error,
                       raw_card_json, raw_detail_json
                  FROM job_linkedin;
            DROP TABLE job_linkedin;
            ALTER TABLE _job_linkedin_new RENAME TO job_linkedin;
            COMMIT;
        """)

    # Drop the old run table (no longer written to)
    conn.execute("DROP INDEX IF EXISTS idx_run_source")
    conn.execute("DROP TABLE IF EXISTS run")
    conn.commit()


def _migrate_analysis_schema(conn: sqlite3.Connection) -> None:
    """Drop analysis tables that have an outdated schema."""
    ja_cols = {r[1] for r in conn.execute("PRAGMA table_info(job_analysis)").fetchall()}
    fd_cols = {r[1] for r in conn.execute("PRAGMA table_info(field_def)").fetchall()}
    # Drop if job_analysis predates EAV, or if field_def still has the old label column
    if (ja_cols and "field_id" not in ja_cols) or "label" in fd_cols:
        conn.execute("DROP TABLE IF EXISTS job_analysis")
        conn.execute("DROP TABLE IF EXISTS field_def")
        conn.commit()


def _migrate_scheduler_run(conn: sqlite3.Connection) -> None:
    """Fix scheduler_run schema: drop NOT NULL on started_at, add jobs_total."""
    cols_info = conn.execute("PRAGMA table_info(scheduler_run)").fetchall()
    if not cols_info:
        return  # table doesn't exist yet; CREATE TABLE IF NOT EXISTS will handle it

    col_map = {r[1]: r for r in cols_info}  # name -> row

    needs_recreate = False
    # started_at must be nullable (notnull flag = 0)
    if "started_at" in col_map and col_map["started_at"][3] == 1:  # notnull==1
        needs_recreate = True

    if needs_recreate:
        conn.executescript("""
            BEGIN;
            ALTER TABLE scheduler_run RENAME TO _scheduler_run_old;
            CREATE TABLE scheduler_run (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                source       TEXT    NOT NULL,
                phase        TEXT    NOT NULL,
                started_at   TEXT,
                finished_at  TEXT,
                status       TEXT    NOT NULL,
                jobs_found   INTEGER,
                jobs_total   INTEGER,
                error        TEXT,
                scope        TEXT
            );
            INSERT INTO scheduler_run
                (id, source, phase, started_at, finished_at, status, jobs_found, error)
            SELECT id, source, phase, started_at, finished_at, status, jobs_found, error
              FROM _scheduler_run_old;
            DROP TABLE _scheduler_run_old;
            COMMIT;
        """)
        return  # table already has jobs_total from the CREATE above

    # Table structure is fine — just add jobs_total if missing
    if "jobs_total" not in col_map:
        conn.execute("ALTER TABLE scheduler_run ADD COLUMN jobs_total INTEGER")
        conn.commit()


def _migrate_scope_column(conn: sqlite3.Connection) -> None:
    """Add scope column to scheduler_run if it doesn't exist yet."""
    col_map = {r[1] for r in conn.execute("PRAGMA table_info(scheduler_run)").fetchall()}
    if not col_map:
        return  # table doesn't exist yet; CREATE TABLE IF NOT EXISTS will handle it
    if "scope" not in col_map:
        conn.execute("ALTER TABLE scheduler_run ADD COLUMN scope TEXT")
        conn.commit()


def sweep_orphan_runs(conn: sqlite3.Connection) -> None:
    """Mark stale scheduler_run rows from a previous crashed process as error/cancelled.

    Call once at process startup — before any new work is queued.
    """
    now = _now_iso()
    # scheduler_run: running rows → error, queued rows → cancelled
    conn.execute(
        """
        UPDATE scheduler_run
           SET status = 'error',
               finished_at = COALESCE(finished_at, ?),
               error = COALESCE(error, 'process did not finish cleanly')
         WHERE status = 'running'
        """,
        (now,),
    )
    conn.execute(
        """
        UPDATE scheduler_run
           SET status = 'cancelled',
               error = 'server restarted before run started'
         WHERE status = 'queued'
        """,
    )
    conn.commit()


