-- Schema for JobBoardScraper. Idempotent: safe to run on every startup.
-- v2: multi-source. Each row is keyed by (source, external_id).

-- Registry of known job sources.
CREATE TABLE IF NOT EXISTS source (
    name          TEXT PRIMARY KEY,
    display_name  TEXT,
    base_url      TEXT
);

INSERT OR IGNORE INTO source (name, display_name, base_url) VALUES
    ('jobsdb',              'JobsDB Hong Kong',          'https://hk.jobsdb.com'),
    ('jobspy_linkedin',     'LinkedIn (via JobSpy)',     'https://www.linkedin.com'),
    ('jobspy_indeed',       'Indeed (via JobSpy)',       'https://www.indeed.com'),
    ('jobspy_glassdoor',    'Glassdoor (via JobSpy)',    'https://www.glassdoor.com'),
    ('jobspy_ziprecruiter', 'ZipRecruiter (via JobSpy)', 'https://www.ziprecruiter.com');

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

CREATE TABLE IF NOT EXISTS job (
    source              TEXT NOT NULL,
    external_id         TEXT NOT NULL,
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
    detail_error        TEXT,
    PRIMARY KEY (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_job_source           ON job(source);
CREATE INDEX IF NOT EXISTS idx_job_listing_date     ON job(listing_date_utc);
CREATE INDEX IF NOT EXISTS idx_job_first_seen       ON job(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_job_last_seen_run    ON job(last_seen_run_id);
CREATE INDEX IF NOT EXISTS idx_job_detail_fetched   ON job(detail_fetched_at);
CREATE INDEX IF NOT EXISTS idx_run_source           ON run(source);
