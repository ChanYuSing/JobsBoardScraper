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

## Visualising results

The easiest way is **Datasette** — a zero-config web UI that runs locally:

```powershell
pip install datasette
datasette data\jobs.sqlite
```

Opens at `http://127.0.0.1:8001`. Browse tables, run SQL, filter and sort — no
setup beyond the install. The `all_jobs` view combines both sources into one
queryable table.

For deeper analysis (charts, pandas DataFrames), open a Jupyter notebook:

```powershell
pip install jupyter pandas plotly
jupyter lab
```

```python
import sqlite3, pandas as pd, plotly.express as px

db = sqlite3.connect("data/jobs.sqlite")

# Listings per day
df = pd.read_sql("SELECT DATE(first_seen_at) AS day, COUNT(*) AS n FROM all_jobs GROUP BY day ORDER BY day", db)
px.line(df, x="day", y="n", title="New listings per day")

# Top companies
df2 = pd.read_sql("SELECT company, COUNT(*) AS n FROM job_jobsdb WHERE company IS NOT NULL GROUP BY company ORDER BY n DESC LIMIT 20", db)
px.bar(df2, x="company", y="n", title="Top 20 companies – JobsDB")

# LinkedIn seniority breakdown
df3 = pd.read_sql("SELECT seniority_level, COUNT(*) AS n FROM job_linkedin WHERE seniority_level IS NOT NULL GROUP BY seniority_level ORDER BY n DESC", db)
px.pie(df3, names="seniority_level", values="n", title="LinkedIn – Seniority levels")
```

