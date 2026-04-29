# JobBoardScraper

Multi-source job-board scraper. Pulls listings from JobsDB (Hong Kong) and the
four boards JobSpy supports (LinkedIn, Indeed, Glassdoor, ZipRecruiter) into a
single SQLite database keyed by `(source, external_id)`.

## Quickstart

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .

# Edit config.yaml -- enable / disable sources, set keywords for JobSpy, etc.
jobboard fetch -v                    # all enabled sources
jobboard fetch --source jobsdb       # one source only
jobboard fetch --source jobspy_linkedin

jobboard enrich                      # fill in JobsDB descriptions (jobspy_* skipped: inline)
jobboard new --since 24h             # recently first-seen jobs
jobboard runs --source jobsdb -n 5   # recent run history
```

Database lives at `data/jobs.sqlite`. Re-running `fetch` upserts: existing
rows get `last_seen_at` updated; new rows get `first_seen_at` set.

## Sources

| Name | Adapter | Description fetched during search? |
|---|---|---|
| `jobsdb` | SEEK GraphQL (`JobSearchV6` + `jobDetails`) | No -- run `enrich` |
| `jobspy_linkedin` | `python-jobspy` | Yes (`linkedin_fetch_description`) |
| `jobspy_indeed` | `python-jobspy` | Yes |
| `jobspy_glassdoor` | `python-jobspy` | Yes |
| `jobspy_ziprecruiter` | `python-jobspy` | Yes (off by default -- no HK presence) |

## Inspecting

```powershell
sqlite3 data/jobs.sqlite "SELECT source, COUNT(*) FROM job GROUP BY source;"
sqlite3 data/jobs.sqlite "SELECT source,external_id,title,company FROM job ORDER BY first_seen_at DESC LIMIT 10;"
```
