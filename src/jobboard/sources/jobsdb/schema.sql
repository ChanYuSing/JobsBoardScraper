-- JobsDB-specific schema. Applied by db.init_schema() via sources/jobsdb/db.py.
-- Idempotent: safe to re-run on every startup.

CREATE TABLE IF NOT EXISTS job_jobsdb (
    -- identity
    job_id              TEXT PRIMARY KEY,
    url                 TEXT,

    -- card fields (from GraphQL jobSearchV6)
    title               TEXT NOT NULL,
    company             TEXT,
    location            TEXT,
    classification      TEXT,   -- e.g. "Information & Communication Technology"
    subclassification   TEXT,   -- e.g. "Developers/Programmers"
    work_types          TEXT,   -- comma-separated e.g. "Full time"
    work_arrangement    TEXT,   -- "Remote" | "Hybrid" | "On-site"
    salary_label        TEXT,   -- e.g. "HK$40,000 – HK$65,000 per month"
    teaser              TEXT,
    bullet_points       TEXT,   -- JSON array of strings
    listing_date_utc    TEXT,
    listing_date_label  TEXT,   -- "6d ago" etc.

    -- detail fields (from GraphQL jobDetails)
    description_html    TEXT,
    description_text    TEXT,
    abstract            TEXT,
    expires_at_utc      TEXT,
    is_expired          INTEGER,  -- 0 | 1

    -- provenance
    first_seen_at       TEXT NOT NULL,   -- ISO-8601 UTC
    last_seen_at        TEXT NOT NULL,
    detail_fetched_at   TEXT,
    detail_error        TEXT,

    -- full raw payloads — never discard source data
    raw_card_json       TEXT,
    raw_detail_json     TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_jobsdb_first_seen     ON job_jobsdb(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_job_jobsdb_detail_fetched ON job_jobsdb(detail_fetched_at);
CREATE INDEX IF NOT EXISTS idx_job_jobsdb_listing_date   ON job_jobsdb(listing_date_utc);

-- Sync triggers: keep job_all in sync with job_jobsdb.
-- INSERT OR REPLACE fires DELETE then INSERT, so both DELETE and INSERT
-- triggers are required to correctly handle upserts.
CREATE TRIGGER IF NOT EXISTS trg_jobsdb_ins AFTER INSERT ON job_jobsdb BEGIN
    INSERT OR REPLACE INTO job_all
        (source, job_id, title, company, location, work_type, work_arrangement,
         salary, date_posted, classification, subclassification, teaser,
         description_text, description_html, url, first_seen_at, detail_fetched_at)
    VALUES
        ('jobsdb', NEW.job_id, NEW.title, NEW.company, NEW.location,
         NEW.work_types, NEW.work_arrangement, NEW.salary_label, NEW.listing_date_utc,
         NEW.classification, NEW.subclassification, NEW.teaser,
         NEW.description_text, NEW.description_html, NEW.url,
         NEW.first_seen_at, NEW.detail_fetched_at);
END;

CREATE TRIGGER IF NOT EXISTS trg_jobsdb_upd AFTER UPDATE ON job_jobsdb BEGIN
    INSERT OR REPLACE INTO job_all
        (source, job_id, title, company, location, work_type, work_arrangement,
         salary, date_posted, classification, subclassification, teaser,
         description_text, description_html, url, first_seen_at, detail_fetched_at)
    VALUES
        ('jobsdb', NEW.job_id, NEW.title, NEW.company, NEW.location,
         NEW.work_types, NEW.work_arrangement, NEW.salary_label, NEW.listing_date_utc,
         NEW.classification, NEW.subclassification, NEW.teaser,
         NEW.description_text, NEW.description_html, NEW.url,
         NEW.first_seen_at, NEW.detail_fetched_at);
END;

CREATE TRIGGER IF NOT EXISTS trg_jobsdb_del AFTER DELETE ON job_jobsdb BEGIN
    DELETE FROM job_all WHERE source = 'jobsdb' AND job_id = OLD.job_id;
END;
