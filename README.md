# JobBoardScraper

Scrapes JobsDB (Hong Kong) for a saved search URL and stores results in SQLite.

## Step 1 quickstart

```powershell
# Create venv & install
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .

# Edit config.yaml if you want a different search URL, then:
python -m jobboard fetch -v
```

Database lives at `data/jobs.sqlite`. Re-running `fetch` upserts: existing rows
get `last_seen_at` updated; new rows get `first_seen_at` set.

## Inspecting

```powershell
sqlite3 data/jobs.sqlite "SELECT id,title,company,work_arrangement FROM job LIMIT 10;"
```
