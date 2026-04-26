"""Typer CLI."""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import typer

from .client import (
    CloudflareBlockedError,
    JobsDBClient,
    RateLimitedError,
    TransientServerError,
)
from .config import load_config
from .db import (
    connect,
    finish_run,
    init_schema,
    jobs_needing_detail,
    record_detail_error,
    start_run,
    update_job_detail,
    upsert_job,
)
from .detail import parse_job_detail
from .normalise import normalise
from .url_parser import parse_search_url

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _main() -> None:
    """JobBoardScraper CLI."""


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------
@app.command()
def fetch(
    config_path: Path = typer.Option(
        Path("config.yaml"), "--config", "-c", help="Path to config.yaml"
    ),
    daterange: int = typer.Option(
        0, "--daterange", help="Override the URL's dateRange (1, 3, 7, 14, 31). 0 = use URL."
    ),
    start_page: int = typer.Option(
        1, "--start-page", help="Resume pagination from this 1-based page (default 1)."
    ),
    max_pages: int = typer.Option(
        0, "--max-pages", help="Stop after this many pages this run. 0 = no cap."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fetch jobs for the configured search URL and upsert them into SQLite."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("jobboard.fetch")

    cfg = load_config(config_path)
    params = parse_search_url(cfg.search.url)
    if daterange > 0:
        params["dateRange"] = daterange
        log.info("Overriding dateRange = %d", daterange)
    log.info("Search params: %s", params)
    if start_page > 1:
        log.info("Resuming from page %d", start_page)
    if max_pages > 0:
        log.info("Will stop after %d pages this run", max_pages)

    conn = connect(cfg.storage.sqlite_path)
    init_schema(conn)
    run_id = start_run(conn)
    log.info("Starting run id=%d", run_id)

    inserted = updated = total_seen = pages = 0
    last_page_done = start_page - 1
    error_msg: str | None = None
    try:
        with JobsDBClient(
            user_agent=cfg.scraper.user_agent,
            timeout_seconds=cfg.scraper.request_timeout_seconds,
            retries=cfg.scraper.retries,
            jitter_ms=tuple(cfg.scraper.jitter_ms),  # type: ignore[arg-type]
        ) as client:
            for page, payload in client.iter_all(
                params,
                page_size=cfg.search.page_size,
                start_page=start_page,
                max_pages=max_pages if max_pages > 0 else None,
            ):
                pages += 1
                jobs = payload.get("data") or []
                total = payload.get("totalCount")
                log.info("Page %d: %d jobs (totalCount=%s)", page, len(jobs), total)
                for raw in jobs:
                    rec = normalise(raw)
                    outcome = upsert_job(conn, rec, run_id)
                    if outcome == "inserted":
                        inserted += 1
                    else:
                        updated += 1
                    total_seen += 1
                conn.commit()
                last_page_done = page
    except CloudflareBlockedError as exc:
        error_msg = f"CloudflareBlocked at page {last_page_done + 1}: {exc}"
        log.error(error_msg)
        log.error(
            "Resume later with: --start-page %d  (last completed page: %d)",
            last_page_done + 1, last_page_done,
        )
        finish_run(
            conn, run_id,
            status="error",
            total_seen=total_seen, inserted=inserted, updated=updated,
            error=error_msg,
        )
        conn.close()
        typer.echo(
            f"Run {run_id}: BLOCKED by Cloudflare. "
            f"Pages done={pages} (1..{last_page_done}). "
            f"Resume with --start-page {last_page_done + 1}"
        )
        raise typer.Exit(code=2)
    except Exception as exc:  # noqa: BLE001
        error_msg = f"{type(exc).__name__}: {exc}"
        log.exception("Fetch failed")
        finish_run(
            conn, run_id,
            status="error",
            total_seen=total_seen, inserted=inserted, updated=updated,
            error=error_msg,
        )
        conn.close()
        raise typer.Exit(code=1)

    finish_run(
        conn, run_id,
        status="ok",
        total_seen=total_seen, inserted=inserted, updated=updated,
    )

    conn.close()
    typer.echo(
        f"Run {run_id}: pages={pages} (last={last_page_done})  seen={total_seen}  "
        f"inserted={inserted}  updated={updated}"
    )


# ---------------------------------------------------------------------------
# new / runs
# ---------------------------------------------------------------------------
_DURATION_RE = re.compile(r"^\s*(\d+)\s*([hd])\s*$", re.IGNORECASE)


def _parse_since(s: str) -> datetime:
    m = _DURATION_RE.match(s)
    if not m:
        raise typer.BadParameter("Use formats like '24h' or '7d'")
    n, unit = int(m.group(1)), m.group(2).lower()
    delta = timedelta(hours=n) if unit == "h" else timedelta(days=n)
    return datetime.now(timezone.utc) - delta


def _open_db(config_path: Path) -> sqlite3.Connection:
    cfg = load_config(config_path)
    conn = connect(cfg.storage.sqlite_path)
    init_schema(conn)
    return conn


@app.command(name="new")
def new_jobs(
    since: str = typer.Option("24h", "--since", help="e.g. 24h, 7d"),
    config_path: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    limit: int = typer.Option(50, "--limit", "-n"),
) -> None:
    """List jobs first seen within the given window."""
    cutoff = _parse_since(since).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = _open_db(config_path)
    rows = conn.execute(
        """
        SELECT id, title, company, work_arrangement, first_seen_at, url
          FROM job
         WHERE first_seen_at >= ?
         ORDER BY first_seen_at DESC
         LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()
    _print_rows(rows, ("first_seen_at", "id", "company", "title", "url"))


@app.command(name="runs")
def list_runs(
    config_path: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """Show recent scraper runs."""
    conn = _open_db(config_path)
    rows = conn.execute(
        """
        SELECT id, started_at, finished_at, status,
               total_seen, inserted, updated, error
          FROM run
         ORDER BY id DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    _print_rows(
        rows,
        ("id", "started_at", "status", "total_seen", "inserted", "updated"),
    )


@app.command(name="enrich")
def enrich(
    config_path: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    limit: int = typer.Option(0, "--limit", "-n", help="0 = no limit"),
    stale_days: int = typer.Option(
        30, "--stale-days", help="Refetch detail older than this many days"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fetch full job descriptions for jobs missing detail (or stale)."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("jobboard.enrich")

    cfg = load_config(config_path)
    conn = connect(cfg.storage.sqlite_path)
    init_schema(conn)

    ids = jobs_needing_detail(
        conn,
        stale_days=stale_days if stale_days > 0 else None,
        limit=limit if limit > 0 else None,
    )
    if not ids:
        typer.echo("Nothing to enrich.")
        conn.close()
        return

    typer.echo(f"Enriching {len(ids)} job(s)...")
    ok = err = 0
    blocked = False
    try:
        with JobsDBClient(
            user_agent=cfg.scraper.user_agent,
            timeout_seconds=cfg.scraper.request_timeout_seconds,
            retries=cfg.scraper.retries,
            jitter_ms=tuple(cfg.scraper.jitter_ms),  # type: ignore[arg-type]
        ) as client:
            for i, job_id in enumerate(ids, 1):
                try:
                    payload = client.fetch_job_detail(job_id)
                    detail = parse_job_detail(payload or {})
                    update_job_detail(
                        conn, job_id,
                        description_html=detail.description_html,
                        description_text=detail.description_text,
                        abstract=detail.abstract,
                        expires_at_utc=detail.expires_at_utc,
                        is_expired=detail.is_expired,
                        detail_raw=detail.raw_json,
                    )
                    ok += 1
                except CloudflareBlockedError as exc:
                    log.error("Cloudflare blocked at job %s (%d/%d). Stopping run.",
                              job_id, i, len(ids))
                    record_detail_error(
                        conn, job_id, f"CloudflareBlockedError: {exc}",
                        transient=True,
                    )
                    err += 1
                    conn.commit()
                    blocked = True
                    break
                except RateLimitedError as exc:
                    log.error("RATE_LIMITED at job %s (%d/%d). Stopping run.",
                              job_id, i, len(ids))
                    record_detail_error(
                        conn, job_id, f"RateLimitedError: {exc}",
                        transient=True,
                    )
                    err += 1
                    conn.commit()
                    blocked = True
                    break
                except (TransientServerError, httpx.HTTPError) as exc:
                    log.warning("transient error for %s: %s", job_id, exc)
                    record_detail_error(
                        conn, job_id, f"{type(exc).__name__}: {exc}",
                        transient=True,
                    )
                    err += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("detail failed for %s: %s", job_id, exc)
                    record_detail_error(
                        conn, job_id, f"{type(exc).__name__}: {exc}",
                    )
                    err += 1
                conn.commit()
                if i % 10 == 0 or i == len(ids):
                    log.info("progress: %d/%d  ok=%d err=%d", i, len(ids), ok, err)
                # polite pacing between detail calls
                client._sleep_jitter()  # type: ignore[attr-defined]
    finally:
        conn.close()

    if blocked:
        typer.echo(
            f"Enrich BLOCKED by Cloudflare. ok={ok}  err={err}  "
            f"remaining={len(ids) - ok - err}. Wait, then rerun."
        )
        raise typer.Exit(code=2)
    typer.echo(f"Enrich done. ok={ok}  err={err}  total={len(ids)}")


def _print_rows(rows: list[sqlite3.Row], cols: tuple[str, ...]) -> None:
    if not rows:
        typer.echo("(no rows)")
        return
    widths = {c: max(len(c), max(len(str(r[c] or "")) for r in rows)) for c in cols}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    typer.echo(header)
    typer.echo("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        typer.echo("  ".join(str(r[c] or "").ljust(widths[c]) for c in cols))


if __name__ == "__main__":
    app()
