# JobBoardScraper

Scrapes job listings from **LinkedIn (guest API)** and **JobsDB (SEEK GraphQL)** into a local SQLite database, with a built-in web UI to browse, filter, triage, and AI-score results.

## Setup

**Requirements:** Python 3.12+

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

## Web UI (recommended)

Start the web server:

```powershell
uvicorn src.jobboard.web.main:app --host 127.0.0.1 --port 8001 --reload
```

Open `http://127.0.0.1:8001` in your browser.

### Pages

| Page | What you can do |
|---|---|
| **Jobs** | Browse all scraped listings. Filter by source, status, keyword, date, work type, salary, and more. Click any job title to open the detail panel. Triage jobs as Saved / Dismissed / New. |
| **Sources** | Configure search parameters for each source — keywords, location, filters. Enable or disable a source. Save and optionally trigger a run immediately. |
| **Schedule** | Set a daily cron schedule (hour, minute, days of week). Control run order and retry behaviour. Tune HTTP scraper settings (timeout, jitter, user agent). |
| **Runs** | View the full history of every scrape run — source, phase, status, duration, job count, and any errors. Filter by source, status, or date. |
| **Analyse** | Paste your CV, define scoring fields, write a system prompt, then preview the fully-assembled prompt for a random job. Wire up any OpenAI-compatible AI provider to score all jobs. |

### Configuring sources in the UI

1. Go to **Sources**.
2. Enter keywords (one per line — all terms are ANDed together).
3. Set location and any filters (date range, work type, etc.).
4. Click **Save** — changes are written to `config.yaml` immediately.
5. Click **▶ Run Now** to trigger a scrape for that source without waiting for the schedule.

### Setting a schedule

1. Go to **Schedule → Run settings**.
2. Pick a time and days. Leave all days unchecked to run every day.
3. Drag the run order chips to control which source runs first.
4. Click **Save**. The scheduler picks up the new cron on next restart (or use **▶ Run All Now** to run immediately).

### AI scoring (Analyse page)

1. Go to **Analyse**.
2. Paste your CV in the **Your CV** textarea.
3. Adjust the **Scoring fields** if needed — each field becomes a column in the results table and a key in the AI's JSON response.
4. Edit the **System prompt** to match your preferences.
5. Click **Preview prompts** to see the fully-assembled prompt for a random job — use this to verify quality before running at scale.
6. Click **Save settings** to persist CV, fields, and system prompt to `config.yaml`.
7. Configure an AI provider in `config.yaml` (see below), then click **Score Each Job** to run scoring across all non-dismissed jobs.

---

## CLI

All operations are also available from the command line.

```powershell
# Phase 1 — fetch card data (title, company, location, salary)
jobboard fetch                          # all enabled sources
jobboard fetch --source linkedin_guest
jobboard fetch --source jobsdb

# Phase 2 — enrich with full descriptions (one HTTP request per job)
jobboard enrich
jobboard enrich --source linkedin_guest
jobboard enrich --source jobsdb

# Quick inspection
jobboard new  --since 24h              # jobs first seen in last 24 h
jobboard runs --source jobsdb -n 5     # last 5 run records
```

Scraping is two-phase: `fetch` collects listing cards; `enrich` fetches the full description for each job. Re-running `fetch` is safe — existing rows are upserted (`last_seen_at` is updated, new rows get `first_seen_at` set).

---

## Sources

| Name | Table | Cap | Notes |
|---|---|---|---|
| `linkedin_guest` | `job_linkedin` | ~1 000 / search | Public REST endpoint, no login required |
| `jobsdb` | `job_jobsdb` | Unlimited (paginated) | SEEK GraphQL API, HK-dominant board |

---

## Config reference (`config.yaml`)

All settings are editable in the web UI. The file is the single source of truth — the UI reads and writes it directly.

```yaml
sources:
  jobsdb:
    enabled: true
    keywords: null              # one keyword per line in UI; null = all jobs
    location: Hong Kong SAR
    daterange: "1"              # days: 1 | 3 | 7 | 14 | 31 | blank = all time
    work_arrangement: null      # on-site | hybrid | remote
    work_type: null             # full-time | part-time | contract | casual | internship
    classification: null        # industry ID e.g. 6281 = ICT
    subclassification: null
    salary_range: null          # e.g. 30000-60000 (monthly HKD)
    salary_type: null           # Monthly | Annual
    sort_mode: null             # ListDate = newest | Relevance = default
    page_size: 32               # results per page (max 32)
    start_page: 1               # resume from page N
    max_pages: 0                # 0 = no cap

  linkedin_guest:
    enabled: true
    keywords: null              # required by LinkedIn — blank returns no results
    location: Hong Kong
    hours_old: 720              # last N hours (720 = 30 days); null = all time
    job_type: null              # fulltime | parttime | contract | internship
    is_remote: null             # true | false | hybrid
    experience_level: null      # 1=Internship 2=Entry 3=Associate 4=Mid-Senior 5=Director 6=Executive
    easy_apply: null            # true = Easy Apply only
    sort_by_date: null          # true = most-recent first
    geo_id: null                # numeric geoId e.g. 102234630 for HK; overrides location
    industry_id: null           # 96=technology | 4=software | 43=financial services

scraper:
  request_timeout_seconds: 20
  retries: 3
  jitter_ms: [1000, 3000]      # random delay range between requests (ms)
  user_agent: "Mozilla/5.0 ..."

storage:
  sqlite_path: data/jobs.sqlite

scheduler:
  cron: "0 1 * * *"            # minute hour dom month dow
  order: [jobsdb, linkedin_guest]
  retry_delay_minutes: 60
  max_retries: 3

ai:
  system_prompt: |
    You are an honest career advisor. Given a candidate CV and a job listing,
    score the job on each criterion below. Be objective — do not inflate scores.
  cv: ""                        # paste your CV here, or use the Analyse page UI
  provider: openai              # openai | groq | openrouter | ollama
  base_url: https://api.openai.com/v1   # swap for Groq/OpenRouter/Ollama endpoint
  api_key: ""                   # leave blank for Ollama (local)
  model: gpt-4o-mini            # any model name supported by the provider
  fields:
    - name: skills_match
      type: int
      description: How well the candidate's skills match what the role requires (1-10)
    - name: seniority_fit
      type: int
      description: How well the candidate's experience level matches the expected seniority (1-10)
    - name: growth_value
      type: int
      description: How much this role benefits the candidate's career growth (1-10)
    - name: overall
      type: int
      description: Overall recommendation — should the candidate apply? (1-10)
    - name: verdict
      type: str
      description: One sentence — strongest reason to apply, or key concern that stops you
```

### AI provider endpoints

All providers below use the same OpenAI-compatible `POST /chat/completions` API:

| Provider | base_url | Free tier |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | No |
| Groq | `https://api.groq.com/openai/v1` | Yes (rate-limited) |
| OpenRouter | `https://openrouter.ai/api/v1` | Yes (some models) |
| Ollama (local) | `http://localhost:11434/v1` | Free, no internet |
| LM Studio (local) | `http://localhost:1234/v1` | Free, no internet |

---

## Database

SQLite file at `data/jobs.sqlite`. Key tables:

| Table | Contents |
|---|---|
| `job_jobsdb` | JobsDB listings |
| `job_linkedin` | LinkedIn listings |
| `job_status` | Triage status per job (new / saved / dismissed) |
| `field_def` | AI scoring field definitions (synced from `config.yaml` at startup) |
| `job_analysis` | AI scores per job per field (EAV) |
| `run` | Scrape run log |


## Setup

**Requirements:** Python 3.12+

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

## Web UI (recommended)

Start the web server:

```powershell
uvicorn jobboard.web.main:app --reload
```

Open `http://127.0.0.1:8000` in your browser.

### Pages

| Page | What you can do |
|---|---|
| **Jobs** | Browse all scraped listings. Filter by source, status, keyword, date, work type, salary, and more. Click any job title to open the detail panel. Triage jobs as Saved / Dismissed / New. |
| **Sources** | Configure search parameters for each source — keywords, location, filters. Enable or disable a source. Save and optionally trigger a run immediately. |
| **Schedule** | Set a daily cron schedule (hour, minute, days of week). Control run order and retry behaviour. Tune HTTP scraper settings (timeout, jitter, user agent). |
| **Runs** | View the full history of every scrape run — source, phase, status, duration, job count, and any errors. Filter by source, status, or date. |

### Configuring sources in the UI

1. Go to **Sources**.
2. Enter keywords (one per line — all terms are ANDed together).
3. Set location and any filters (date range, work type, etc.).
4. Click **Save** — changes are written to `config.yaml` immediately.
5. Click **▶ Run Now** to trigger a scrape for that source without waiting for the schedule.

### Setting a schedule

1. Go to **Schedule → Run settings**.
2. Pick a time and days. Leave all days unchecked to run every day.
3. Drag the run order chips to control which source runs first.
4. Click **Save**. The scheduler picks up the new cron on next restart (or use **▶ Run All Now** to run immediately).

---

## CLI

All operations are also available from the command line.

```powershell
# Phase 1 — fetch card data (title, company, location, salary)
jobboard fetch                          # all enabled sources
jobboard fetch --source linkedin_guest
jobboard fetch --source jobsdb

# Phase 2 — enrich with full descriptions (one HTTP request per job)
jobboard enrich
jobboard enrich --source linkedin_guest
jobboard enrich --source jobsdb

# Quick inspection
jobboard new  --since 24h              # jobs first seen in last 24 h
jobboard runs --source jobsdb -n 5     # last 5 run records
```

Scraping is two-phase: `fetch` collects listing cards; `enrich` fetches the full description for each job. Re-running `fetch` is safe — existing rows are upserted (`last_seen_at` is updated, new rows get `first_seen_at` set).

---

## Sources

| Name | Table | Cap | Notes |
|---|---|---|---|
| `linkedin_guest` | `job_linkedin` | ~1 000 / search | Public REST endpoint, no login required |
| `jobsdb` | `job_jobsdb` | Unlimited (paginated) | SEEK GraphQL API, HK-dominant board |

---

## Config reference (`config.yaml`)

All settings are editable in the web UI. The file is the single source of truth — the UI reads and writes it directly.

```yaml
sources:
  jobsdb:
    enabled: true
    keywords: null              # one keyword per line in UI; null = all jobs
    location: Hong Kong SAR
    daterange: "1"              # days: 1 | 3 | 7 | 14 | 31 | blank = all time
    work_arrangement: null      # on-site | hybrid | remote
    work_type: null             # full-time | part-time | contract | casual | internship
    classification: null        # industry ID e.g. 6281 = ICT
    subclassification: null
    salary_range: null          # e.g. 30000-60000 (monthly HKD)
    salary_type: null           # Monthly | Annual
    sort_mode: null             # ListDate = newest | Relevance = default
    page_size: 32               # results per page (max 32)
    start_page: 1               # resume from page N
    max_pages: 0                # 0 = no cap

  linkedin_guest:
    enabled: true
    keywords: null              # required by LinkedIn — blank returns no results
    location: Hong Kong
    hours_old: 720              # last N hours (720 = 30 days); null = all time
    job_type: null              # fulltime | parttime | contract | internship
    is_remote: null             # true | false | hybrid
    experience_level: null      # 1=Internship 2=Entry 3=Associate 4=Mid-Senior 5=Director 6=Executive
    easy_apply: null            # true = Easy Apply only
    sort_by_date: null          # true = most-recent first
    geo_id: null                # numeric geoId e.g. 102234630 for HK; overrides location
    industry_id: null           # 96=technology | 4=software | 43=financial services

scraper:
  request_timeout_seconds: 20
  retries: 3
  jitter_ms: [1000, 3000]      # random delay range between requests (ms)
  user_agent: "Mozilla/5.0 ..."

storage:
  sqlite_path: data/jobs.sqlite

scheduler:
  cron: "0 1 * * *"            # minute hour dom month dow
  order: [jobsdb, linkedin_guest]
  retry_delay_minutes: 60
  max_retries: 3
```

---

## Database

SQLite file at `data/jobs.sqlite`. Key tables:

| Table | Contents |
|---|---|
| `job_jobsdb` | JobsDB listings |
| `job_linkedin` | LinkedIn listings |
| `job_status` | Triage status per job (new / saved / dismissed) |
| `scheduler_run` | One row per scrape phase run |
| `run` | CLI run log |

