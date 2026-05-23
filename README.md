# JobBoardScraper

Scrapes job listings from **LinkedIn (guest API)** and **JobsDB (SEEK GraphQL)** into a local SQLite database, with a built-in web UI to browse, filter, triage, and AI-score results.

---

## Setup and first run

### Step 1 — Install

**Option A: Docker (recommended, no Python required)**

Requirements: [Docker Desktop](https://www.docker.com/products/docker-desktop/)

```bash
git clone https://github.com/ChanYuSing/JobsBoardScraper.git
cd JobBoardScraper
docker compose up -d
```

Open `http://localhost:8001`.

- Data is saved in a Docker volume — not lost when the container stops.
- Edit `config.yaml` at any time — no rebuild needed.
- To stop: `docker compose stop`. To remove the container (data preserved): `docker compose down`.

> **Local AI (Ollama / LM Studio):** These run on your machine, not inside the container. Set `base_url` to use `host.docker.internal`:
> ```yaml
> ai:
>   provider: ollama
>   base_url: http://host.docker.internal:11434/v1
> ```

**Option B: Python 3.12+**

```bash
# macOS / Linux
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn src.jobboard.web.main:app --host 127.0.0.1 --port 8001 --reload
```

```powershell
# Windows (PowerShell)
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -e .
uvicorn src.jobboard.web.main:app --host 127.0.0.1 --port 8001 --reload
```

Open `http://127.0.0.1:8001`.

---

### Step 2 — Configure your search

1. Go to **Sources**.
2. Enter keywords (one per line — all terms are ANDed together) and set your location.
3. Click **Save** — changes are written to `config.yaml` immediately.

See [Config reference](#config-reference-configyaml) for all available filters (work type, salary, date range, etc.).

---

### Step 3 — Fetch listings

Click **Run Now** on the Sources page, or **Run All Now** on the Schedule page.

This pulls listing cards — title, company, salary. Check the **Runs** page for progress. A typical run fetches hundreds to thousands of listings in under a minute. Re-running fetch is safe — existing rows are upserted.

---

### Step 4 — Enrich with full descriptions

After fetch completes, click **Run Now** again (the Enrich phase runs as a second pass), or use **Run All Now** on the Schedule page to run both phases together.

This fetches the full job description for each listing (one HTTP request per job). It takes longer than fetch. Jobs without descriptions are skipped by the AI scorer — enrich must run before scoring.

---

### Step 5 — Score jobs with AI

> **Prerequisite:** You need a local model (Ollama / LM Studio) or an API key from OpenAI, DeepSeek, Gemini, Grok, or Anthropic. See the [AI providers](#ai-providers) table.

1. Go to **Analyse**.
2. Paste your CV into the **Your CV** textarea.
3. Select your AI provider and enter your API key.
4. Optionally adjust the **Scoring fields** and **System prompt** to match your preferences.
5. Click **Save settings**.
6. Click **Score new jobs** to score all unscored jobs, or **Re-score all** to re-run everything.

---

### Step 6 — Browse results

Go to **Jobs**. Add score columns from the column picker and filter by minimum score to surface the best matches. Click any job title to open the full description and AI verdict. Triage jobs as **Saved**, **Dismissed**, or **New**.

---

## Web UI reference

### Pages

| Page | What you can do |
|---|---|
| **Jobs** | Browse all scraped listings. Filter by source, status, keyword, date, work type, salary, and more. Click any job to open the detail panel. Triage jobs as Saved / Dismissed / New. Add AI score columns and filter by minimum score. |
| **Sources** | Configure search parameters for each source — keywords, location, filters. Enable or disable a source. Save and trigger a run immediately. |
| **Schedule** | Set a cron schedule (hour, minute, days of week). Control run order. Tune scraper settings (timeout, jitter, user agent). Toggle auto-score after each run. |
| **Runs** | View the full history of every scrape run — source, phase, status, duration, job count, and any errors. Cancel queued or active runs. |
| **Analyse** | Paste your CV, define scoring fields, write a system prompt, preview the assembled prompt, then score jobs via any supported AI provider. |

### Score Jobs badge

The badge on the Analyse page shows the full breakdown:

`N new · N total · N awaiting enrich · ⚠ N enrich errors · N no description`

| State | Meaning |
|---|---|
| **new** | Have a description but not yet scored |
| **total** | All non-dismissed jobs with a description (the scoring pool) |
| **awaiting enrich** | Scraped but not yet enriched — run Enrich first |
| **⚠ enrich errors** | Enrich failed (network error, 403, etc.) — retried automatically on the next Enrich run |
| **no description** | Enriched but the listing had no description text — retried on the next Enrich run |

### Setting a schedule

1. Go to **Schedule → Run settings**.
2. Pick a time and days. Leave all days unchecked to run every day.
3. Set the run order using the dropdowns.
4. Click **Save settings** — the scheduler updates immediately, no restart required.

Enable **Auto-score** to have jobs scored automatically after each fetch+enrich cycle.

---

## CLI

All operations are also available from the command line.

```bash
# Phase 1 -- fetch card data (title, company, location, salary)
jobboard fetch                          # all enabled sources
jobboard fetch --source linkedin_guest
jobboard fetch --source jobsdb

# Phase 2 -- enrich with full descriptions (one HTTP request per job)
jobboard enrich
jobboard enrich --source linkedin_guest
jobboard enrich --source jobsdb

# Quick inspection
jobboard new  --since 24h              # jobs first seen in last 24 h
jobboard runs --source jobsdb -n 5     # last 5 run records
```

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
    keywords: []                # required by LinkedIn -- blank returns no results
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
  provider: ollama              # see AI providers table below
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
