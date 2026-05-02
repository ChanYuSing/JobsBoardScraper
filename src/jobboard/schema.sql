-- Shared schema for JobBoardScraper.
-- Source-specific tables live in sources/<name>/schema.sql

-- One row per scraper run (audit + lifecycle reference).
CREATE TABLE IF NOT EXISTS run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    started_at      TEXT NOT NULL,    -- ISO-8601 UTC
    finished_at     TEXT,             -- NULL while in progress
    status          TEXT NOT NULL,    -- 'running' / 'ok' / 'error'
    error           TEXT,
    total_seen      INTEGER DEFAULT 0,
    inserted        INTEGER DEFAULT 0,
    updated         INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_run_source ON run(source);
