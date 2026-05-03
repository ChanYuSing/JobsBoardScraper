"""Scheduler daemon.

Reads a single cron expression from config.yaml and fires one job that
runs fetch → enrich for every enabled source, in the order they appear
in the ``sources:`` block of config.yaml.

Each source is attempted independently — an error in one source is logged
and recorded, then execution continues with the next source.  After all
sources have been attempted, if any failed, a summary exception is raised
so APScheduler fires EVENT_JOB_ERROR and the retry listener replays the
full sequence up to ``max_retries`` times.

Run standalone:
    python -m jobboard.scheduler

Or embed in FastAPI (future web UI):
    from jobboard.scheduler import build_scheduler
    scheduler = build_scheduler(cfg)
    # start inside FastAPI lifespan:
    #   scheduler.start()  /  scheduler.shutdown()
"""
from __future__ import annotations

import logging
import signal
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .config import Config, load_config
from .db import connect, init_schema
from .scheduler_db import log_run_finish, log_run_start
from .sources import build_adapter

log = logging.getLogger("jobboard.scheduler")


class _PartialFailure(Exception):
    """Raised when one or more sources fail; carries the list of failed sources
    so the retry listener can replay only those instead of all sources."""
    def __init__(self, failed: list[str]) -> None:
        super().__init__(f"Sources failed (will retry): {failed}")
        self.failed = failed


# ---------------------------------------------------------------------------
# Job worker — fetch ALL sources, then enrich ALL sources
# ---------------------------------------------------------------------------

def _run_all(sources: list[str], config_path: str, db_path: str, _meta: dict | None = None) -> None:
    """Fetch every source in order, then enrich every source in order.

    Errors in one source are logged and recorded, but the remaining sources
    still run.  After all sources have been attempted, if any failed,
    a summary exception is raised so APScheduler fires EVENT_JOB_ERROR
    and the retry listener can schedule a replay of the full sequence.
    """
    cfg = load_config(config_path)
    failed: list[str] = []  # sources that errored in either phase

    # ---- phase 1: fetch all ----
    for source in sources:
        conn = connect(db_path)
        init_schema(conn)
        run_id = log_run_start(conn, source, "fetch")
        log.info("[%s] fetch started  scheduler_run.id=%d", source, run_id)
        inserted = updated = 0
        try:
            adapter = build_adapter(source, cfg)
            with adapter:
                if hasattr(adapter, "search_paginated"):
                    from .sources.jobsdb import db as jdb_db
                    for page, recs, total in adapter.search_paginated():
                        log.info("[%s] page=%d  cards=%d  total=%s", source, page, len(recs), total)
                        for rec in recs:
                            outcome = jdb_db.upsert_card(conn, rec, run_id=None)
                            if outcome == "inserted":
                                inserted += 1
                            else:
                                updated += 1
                        conn.commit()
                else:
                    from .sources.linkedin import db as li_db
                    for card in adapter.search():
                        outcome = li_db.upsert_card(conn, card, run_id=None)
                        if outcome == "inserted":
                            inserted += 1
                        else:
                            updated += 1
                    conn.commit()

            log_run_finish(conn, run_id, status="ok", jobs_found=inserted + updated)
            log.info("[%s] fetch ok  inserted=%d  updated=%d", source, inserted, updated)

        except Exception as exc:
            log_run_finish(conn, run_id, status="error", error=f"{type(exc).__name__}: {exc}")
            log.error("[%s] fetch error — skipping to next source: %s", source, exc)
            failed.append(source)
        finally:
            conn.close()

    # ---- phase 2: enrich all ----
    for source in sources:
        conn = connect(db_path)
        init_schema(conn)
        run_id = log_run_start(conn, source, "enrich")
        log.info("[%s] enrich started  scheduler_run.id=%d", source, run_id)
        ok = err = 0
        try:
            adapter = build_adapter(source, cfg)
            if getattr(adapter, "enrich_inline", False):
                log_run_finish(conn, run_id, status="ok", jobs_found=0)
                log.info("[%s] enrich_inline — nothing to do", source)
                continue

            if source == "jobsdb":
                from .sources.jobsdb import db as jdb_db
                job_ids = jdb_db.jobs_needing_enrich(conn, stale_days=None, limit=None)

                def _write_detail(jid: str, detail: Any) -> None:
                    jdb_db.upsert_detail(conn, jid, detail)

                def _write_error(jid: str, error: str) -> None:
                    jdb_db.record_detail_error(conn, jid, error)

            elif source == "linkedin_guest":
                from .sources.linkedin import db as li_db
                job_ids = li_db.jobs_needing_enrich(conn, stale_days=None, limit=None)

                def _write_detail(jid: str, detail: Any) -> None:  # type: ignore[misc]
                    li_db.upsert_detail(conn, jid, detail)

                def _write_error(jid: str, error: str) -> None:  # type: ignore[misc]
                    li_db.record_detail_error(conn, jid, error)
            else:
                log.warning("[%s] no enrich path; skipping", source)
                log_run_finish(conn, run_id, status="ok", jobs_found=0)
                continue

            log.info("[%s] enriching %d job(s)", source, len(job_ids))
            with adapter:
                for job_id in job_ids:
                    try:
                        payload = adapter.fetch_detail(job_id)
                        detail = adapter.parse_detail(payload or {})
                        _write_detail(job_id, detail)
                        ok += 1
                    except Exception as exc:
                        log.warning("[%s] detail error for %s: %s", source, job_id, exc)
                        _write_error(job_id, f"{type(exc).__name__}: {exc}")
                        err += 1
                    conn.commit()
                    sleep_jitter = getattr(adapter, "sleep_jitter", None)
                    if sleep_jitter:
                        sleep_jitter()

            log_run_finish(conn, run_id, status="ok", jobs_found=ok)
            log.info("[%s] enrich ok  ok=%d  err=%d", source, ok, err)

        except Exception as exc:
            log_run_finish(conn, run_id, status="error", error=f"{type(exc).__name__}: {exc}")
            log.error("[%s] enrich error — skipping to next source: %s", source, exc)
            failed.append(source)
        finally:
            conn.close()

    # ---- raise only the failed sources so the retry replays just those ----
    if failed:
        raise _PartialFailure(list(dict.fromkeys(failed)))  # deduplicate, preserve order


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

def _make_error_listener(
    scheduler: BackgroundScheduler,
    config_path: str,
    db_path: str,
    retry_delay_minutes: int,
    max_retries: int,
) -> Any:
    """Return an EVENT_JOB_ERROR listener that schedules one-shot retries."""

    def _on_error(event: JobExecutionEvent) -> None:
        job = scheduler.get_job(event.job_id)
        if job is None:
            return

        meta: dict = job.kwargs.get("_meta", {})
        if not meta:
            return

        retries: int = meta.get("retries", 0)
        all_sources: list[str] = meta["sources"]

        # Only retry the sources that actually failed; leave successful ones alone.
        exc = event.exception
        retry_sources = exc.failed if isinstance(exc, _PartialFailure) else all_sources

        if retries >= max_retries:
            log.error("Exhausted after %d retries — giving up. sources=%s", max_retries, retry_sources)
            conn = connect(db_path)
            init_schema(conn)
            for source in retry_sources:
                run_id = log_run_start(conn, source, "fetch+enrich")
                log_run_finish(
                    conn, run_id,
                    status="exhausted",
                    error=f"Gave up after {max_retries} retries: {exc}",
                )
            conn.close()
            return

        fire_at = datetime.now(timezone.utc) + timedelta(minutes=retry_delay_minutes)
        retry_num = retries + 1
        log.warning(
            "Retry #%d/%d scheduled at %s  failed sources=%s",
            retry_num, max_retries, fire_at.strftime("%H:%M UTC"), retry_sources,
        )

        scheduler.add_job(
            _run_all,
            trigger=DateTrigger(run_date=fire_at),
            kwargs={
                "sources": retry_sources,
                "config_path": config_path,
                "db_path": db_path,
                "_meta": {"sources": all_sources, "retries": retry_num},
            },
            id=f"retry_all_{retry_num}",
            replace_existing=True,
            misfire_grace_time=None,
        )

    return _on_error


# ---------------------------------------------------------------------------
# Scheduler builder
# ---------------------------------------------------------------------------

def build_scheduler(cfg: Config, config_path: str = "config.yaml") -> BackgroundScheduler:
    """Build and return a configured (but not yet started) BackgroundScheduler."""
    db_path = cfg.storage.sqlite_path
    sched_cfg = cfg.scheduler

    if not sched_cfg.cron:
        raise ValueError("scheduler.cron is not set in config.yaml")

    # Sources to run — use explicit order list if set, else config.yaml insertion order.
    # Only enabled sources are included either way.
    enabled = set(cfg.enabled_sources())
    if sched_cfg.order is not None:
        sources = [s for s in sched_cfg.order if s in enabled]
        unlisted = [s for s in cfg.enabled_sources() if s not in sched_cfg.order]
        if unlisted:
            log.warning("Enabled sources not in scheduler.order (will be skipped): %s", unlisted)
    else:
        sources = cfg.enabled_sources()
    if not sources:
        raise ValueError("No enabled sources found in config.yaml")

    scheduler = BackgroundScheduler(timezone="Asia/Hong_Kong")

    scheduler.add_job(
        _run_all,
        trigger=CronTrigger.from_crontab(sched_cfg.cron, timezone="Asia/Hong_Kong"),
        kwargs={
            "sources": sources,
            "config_path": config_path,
            "db_path": db_path,
            "_meta": {"sources": sources, "retries": 0},
        },
        id="run_all",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=120,
    )
    log.info("Registered: all sources %s  cron='%s'", sources, sched_cfg.cron)

    scheduler.add_listener(
        _make_error_listener(
            scheduler, config_path, db_path,
            sched_cfg.retry_delay_minutes, sched_cfg.max_retries,
        ),
        EVENT_JOB_ERROR,
    )

    return scheduler


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main(config_path: str = "config.yaml") -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(config_path)

    # Ensure DB + scheduler_run table exist before the first job fires.
    conn = connect(cfg.storage.sqlite_path)
    init_schema(conn)
    conn.close()

    scheduler = build_scheduler(cfg, config_path=config_path)
    scheduler.start()
    log.info("Scheduler started. Press Ctrl+C to stop.")

    def _shutdown(signum: int, frame: Any) -> None:  # noqa: ANN001
        log.info("Shutdown signal received — stopping scheduler.")
        scheduler.shutdown(wait=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Block the main thread — compatible with Windows (no signal.pause).
    try:
        while True:
            import time
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        _shutdown(0, None)


if __name__ == "__main__":
    config_arg = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    main(config_arg)
