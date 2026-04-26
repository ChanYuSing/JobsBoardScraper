-- Schema for JobBoardScraper. Idempotent: safe to run on every startup.

-- One row per scraper run (audit + lifecycle reference).
CREATE TABLE IF NOT EXISTS run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,    -- ISO-8601 UTC
    finished_at     TEXT,             -- NULL while in progress
    status          TEXT NOT NULL,    -- 'running' / 'ok' / 'error'
    error           TEXT,
    total_seen      INTEGER DEFAULT 0,
    inserted        INTEGER DEFAULT 0,
    updated         INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS job (
    id                  TEXT PRIMARY KEY,
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
    url                 TEXT,
    raw                 TEXT NOT NULL,
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    first_seen_run_id   INTEGER REFERENCES run(id),
    last_seen_run_id    INTEGER REFERENCES run(id),
    description_html    TEXT,
    description_text    TEXT,
    abstract            TEXT,
    expires_at_utc      TEXT,
    is_expired          INTEGER,
    detail_raw          TEXT,
    detail_fetched_at   TEXT,
    detail_error        TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_listing_date    ON job(listing_date_utc);
CREATE INDEX IF NOT EXISTS idx_job_first_seen      ON job(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_job_last_seen_run   ON job(last_seen_run_id);
CREATE INDEX IF NOT EXISTS idx_job_detail_fetched  ON job(detail_fetched_at);
