# JobBoardScraper

Hong Kong job-board scraper focused on ML/AI roles.
Pulls listings from **LinkedIn (guest API)** and **JobsDB (SEEK GraphQL)** into
a single SQLite database with one table per source.

## Quickstart

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

Edit `config.yaml` to set keywords, filters, enable/disable sources.

```powershell
# Phase 1 — fetch card data
jobboard fetch                          # all enabled sources
jobboard fetch --source linkedin_guest  # one source only
jobboard fetch --source jobsdb

# Phase 2 — enrich with full descriptions (makes one HTTP request per job)
jobboard enrich                         # all sources that need it
jobboard enrich --source linkedin_guest
jobboard enrich --source jobsdb

# Inspect recent activity
jobboard new   --since 24h              # jobs first seen in last 24 h
jobboard runs  --source jobsdb  -n 5    # last 5 run records
```

Database lives at `data/jobs.sqlite`. Re-running `fetch` upserts — existing
rows get `last_seen_at` bumped; new rows get `first_seen_at` set.

## Sources

| Source name | Table | Card ceiling | Description |
|---|---|---|---|
| `linkedin_guest` | `job_linkedin` | 1 000 / keyword (hard cap) | Public REST endpoint, no auth |
| `jobsdb` | `job_jobsdb` | unlimited (paginated) | SEEK GraphQL, HK-dominant board |

Both sources are **two-phase**: `fetch` populates card fields; `enrich` fills
`description_text/html` and structured detail fields.
JobsDB is the exception — descriptions are fetched inline during `enrich` only.

## Config reference (`config.yaml`)

```yaml
sources:
  linkedin_guest:
    enabled: true
    keywords: ["machine learning", "data scientist"]
    location: "Hong Kong"       # text fallback
    geo_id: null                # 102234630 = HK — overrides location, more precise
    hours_old: 720              # f_TPR: last N hours (720 = 30 days); null = no filter
    job_type: null              # fulltime | parttime | contract | internship
    is_remote: null             # true | false | "hybrid" | null
    experience_level: null      # 1–6 or [2,3,4] for multi; null = all
    easy_apply: null            # true = Easy Apply only
    sort_by_date: null          # true = most-recent first
    industry_id: null           # 96=technology | 4=software | 43=financial services

  jobsdb:
    enabled: true
    url: "https://hk.jobsdb.com/jobs?daterange=2"
    page_size: 32               # max 32 per GraphQL page

scraper:
  request_timeout_seconds: 20
  retries: 3
  jitter_ms: [1000, 3000]

storage:
  sqlite_path: "data/jobs.sqlite"
```

## Inspecting the database

```powershell
# Row counts per table
sqlite3 data/jobs.sqlite "SELECT 'linkedin', COUNT(*) FROM job_linkedin UNION ALL SELECT 'jobsdb', COUNT(*) FROM job_jobsdb;"

# Most recently seen jobs
sqlite3 data/jobs.sqlite "SELECT job_id, title, company FROM job_linkedin ORDER BY last_seen_at DESC LIMIT 10;"
sqlite3 data/jobs.sqlite "SELECT job_id, title, company FROM job_jobsdb  ORDER BY last_seen_at DESC LIMIT 10;"

# NULL-rate audit (run after every enrich cycle)
sqlite3 data/jobs.sqlite "
SELECT
    COUNT(*)                                                                    AS total,
    100*SUM(CASE WHEN detail_fetched_at IS NULL THEN 1 ELSE 0 END)/COUNT(*)    AS pct_not_enriched,
    100*SUM(CASE WHEN seniority_level   IS NULL THEN 1 ELSE 0 END)/COUNT(*)    AS pct_null_seniority,
    100*SUM(CASE WHEN description_text  IS NULL THEN 1 ELSE 0 END)/COUNT(*)    AS pct_null_description
FROM job_linkedin;"

sqlite3 data/jobs.sqlite "
SELECT
    COUNT(*)                                                                    AS total,
    100*SUM(CASE WHEN detail_fetched_at IS NULL THEN 1 ELSE 0 END)/COUNT(*)    AS pct_not_enriched,
    100*SUM(CASE WHEN salary_label      IS NULL THEN 1 ELSE 0 END)/COUNT(*)    AS pct_null_salary,
    100*SUM(CASE WHEN description_text  IS NULL THEN 1 ELSE 0 END)/COUNT(*)    AS pct_null_description
FROM job_jobsdb;"
```

Interpretation: 100% NULL after enrich → selector broken. 30–70% NULL → field
is genuinely optional. 0% NULL → structural field, alert if it ever goes missing.

## Research tools

```powershell
# Static + DB coverage probe (no network)
python src/jobboard/sources/linkedin/probe_coverage.py
python src/jobboard/sources/jobsdb/probe_coverage.py
```

See `DESIGN.md` for full API research findings, confirmed parameters, field
coverage tables, and architectural decisions.
