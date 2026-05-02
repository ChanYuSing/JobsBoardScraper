"""Typer CLI."""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import typer

from .sources.jobsdb.adapter import (
    CloudflareBlockedError,
    RateLimitedError,
    TransientServerError,
)
from .config import Config, load_config
from .db import (
    connect,
    finish_run,
    init_schema,
    start_run,
)
from .sources import KNOWN_SOURCES, build_adapter

app = typer.Typer(add_completion=False, no_args_is_help=True)
log = logging.getLogger("jobboard.cli")


@app.callback()
def _main() -> None:
    """JobBoardScraper CLI."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _resolve_sources(cfg: Config, source_arg: str) -> list[str]:
    """Expand ``--source X`` (or ``all``) against the config."""
    if source_arg == "all":
        chosen = cfg.enabled_sources()
        if not chosen:
            raise typer.BadParameter("No enabled sources in config.yaml")
        return chosen
    if source_arg not in cfg.sources:
        raise typer.BadParameter(
            f"Unknown source '{source_arg}'. Known: {', '.join(KNOWN_SOURCES)}"
        )
    return [source_arg]


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------
@app.command()
def fetch(
    source: str = typer.Option(
        "all", "--source", "-s",
        help="Source name (jobsdb, jobspy_linkedin, ...) or 'all'.",
    ),
    config_path: Path = typer.Option(
        Path("config.yaml"), "--config", "-c", help="Path to config.yaml"
    ),
    daterange: int = typer.Option(
        0, "--daterange",
        help="JobsDB only: override URL's dateRange (1, 3, 7, 14, 31). 0 = use URL.",
    ),
    start_page: int = typer.Option(
        1, "--start-page",
        help="JobsDB only: resume pagination from this 1-based page.",
    ),
    max_pages: int = typer.Option(
        0, "--max-pages",
        help="JobsDB only: stop after this many pages this run. 0 = no cap.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fetch jobs from one or all enabled sources and upsert them into SQLite."""
    _setup_logging(verbose)
    cfg = load_config(config_path)

    # Apply CLI overrides into the JobsDB section so the adapter sees one
    # source of truth.
    if "jobsdb" in cfg.sources:
        s = cfg.sources["jobsdb"]
        if daterange > 0:
            s.daterange = daterange
        if start_page > 1:
            s.start_page = start_page
        if max_pages > 0:
            s.max_pages = max_pages

    sources = _resolve_sources(cfg, source)
    log.info("Sources to run: %s", ", ".join(sources))

    conn = connect(cfg.storage.sqlite_path)
    init_schema(conn)

    overall_exit = 0
    for src_name in sources:
        try:
            code = _run_one_fetch(conn, cfg, src_name)
        except typer.Exit as exc:
            code = int(exc.exit_code or 0)
        if code and code > overall_exit:
            overall_exit = code

    conn.close()
    if overall_exit:
        raise typer.Exit(code=overall_exit)


def _run_one_fetch(conn: sqlite3.Connection, cfg: Config, source: str) -> int:
    log.info("--- fetch source=%s ---", source)
    run_id = start_run(conn, source)
    log.info("Starting run id=%d source=%s", run_id, source)

    inserted = updated = total_seen = 0
    try:
        adapter = build_adapter(source, cfg)
    except Exception as exc:  # noqa: BLE001
        log.error("Could not build adapter for %s: %s", source, exc)
        finish_run(
            conn, run_id, status="error",
            total_seen=0, inserted=0, updated=0,
            error=f"adapter init failed: {exc}",
        )
        return 1

    error_msg: str | None = None
    cf_blocked = False
    try:
        with adapter:
            # JobsDB has a richer iterator that exposes page numbers; use it
            # when available so we can log page-level progress and resume
            # cleanly on Cloudflare blocks.
            if hasattr(adapter, "search_paginated"):
                last_page_done = 0
                try:
                    from .sources.jobsdb import db as jdb_db
                    for page, recs, total in adapter.search_paginated():  # type: ignore[attr-defined]
                        log.info("Page %d: %d jobs (totalCount=%s)",
                                 page, len(recs), total)
                        for rec in recs:
                            outcome = jdb_db.upsert_card(conn, rec, run_id)
                            if outcome == "inserted":
                                inserted += 1
                            else:
                                updated += 1
                            total_seen += 1
                        conn.commit()
                        last_page_done = page
                except CloudflareBlockedError as exc:
                    cf_blocked = True
                    error_msg = (
                        f"CloudflareBlocked at page {last_page_done + 1}: {exc}"
                    )
                    log.error(error_msg)
                    log.error(
                        "Resume later with: --source jobsdb --start-page %d",
                        last_page_done + 1,
                    )
            else:
                if source == "linkedin_guest":
                    from .sources.linkedin import db as li_db
                    for card in adapter.search():
                        outcome = li_db.upsert_card(conn, card, run_id)
                        if outcome == "inserted":
                            inserted += 1
                        else:
                            updated += 1
                        total_seen += 1
                conn.commit()
    except CloudflareBlockedError as exc:
        cf_blocked = True
        error_msg = f"CloudflareBlocked: {exc}"
        log.error(error_msg)
    except Exception as exc:  # noqa: BLE001
        error_msg = f"{type(exc).__name__}: {exc}"
        log.exception("Fetch failed for source %s", source)

    if error_msg:
        finish_run(
            conn, run_id, status="error",
            total_seen=total_seen, inserted=inserted, updated=updated,
            error=error_msg,
        )
        typer.echo(
            f"[{source}] Run {run_id}: ERROR  seen={total_seen}  "
            f"inserted={inserted}  updated={updated}  -- {error_msg}"
        )
        return 2 if cf_blocked else 1

    finish_run(
        conn, run_id, status="ok",
        total_seen=total_seen, inserted=inserted, updated=updated,
    )
    typer.echo(
        f"[{source}] Run {run_id}: ok  seen={total_seen}  "
        f"inserted={inserted}  updated={updated}"
    )
    return 0


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
    source: str = typer.Option(
        "all", "--source", "-s", help="Filter by source name, or 'all'."
    ),
    config_path: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    limit: int = typer.Option(50, "--limit", "-n"),
) -> None:
    """List jobs first seen within the given window."""
    cutoff = _parse_since(since).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = _open_db(config_path)
    parts: list[str] = []
    args: list = []
    if source in ("all", "linkedin_guest"):
        parts.append(
            "SELECT 'linkedin_guest' AS source, job_id AS external_id, "
            "       title, company, first_seen_at, url "
            "  FROM job_linkedin WHERE first_seen_at >= ?"
        )
        args.append(cutoff)
    if source in ("all", "jobsdb"):
        parts.append(
            "SELECT 'jobsdb' AS source, job_id AS external_id, "
            "       title, company, first_seen_at, url "
            "  FROM job_jobsdb WHERE first_seen_at >= ?"
        )
        args.append(cutoff)
    if not parts:
        typer.echo(f"Unknown source '{source}'.")
        return
    sql = " UNION ALL ".join(parts) + " ORDER BY first_seen_at DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    _print_rows(
        rows,
        ("first_seen_at", "source", "external_id", "company", "title", "url"),
    )


@app.command(name="runs")
def list_runs(
    config_path: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    source: str = typer.Option("all", "--source", "-s"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """Show recent scraper runs."""
    conn = _open_db(config_path)
    sql = (
        "SELECT id, source, started_at, finished_at, status, "
        "       total_seen, inserted, updated, error FROM run"
    )
    args: list = []
    if source != "all":
        sql += " WHERE source = ?"
        args.append(source)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    _print_rows(
        rows,
        ("id", "source", "started_at", "status", "total_seen", "inserted", "updated"),
    )


# ---------------------------------------------------------------------------
# enrich
# ---------------------------------------------------------------------------
@app.command(name="enrich")
def enrich(
    source: str = typer.Option(
        "all", "--source", "-s",
        help="Source to enrich (default: every source whose adapter needs enrichment).",
    ),
    config_path: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    limit: int = typer.Option(0, "--limit", "-n", help="0 = no limit"),
    stale_days: int = typer.Option(
        30, "--stale-days", help="Refetch detail older than this many days"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fetch full job descriptions for jobs missing detail (or stale)."""
    _setup_logging(verbose)
    cfg = load_config(config_path)

    targets = _resolve_sources(cfg, source)
    overall_exit = 0
    conn = connect(cfg.storage.sqlite_path)
    init_schema(conn)

    try:
        for src_name in targets:
            try:
                adapter = build_adapter(src_name, cfg)
            except Exception as exc:  # noqa: BLE001
                log.error("Skipping %s: %s", src_name, exc)
                continue
            if getattr(adapter, "enrich_inline", False):
                log.info("[%s] adapter fetches descriptions inline; skipping enrich.",
                         src_name)
                continue
            code = _enrich_one(conn, adapter, src_name, limit, stale_days)
            if code > overall_exit:
                overall_exit = code
    finally:
        conn.close()

    if overall_exit:
        raise typer.Exit(code=overall_exit)


def _enrich_one(
    conn: sqlite3.Connection, adapter, source: str, limit: int, stale_days: int
) -> int:
    stale = stale_days if stale_days > 0 else None
    lim = limit if limit > 0 else None

    if source == "linkedin_guest":
        from .sources.linkedin import db as li_db
        job_ids = li_db.jobs_needing_enrich(conn, stale_days=stale, limit=lim)

        def _write_detail(job_id: str, detail) -> None:
            li_db.upsert_detail(conn, job_id, detail)

        def _write_error(job_id: str, error: str) -> None:
            li_db.record_detail_error(conn, job_id, error)

    elif source == "jobsdb":
        from .sources.jobsdb import db as jdb_db
        job_ids = jdb_db.jobs_needing_enrich(conn, stale_days=stale, limit=lim)

        def _write_detail(job_id: str, detail) -> None:
            jdb_db.upsert_detail(conn, job_id, detail)

        def _write_error(job_id: str, error: str) -> None:
            jdb_db.record_detail_error(conn, job_id, error)

    else:
        log.error("[%s] No enrich path configured for this source.", source)
        return 1

    if not job_ids:
        typer.echo(f"[{source}] Nothing to enrich.")
        return 0

    typer.echo(f"[{source}] Enriching {len(job_ids)} job(s)...")
    ok = err = 0
    blocked = False
    with adapter:
        for i, job_id in enumerate(job_ids, 1):
            try:
                payload = adapter.fetch_detail(job_id)
                detail = adapter.parse_detail(payload or {})
                _write_detail(job_id, detail)
                ok += 1
            except CloudflareBlockedError as exc:
                log.error("[%s] Cloudflare blocked at %s (%d/%d). Stopping.",
                          source, job_id, i, len(job_ids))
                _write_error(job_id, f"CloudflareBlockedError: {exc}")
                err += 1
                conn.commit()
                blocked = True
                break
            except RateLimitedError as exc:
                log.error("[%s] RATE_LIMITED at %s (%d/%d). Stopping.",
                          source, job_id, i, len(job_ids))
                _write_error(job_id, f"RateLimitedError: {exc}")
                err += 1
                conn.commit()
                blocked = True
                break
            except (TransientServerError, httpx.HTTPError) as exc:
                log.warning("[%s] transient error for %s: %s", source, job_id, exc)
                _write_error(job_id, f"{type(exc).__name__}: {exc}")
                err += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("[%s] detail failed for %s: %s", source, job_id, exc)
                _write_error(job_id, f"{type(exc).__name__}: {exc}")
                err += 1
            conn.commit()
            if i % 10 == 0 or i == len(job_ids):
                log.info("[%s] progress: %d/%d  ok=%d err=%d",
                         source, i, len(job_ids), ok, err)
            sleep_jitter = getattr(adapter, "sleep_jitter", None)
            if sleep_jitter:
                sleep_jitter()

    if blocked:
        typer.echo(
            f"[{source}] Enrich BLOCKED. ok={ok}  err={err}  "
            f"remaining={len(job_ids) - ok - err}. Wait, then rerun."
        )
        return 2
    typer.echo(f"[{source}] Enrich done. ok={ok}  err={err}  total={len(job_ids)}")
    return 0


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
