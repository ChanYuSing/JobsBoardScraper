-- Shared schema for JobBoardScraper.
-- Source-specific tables live in sources/<name>/schema.sql

-- One row per scheduler-triggered run (fetch or enrich phase).
CREATE TABLE IF NOT EXISTS scheduler_run (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT    NOT NULL,          -- jobsdb | linkedin_guest
    phase        TEXT    NOT NULL,          -- fetch | enrich
    started_at   TEXT,                      -- ISO-8601 UTC; NULL while queued
    finished_at  TEXT,                      -- NULL while in progress
    status       TEXT    NOT NULL,          -- queued | running | ok | error | cancelled
    jobs_found   INTEGER,                   -- progress counter (inserted+updated for fetch; ok count for enrich)
    jobs_total   INTEGER,                   -- total to process (NULL for fetch; set at enrich start)
    error        TEXT,                      -- NULL on success
    scope        TEXT                       -- optional label (e.g. preset name for AI score runs)
);

CREATE INDEX IF NOT EXISTS idx_sched_run_source ON scheduler_run(source);
CREATE INDEX IF NOT EXISTS idx_sched_run_status ON scheduler_run(status);

-- One row per job touched by a scheduler run (fetch / enrich / score).
CREATE TABLE IF NOT EXISTS scheduler_run_job (
    run_id  INTEGER NOT NULL REFERENCES scheduler_run(id) ON DELETE CASCADE,
    source  TEXT    NOT NULL,
    job_id  TEXT    NOT NULL,
    PRIMARY KEY (run_id, source, job_id)
);
CREATE INDEX IF NOT EXISTS idx_srj_run_id ON scheduler_run_job(run_id);

-- User triage: saved / dismissed per job.
CREATE TABLE IF NOT EXISTS job_status (
    source     TEXT NOT NULL,
    job_id     TEXT NOT NULL,
    status     TEXT NOT NULL,      -- 'saved' | 'dismissed'
    marked_at  TEXT NOT NULL,      -- ISO-8601 UTC
    PRIMARY KEY (source, job_id)
);

-- User-defined scoring criteria for AI analysis.
CREATE TABLE IF NOT EXISTS field_def (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,           -- AI JSON key; label auto-derived as title-case
    type        TEXT NOT NULL CHECK(type IN ('int', 'str')),
    description TEXT NOT NULL DEFAULT '',       -- injected into AI prompt
    sort_order  INTEGER NOT NULL DEFAULT 0
);

-- AI per-job scoring results (EAV — one row per job × field).
CREATE TABLE IF NOT EXISTS job_analysis (
    source      TEXT NOT NULL,
    job_id      TEXT NOT NULL,
    field_id    INTEGER NOT NULL REFERENCES field_def(id) ON DELETE CASCADE,
    value_int   INTEGER,
    value_str   TEXT,
    analysed_at TEXT NOT NULL,
    PRIMARY KEY (source, job_id, field_id)
);
CREATE INDEX IF NOT EXISTS idx_job_analysis_src_job   ON job_analysis (source, job_id);
-- Speeds up the scored-count query (GROUP BY job_id filtered by source + field_id):
CREATE INDEX IF NOT EXISTS idx_job_analysis_src_field ON job_analysis (source, field_id);
-- Speeds up the dismissed anti-join (COALESCE filter on job_status):
CREATE INDEX IF NOT EXISTS idx_job_status_src_status  ON job_status (source, status);

-- Denormalised flat table for fast job list queries.
-- Kept in sync with job_jobsdb and job_linkedin via triggers defined in each
-- source schema.  PK matches the source tables' (source, job_id) pair.
CREATE TABLE IF NOT EXISTS job_all (
    source            TEXT NOT NULL,
    job_id            TEXT NOT NULL,
    title             TEXT,
    company           TEXT,
    location          TEXT,
    work_type         TEXT,
    work_arrangement  TEXT,
    salary            TEXT,
    date_posted       TEXT,
    classification    TEXT,
    subclassification TEXT,
    teaser            TEXT,
    description_text  TEXT,
    description_html  TEXT,
    url               TEXT,
    first_seen_at     TEXT,
    detail_fetched_at TEXT,
    PRIMARY KEY (source, job_id)
);

CREATE INDEX IF NOT EXISTS idx_job_all_date_posted      ON job_all(date_posted);
CREATE INDEX IF NOT EXISTS idx_job_all_first_seen       ON job_all(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_job_all_source           ON job_all(source);
CREATE INDEX IF NOT EXISTS idx_job_all_title            ON job_all(title);
CREATE INDEX IF NOT EXISTS idx_job_all_company          ON job_all(company);
CREATE INDEX IF NOT EXISTS idx_job_all_location         ON job_all(location);
CREATE INDEX IF NOT EXISTS idx_job_all_work_type        ON job_all(work_type);
CREATE INDEX IF NOT EXISTS idx_job_all_classification   ON job_all(classification);
CREATE INDEX IF NOT EXISTS idx_job_all_subclassification ON job_all(subclassification);

-- Named filter presets: saved filter configurations for Jobs page and AI scoring scope.
CREATE TABLE IF NOT EXISTS filter_preset (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    params     TEXT NOT NULL,              -- JSON object of all filter params
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
