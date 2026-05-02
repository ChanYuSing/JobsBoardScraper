-- LinkedIn-specific schema. Applied by db.init_schema() via sources/linkedin/db.py.
-- Idempotent: safe to re-run on every startup.

CREATE TABLE IF NOT EXISTS job_linkedin (
    -- identity
    job_id              TEXT PRIMARY KEY,
    url                 TEXT,

    -- card fields (populated at fetch time)
    title               TEXT NOT NULL,
    company             TEXT,
    company_url         TEXT,
    company_logo_url    TEXT,
    location            TEXT,
    date_posted         TEXT,   -- ISO-8601 date from <time datetime="...">
    benefit_text        TEXT,   -- e.g. "Be an early applicant" (~50% present)

    -- detail fields (populated at enrich time)
    seniority_level     TEXT,
    employment_type     TEXT,
    job_function        TEXT,
    industries          TEXT,
    num_applicants      TEXT,   -- free-text e.g. "Be among the first 25 applicants"
    description_text    TEXT,
    description_html    TEXT,

    -- provenance
    first_seen_at       TEXT NOT NULL,   -- ISO-8601 UTC
    last_seen_at        TEXT NOT NULL,
    first_seen_run_id   INTEGER REFERENCES run(id),
    last_seen_run_id    INTEGER REFERENCES run(id),
    detail_fetched_at   TEXT,
    detail_error        TEXT,

    -- full raw payloads — never discard source data
    raw_card_json       TEXT,
    raw_detail_json     TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_linkedin_first_seen      ON job_linkedin(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_job_linkedin_detail_fetched  ON job_linkedin(detail_fetched_at);
CREATE INDEX IF NOT EXISTS idx_job_linkedin_last_seen_run   ON job_linkedin(last_seen_run_id);
CREATE INDEX IF NOT EXISTS idx_job_linkedin_date_posted     ON job_linkedin(date_posted);
