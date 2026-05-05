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
    detail_fetched_at   TEXT,
    detail_error        TEXT,

    -- full raw payloads — never discard source data
    raw_card_json       TEXT,
    raw_detail_json     TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_linkedin_first_seen      ON job_linkedin(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_job_linkedin_detail_fetched  ON job_linkedin(detail_fetched_at);
CREATE INDEX IF NOT EXISTS idx_job_linkedin_date_posted     ON job_linkedin(date_posted);

-- Sync triggers: keep job_all in sync with job_linkedin.
CREATE TRIGGER IF NOT EXISTS trg_linkedin_ins AFTER INSERT ON job_linkedin BEGIN
    INSERT OR REPLACE INTO job_all
        (source, job_id, title, company, location, work_type, work_arrangement,
         salary, date_posted, classification, subclassification, teaser,
         description_text, description_html, url, first_seen_at, detail_fetched_at)
    VALUES
        ('linkedin_guest', NEW.job_id, NEW.title, NEW.company, NEW.location,
         NEW.employment_type, NULL, NULL, NEW.date_posted,
         NEW.industries, NEW.job_function, NULL,
         NEW.description_text, NEW.description_html, NEW.url,
         NEW.first_seen_at, NEW.detail_fetched_at);
END;

CREATE TRIGGER IF NOT EXISTS trg_linkedin_upd AFTER UPDATE ON job_linkedin BEGIN
    INSERT OR REPLACE INTO job_all
        (source, job_id, title, company, location, work_type, work_arrangement,
         salary, date_posted, classification, subclassification, teaser,
         description_text, description_html, url, first_seen_at, detail_fetched_at)
    VALUES
        ('linkedin_guest', NEW.job_id, NEW.title, NEW.company, NEW.location,
         NEW.employment_type, NULL, NULL, NEW.date_posted,
         NEW.industries, NEW.job_function, NULL,
         NEW.description_text, NEW.description_html, NEW.url,
         NEW.first_seen_at, NEW.detail_fetched_at);
END;

CREATE TRIGGER IF NOT EXISTS trg_linkedin_del AFTER DELETE ON job_linkedin BEGIN
    DELETE FROM job_all WHERE source = 'linkedin_guest' AND job_id = OLD.job_id;
END;
