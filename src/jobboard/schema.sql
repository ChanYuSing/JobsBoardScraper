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
    error        TEXT                       -- NULL on success
);

CREATE INDEX IF NOT EXISTS idx_sched_run_source ON scheduler_run(source);
CREATE INDEX IF NOT EXISTS idx_sched_run_status ON scheduler_run(status);

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
