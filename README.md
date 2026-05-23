# JobBoardScraper

Scrapes job listings from **LinkedIn (guest API)** and **JobsDB (SEEK GraphQL)** into a local SQLite database, with a built-in web UI to browse, filter, triage, and AI-score results.

## Setup

### Option A — Docker (recommended, no Python required)

**Requirements:** [Docker Desktop](https://www.docker.com/products/docker-desktop/)

```bash
git clone https://github.com/ChanYuSing/JobsBoardScraper.git
cd JobBoardScraper
docker compose up
```

Open `http://localhost:8001` in your browser. That's it.

- Your database is saved in `./data/` on your machine (not lost when the container stops).
- Edit `config.yaml` at any time — no rebuild needed.
- To stop: `Ctrl+C`. To run in the background: `docker compose up -d`.

> **Note for local AI (Ollama / LM Studio):** These run on your machine, not inside the container.
> Change the provider `base_url` in `config.yaml` to use `host.docker.internal` instead of `localhost`:
> ```yaml
> ai:
>   provider: ollama
>   base_url: http://host.docker.internal:11434/v1
> ```

---

### Option B — Python (manual)

**Requirements:** Python 3.12+

**Windows (PowerShell)**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Web UI (recommended)

Start the web server:

```bash
uvicorn src.jobboard.web.main:app --host 127.0.0.1 --port 8001 --reload
```

Open `http://127.0.0.1:8001` in your browser.

### Pages

| Page | What you can do |
|---|---|
| **Jobs** | Browse all scraped listings. Filter by source, status, keyword, date, work type, salary, and more. Click any job title to open the detail panel. Triage jobs as Saved / Dismissed / New. Add AI score columns and filter by minimum score. |
| **Sources** | Configure search parameters for each source —keywords, location, filters. Enable or disable a source. Save and optionally trigger a run immediately. |
| **Schedule** | Set a cron schedule (hour, minute, days of week). Control run order. Tune HTTP scraper settings (timeout, jitter, user agent). Toggle auto-score after each run. |
| **Runs** | View the full history of every scrape run —source, phase, status, duration, job count, and any errors. Cancel queued or active runs. |
| **Analyse** | Paste your CV, define scoring fields, write a system prompt, preview the assembled prompt, then score all jobs via any supported AI provider. |

### Configuring sources in the UI

1. Go to **Sources**.
2. Enter keywords (one per line — all terms are ANDed together).
3. Set location and any filters (date range, work type, etc.).
4. Click **Save** — changes are written to `config.yaml` immediately.
5. Click **Run Now** to trigger a scrape for that source without waiting for the schedule.

### Setting a schedule

1. Go to **Schedule → Run settings**.
2. Pick a time and days. Leave all days unchecked to run every day.
3. Set the run order using the dropdowns.
4. Click **Save settings**. The live scheduler is updated immediately — no restart required.
5. Use **Run All Now** to trigger a run immediately.

### Typical workflow

1. **Fetch** — pulls listing cards (title, company, salary). Run from the Sources page or Schedule page.
2. **Enrich** — fetches the full description for each job (one request per listing). Must run before scoring.
3. **Score** — sends each job's description + your CV to the AI. Only jobs with a description are scored.

All three phases can be triggered manually from the UI or run automatically on a cron schedule.

### Score Jobs badge

The badge on the Analyse page shows the full breakdown:

`N new · N total · N awaiting enrich · ⚠ N enrich errors · N no description`

| State | Meaning |
|---|---|
| **new** | Have a description but not yet scored |
| **total** | All non-dismissed jobs with a description (the scoring pool) |
| **awaiting enrich** | Scraped but not yet enriched — run Enrich first |
| **⚠ enrich errors** | Enrich was attempted but failed (network error, 403, etc.) — will be retried automatically on the next Enrich run |
| **no description** | Enriched successfully but the listing had no description text — will be retried on the next Enrich run |

### AI scoring (Analyse page)

1. Go to **Analyse**.
2. Paste your CV in the **Your CV** textarea.
3. Adjust the **Scoring fields** if needed — each field becomes a column in the Jobs table.
4. Edit the **System prompt** to match your preferences.
5. Click **Preview prompts** to verify the assembled prompt on a random job.
6. Click **Save settings** to persist CV, fields, and prompt to `config.yaml`.
7. Select an AI provider and enter your API key, then click **Score new jobs** (unscored only) or **Re-score all**.

Alternatively, enable **Auto-score** on the Schedule page to score new jobs automatically after each fetch+enrich cycle.

---

## CLI

All operations are also available from the command line.

```powershell
# Phase 1 —fetch card data (title, company, location, salary)
jobboard fetch                          # all enabled sources
jobboard fetch --source linkedin_guest
jobboard fetch --source jobsdb

# Phase 2 —enrich with full descriptions (one HTTP request per job)
jobboard enrich
jobboard enrich --source linkedin_guest
jobboard enrich --source jobsdb

# Quick inspection
jobboard new  --since 24h              # jobs first seen in last 24 h
jobboard runs --source jobsdb -n 5     # last 5 run records
```

Scraping is two-phase: `fetch` collects listing cards; `enrich` fetches the full description for each job. Re-running `fetch` is safe —existing rows are upserted (`last_seen_at` is updated, new rows get `first_seen_at` set).

---

## Sources

| Name | Table | Cap | Notes |
|---|---|---|---|
| `linkedin_guest` | `job_linkedin` | ~1 000 / search | Public REST endpoint, no login required |
| `jobsdb` | `job_jobsdb` | Unlimited (paginated) | SEEK GraphQL API, HK-dominant board |

---

## Config reference (`config.yaml`)

All settings are editable in the web UI. The file is the single source of truth —the UI reads and writes it directly.

```yaml
sources:
  jobsdb:
    enabled: true
    keywords: null              # one keyword per line in UI; null = all jobs
    location: Hong Kong SAR
    daterange: "1"              # days: 1 | 3 | 7 | 14 | 31 | null = all time
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
    keywords: []                # required by LinkedIn —blank returns no results
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
  jitter_ms: [1000, 3000]       # random delay range between requests (ms)
  user_agent: "Mozilla/5.0 ..."

storage:
  sqlite_path: data/jobs.sqlite

scheduler:
  enabled: false                # set true to activate cron scheduling
  cron: "0 1 * * *"            # minute hour dom month dow
  order: [jobsdb, linkedin_guest]

ai:
  provider: ollama              # see provider table below
  model: llama3.2
  base_url: ""                  # leave empty to use provider default
  api_key: ""                   # or set AI_API_KEY environment variable
  api_keys: {}                  # per-provider key store (managed by the UI)
  temperature: 0.2
  auto_score: false             # score new jobs automatically after each run
  system_prompt: "..."
  cv: ""                        # paste your CV here, or use the Analyse page UI
  fields:
    - name: skills_match
      type: int
      description: >-
        How well the candidate's demonstrated skills and experience cover the role's
        core technical and functional requirements. Score only against stated core
        requirements — ignore nice-to-haves.
        1–3: missing most core requirements;
        4–6: meets some core requirements but has notable gaps;
        7–10: meets most or all core requirements. (1-10)
    - name: seniority_fit
      type: int
      description: >-
        How well the candidate's experience level matches the seniority this role expects.
        Penalise both under-qualification (likely rejection) and over-qualification
        (likely boredom or mismatch). If the JD gives no explicit seniority signal,
        infer from the depth of responsibilities described.
        1–3: significantly mismatched in either direction;
        4–6: roughly appropriate but with meaningful gap or excess;
        7–10: well-matched level. (1-10)
    - name: growth_value
      type: int
      description: >-
        Assuming the candidate secures and works this role, how much would it advance
        their career, skills, or future opportunities? Score independently of hiring
        probability — a reach role can still score 9. Consider: skill development,
        company or industry prestige, scope of the role, and alignment with their
        stated career direction.
        1–3: little benefit or actively misaligned with their goals;
        4–6: modest step, some relevant exposure;
        7–10: meaningful accelerator — valuable skills, brand, or access. (1-10)
    - name: overall
      type: int
      description: >-
        Should the candidate apply? Synthesise skills match, seniority fit, and growth
        value. A strong reach role with high growth value warrants a higher score than
        an easy win with low value. A mismatch on role direction or industry is
        sufficient to score low even if technical skills match.
        1–3: do not apply; 4–6: apply if bandwidth allows; 7–10: strong apply. (1-10)
    - name: verdict
      type: str
      description: >-
        One or two sentence sharp verdict. Include the strongest reason to apply OR
        the key reason to hesitate.
```

### AI providers

| Provider key | Endpoint | Auth | Notes |
|---|---|---|---|
| `ollama` | `http://localhost:11434/v1` | None | Local, free |
| `lmstudio` | `http://localhost:1234/v1` | None | Local, free |
| `openai` | `https://api.openai.com/v1` | API key | GPT-4o, GPT-4o-mini |
| `grok` | `https://api.x.ai/v1` | API key | xAI Grok models |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai` | API key | OpenAI-compat endpoint |
| `deepseek` | `https://api.deepseek.com` | API key | DeepSeek V3/V4 |
| `anthropic` | `https://api.anthropic.com` | API key | Claude 3.x / Sonnet |
| `openai_compat` | *(set base_url manually)* | Optional | Any OpenAI-compatible API |

API keys can be set via the `AI_API_KEY` environment variable, per-provider in the `api_keys` dict, or via the Analyse page UI (which stores them in `api_keys`).

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
| `scheduler_run` | Scrape and score run history |
