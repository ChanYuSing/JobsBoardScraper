-- JobBoardScraper — Postgres / Supabase schema (idempotent).
-- Drop-in replacement for the SQLite schema.sql + per-source schema.sql files.
-- Applied by jobboard.db.init_schema() at process startup.

-- ─── Per-source raw tables ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS job_jobsdb (
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

CREATE INDEX IF NOT EXISTS idx_job_jobsdb_first_seen     ON job_jobsdb(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_job_jobsdb_detail_fetched ON job_jobsdb(detail_fetched_at);
CREATE INDEX IF NOT EXISTS idx_job_jobsdb_listing_date   ON job_jobsdb(listing_date_utc);

CREATE TABLE IF NOT EXISTS job_linkedin (
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

CREATE INDEX IF NOT EXISTS idx_job_linkedin_first_seen      ON job_linkedin(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_job_linkedin_detail_fetched  ON job_linkedin(detail_fetched_at);
CREATE INDEX IF NOT EXISTS idx_job_linkedin_date_posted     ON job_linkedin(date_posted);

-- ─── Cross-cutting tables ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS scheduler_run (
    id           BIGSERIAL PRIMARY KEY,
    source       TEXT    NOT NULL,
    phase        TEXT    NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    status       TEXT    NOT NULL,
    jobs_found   INTEGER,
    jobs_total   INTEGER,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_sched_run_source ON scheduler_run(source);
CREATE INDEX IF NOT EXISTS idx_sched_run_status ON scheduler_run(status);

CREATE TABLE IF NOT EXISTS job_status (
    source     TEXT NOT NULL,
    job_id     TEXT NOT NULL,
    status     TEXT NOT NULL,
    marked_at  TEXT NOT NULL,
    PRIMARY KEY (source, job_id)
);

CREATE TABLE IF NOT EXISTS field_def (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    type        TEXT NOT NULL CHECK(type IN ('int', 'str')),
    description TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS job_analysis (
    source      TEXT NOT NULL,
    job_id      TEXT NOT NULL,
    field_id    BIGINT NOT NULL REFERENCES field_def(id) ON DELETE CASCADE,
    value_int   INTEGER,
    value_str   TEXT,
    analysed_at TEXT NOT NULL,
    PRIMARY KEY (source, job_id, field_id)
);
CREATE INDEX IF NOT EXISTS idx_job_analysis_src_job ON job_analysis (source, job_id);

-- ─── Denormalised job_all (kept in sync via triggers) ───────────────────

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

CREATE INDEX IF NOT EXISTS idx_job_all_date_posted       ON job_all(date_posted);
CREATE INDEX IF NOT EXISTS idx_job_all_first_seen        ON job_all(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_job_all_source            ON job_all(source);
CREATE INDEX IF NOT EXISTS idx_job_all_title             ON job_all(title);
CREATE INDEX IF NOT EXISTS idx_job_all_company           ON job_all(company);
CREATE INDEX IF NOT EXISTS idx_job_all_location          ON job_all(location);
CREATE INDEX IF NOT EXISTS idx_job_all_work_type         ON job_all(work_type);
CREATE INDEX IF NOT EXISTS idx_job_all_classification    ON job_all(classification);
CREATE INDEX IF NOT EXISTS idx_job_all_subclassification ON job_all(subclassification);

-- ─── Trigram indexes for LIKE '%substring%' filters ────────────────────
-- These let Postgres use an index for the chip-based keyword filters
-- instead of full-table-scanning every text column on every request.
-- Without these, filtering on description_text (~40k HTML blobs) takes >1s.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_job_all_title_trgm
    ON job_all USING gin (LOWER(title) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_job_all_company_trgm
    ON job_all USING gin (LOWER(company) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_job_all_location_trgm
    ON job_all USING gin (LOWER(location) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_job_all_work_type_trgm
    ON job_all USING gin (LOWER(work_type) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_job_all_classification_trgm
    ON job_all USING gin (LOWER(classification) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_job_all_subclassification_trgm
    ON job_all USING gin (LOWER(subclassification) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_job_all_description_trgm
    ON job_all USING gin (LOWER(description_text) gin_trgm_ops);

-- ─── Triggers: keep job_all in sync ─────────────────────────────────────

CREATE OR REPLACE FUNCTION sync_jobsdb_to_all() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM job_all WHERE source = 'jobsdb' AND job_id = OLD.job_id;
        RETURN OLD;
    END IF;
    INSERT INTO job_all
        (source, job_id, title, company, location, work_type, work_arrangement,
         salary, date_posted, classification, subclassification, teaser,
         description_text, description_html, url, first_seen_at, detail_fetched_at)
    VALUES
        ('jobsdb', NEW.job_id, NEW.title, NEW.company, NEW.location,
         NEW.work_types, NEW.work_arrangement, NEW.salary_label, NEW.listing_date_utc,
         NEW.classification, NEW.subclassification, NEW.teaser,
         NEW.description_text, NEW.description_html, NEW.url,
         NEW.first_seen_at, NEW.detail_fetched_at)
    ON CONFLICT (source, job_id) DO UPDATE SET
        title             = EXCLUDED.title,
        company           = EXCLUDED.company,
        location          = EXCLUDED.location,
        work_type         = EXCLUDED.work_type,
        work_arrangement  = EXCLUDED.work_arrangement,
        salary            = EXCLUDED.salary,
        date_posted       = EXCLUDED.date_posted,
        classification    = EXCLUDED.classification,
        subclassification = EXCLUDED.subclassification,
        teaser            = EXCLUDED.teaser,
        description_text  = EXCLUDED.description_text,
        description_html  = EXCLUDED.description_html,
        url               = EXCLUDED.url,
        first_seen_at     = EXCLUDED.first_seen_at,
        detail_fetched_at = EXCLUDED.detail_fetched_at;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_jobsdb_sync ON job_jobsdb;
CREATE TRIGGER trg_jobsdb_sync
    AFTER INSERT OR UPDATE OR DELETE ON job_jobsdb
    FOR EACH ROW EXECUTE FUNCTION sync_jobsdb_to_all();

CREATE OR REPLACE FUNCTION sync_linkedin_to_all() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM job_all WHERE source = 'linkedin_guest' AND job_id = OLD.job_id;
        RETURN OLD;
    END IF;
    INSERT INTO job_all
        (source, job_id, title, company, location, work_type, work_arrangement,
         salary, date_posted, classification, subclassification, teaser,
         description_text, description_html, url, first_seen_at, detail_fetched_at)
    VALUES
        ('linkedin_guest', NEW.job_id, NEW.title, NEW.company, NEW.location,
         NEW.employment_type, NULL, NULL, NEW.date_posted,
         NEW.industries, NEW.job_function, NULL,
         NEW.description_text, NEW.description_html, NEW.url,
         NEW.first_seen_at, NEW.detail_fetched_at)
    ON CONFLICT (source, job_id) DO UPDATE SET
        title             = EXCLUDED.title,
        company           = EXCLUDED.company,
        location          = EXCLUDED.location,
        work_type         = EXCLUDED.work_type,
        work_arrangement  = EXCLUDED.work_arrangement,
        salary            = EXCLUDED.salary,
        date_posted       = EXCLUDED.date_posted,
        classification    = EXCLUDED.classification,
        subclassification = EXCLUDED.subclassification,
        teaser            = EXCLUDED.teaser,
        description_text  = EXCLUDED.description_text,
        description_html  = EXCLUDED.description_html,
        url               = EXCLUDED.url,
        first_seen_at     = EXCLUDED.first_seen_at,
        detail_fetched_at = EXCLUDED.detail_fetched_at;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_linkedin_sync ON job_linkedin;
CREATE TRIGGER trg_linkedin_sync
    AFTER INSERT OR UPDATE OR DELETE ON job_linkedin
    FOR EACH ROW EXECUTE FUNCTION sync_linkedin_to_all();
