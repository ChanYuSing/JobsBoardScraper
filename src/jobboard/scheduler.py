"""Scheduler daemon.

Reads a single cron expression from config.yaml and fires one job that
runs fetch → enrich for every enabled source, in the order they appear
in the ``sources:`` block of config.yaml.

Each source is attempted independently — an error in one source is logged
and recorded, then execution continues with the next source.

Run standalone:
    python -m jobboard.scheduler

Or embed in FastAPI:
    from jobboard.scheduler import build_scheduler
    scheduler = build_scheduler(cfg)
    # start inside FastAPI lifespan:
    #   scheduler.start()  /  scheduler.shutdown()
"""
from __future__ import annotations

import dataclasses
import logging
import queue as _queue_mod
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Config, load_config
from .db import connect, init_schema
from .scheduler_db import (
    log_run_finish,
    log_run_job,
    log_run_queued,
    log_run_start,
    mark_run_active,
    set_run_jobs_total,
    update_run_jobs_found,
)
from .sources import build_adapter

log = logging.getLogger("jobboard.scheduler")

# ── cancellation / active-run state ─────────────────────────────────────────
_cancel_event = threading.Event()   # set to request cancellation of the running job
_run_active = threading.Event()     # set while _run_all is executing

# ── task queue ─────────────────────────────────────────────────────────────────
@dataclasses.dataclass
class RunTask:
    """A single unit of work placed on the run queue."""
    sources: list[str]
    config_path: str
    db_path: str
    phases: list[str] | None                          # None = fetch + enrich
    queued_ids: dict[tuple[str, str], int]            # (source, phase) -> scheduler_run.id


@dataclasses.dataclass
class ScoreTask:
    """Score all (or new) jobs via AI."""
    config_path: str
    db_path: str
    rescore: bool
    progress_cb: Any | None = None   # callable(done, total, errors) or None
    done_event: threading.Event = dataclasses.field(default_factory=threading.Event)
    run_id: int | None = None        # pre-allocated scheduler_run.id (queued)
    preset_name: str | None = None   # scope label for the Runs page
    preset_params: dict | list[dict] | None = None  # filter scope (single or union of presets)


_QueueItem = RunTask | ScoreTask
_run_queue: _queue_mod.Queue[_QueueItem | None] = _queue_mod.Queue()


def start_queue_worker() -> threading.Thread:
    """Start the background thread that drains _run_queue one run at a time."""
    def _worker():
        while True:
            task = _run_queue.get()
            if task is None:          # shutdown sentinel
                break
            try:
                if isinstance(task, ScoreTask):
                    _run_score(task)
                else:
                    _run_all(task.sources, task.config_path, task.db_path,
                             phases=task.phases, _queued_ids=task.queued_ids)
            except Exception:
                pass
            finally:
                _run_queue.task_done()

    t = threading.Thread(target=_worker, daemon=True, name="run_queue_worker")
    t.start()
    return t


def _cancel_file(db_path: str) -> Path:
    """Path to the cross-process cancel sentinel file (same dir as the DB)."""
    return Path(db_path).parent / ".cancel_run"


def _is_cancelled(db_path: str) -> bool:
    """Return True if a cancel has been requested (in-process event or cancel file).

    Also consumes the cancel file so a single kill request stops one run only.
    """
    if _cancel_event.is_set():
        return True
    cf = _cancel_file(db_path)
    if cf.exists():
        try:
            cf.unlink(missing_ok=True)
        except OSError:
            pass
        _cancel_event.set()
        return True
    return False


# ---------------------------------------------------------------------------
# Job worker — fetch ALL sources, then enrich ALL sources
# ---------------------------------------------------------------------------

def _run_all(
    sources: list[str],
    config_path: str,
    db_path: str,
    phases: list[str] | None = None,
    _queued_ids: dict[tuple[str, str], int] | None = None,
) -> None:
    """Fetch every source in order, then enrich every source in order.

    ``phases`` controls which phases run.  Pass ``["fetch"]`` or
    ``["enrich"]`` to run only one phase; ``None`` (default) runs both.

    ``_queued_ids`` maps (source, phase) -> pre-allocated run_id with
    status='queued'.  The worker transitions each to 'running' before
    executing; if the row was deleted/cancelled in the meantime, the
    phase is skipped.

    Errors in one source are logged and recorded, but the remaining sources
    still run.
    """
    run_fetch  = phases is None or "fetch"  in phases
    run_enrich = phases is None or "enrich" in phases

    _cancel_event.clear()
    # Clear any leftover cancel file from a previous kill request.
    try:
        _cancel_file(db_path).unlink(missing_ok=True)
    except OSError:
        pass
    _run_active.set()
    try:
        cfg = load_config(config_path)
        failed: list[str] = []  # sources that errored in either phase

        # ---- phase 1: fetch all ----
        for source in (sources if run_fetch else []):
            if _is_cancelled(db_path):
                log.warning("Run cancelled — stopping before fetch of %s", source)
                return
            conn = connect(db_path)
            if _queued_ids and (source, "fetch") in _queued_ids:
                run_id = _queued_ids[(source, "fetch")]
                if not mark_run_active(conn, run_id):
                    log.info("[%s] fetch skipped — queued row %d was cancelled", source, run_id)
                    conn.close()
                    continue
            else:
                run_id = log_run_start(conn, source, "fetch")
            log.info("[%s] fetch started  scheduler_run.id=%d", source, run_id)
            inserted = updated = 0
            cancelled = False
            try:
                adapter = build_adapter(source, cfg)
                with adapter:
                    if hasattr(adapter, "search_paginated"):
                        from .sources.jobsdb import db as jdb_db
                        for page, recs, total in adapter.search_paginated():
                            if _is_cancelled(db_path):
                                log.warning("[%s] fetch cancelled mid-page", source)
                                cancelled = True
                                break
                            log.info("[%s] page=%d  cards=%d  total=%s", source, page, len(recs), total)
                            for rec in recs:
                                outcome = jdb_db.upsert_card(conn, rec)
                                if outcome == "inserted":
                                    inserted += 1
                                else:
                                    updated += 1
                                log_run_job(conn, run_id, source, rec.job_id)
                            conn.commit()
                            update_run_jobs_found(conn, run_id, inserted + updated)
                    else:
                        from .sources.linkedin import db as li_db
                        for card in adapter.search():
                            if _is_cancelled(db_path):
                                log.warning("[%s] fetch cancelled mid-card", source)
                                cancelled = True
                                break
                            outcome = li_db.upsert_card(conn, card)
                            if outcome == "inserted":
                                inserted += 1
                            else:
                                updated += 1
                            log_run_job(conn, run_id, source, card.job_id)
                            conn.commit()
                            update_run_jobs_found(conn, run_id, inserted + updated)

                if cancelled:
                    log_run_finish(conn, run_id, status="cancelled",
                                   jobs_found=inserted + updated, error="cancelled by user")
                    return
                log_run_finish(conn, run_id, status="ok", jobs_found=inserted + updated)
                log.info("[%s] fetch ok  inserted=%d  updated=%d", source, inserted, updated)
                from .web.services.jobs import invalidate_filter_options_cache
                invalidate_filter_options_cache()

            except Exception as exc:
                log_run_finish(conn, run_id, status="error", error=f"{type(exc).__name__}: {exc}")
                log.error("[%s] fetch error — skipping to next source: %s", source, exc)
                failed.append(source)
            finally:
                conn.close()

        # ---- phase 2: enrich all ----
        for source in (sources if run_enrich else []):
            if _is_cancelled(db_path):
                log.warning("Run cancelled — stopping before enrich of %s", source)
                return
            conn = connect(db_path)
            if _queued_ids and (source, "enrich") in _queued_ids:
                run_id = _queued_ids[(source, "enrich")]
                if not mark_run_active(conn, run_id):
                    log.info("[%s] enrich skipped — queued row %d was cancelled", source, run_id)
                    conn.close()
                    continue
            else:
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
                set_run_jobs_total(conn, run_id, len(job_ids))
                cancelled = False
                with adapter:
                    for job_id in job_ids:
                        if _is_cancelled(db_path):
                            log.warning("Run cancelled — stopping mid-enrich of %s", source)
                            cancelled = True
                            break
                        try:
                            payload = adapter.fetch_detail(job_id)
                            detail = adapter.parse_detail(payload or {})
                            _write_detail(job_id, detail)
                            log_run_job(conn, run_id, source, job_id)
                            ok += 1
                        except Exception as exc:
                            log.warning("[%s] detail error for %s: %s", source, job_id, exc)
                            _write_error(job_id, f"{type(exc).__name__}: {exc}")
                            err += 1
                        conn.commit()
                        if (ok + err) % 10 == 0:
                            update_run_jobs_found(conn, run_id, ok)
                            log.info("[%s] enrich progress  ok=%d  err=%d", source, ok, err)
                        sleep_jitter = getattr(adapter, "sleep_jitter", None)
                        if sleep_jitter:
                            sleep_jitter()

                if cancelled:
                    log_run_finish(conn, run_id, status="cancelled",
                                   jobs_found=ok, error="cancelled by user")
                    return
                log_run_finish(conn, run_id, status="ok", jobs_found=ok)
                log.info("[%s] enrich ok  ok=%d  err=%d", source, ok, err)

            except Exception as exc:
                log_run_finish(conn, run_id, status="error", error=f"{type(exc).__name__}: {exc}")
                log.error("[%s] enrich error — skipping to next source: %s", source, exc)
                failed.append(source)
            finally:
                conn.close()

        if failed:
            log.error("Run completed with errors in: %s", list(dict.fromkeys(failed)))
    finally:
        _run_active.clear()


# ---------------------------------------------------------------------------
# Public enqueue API — used by web routes and APScheduler bridge
# ---------------------------------------------------------------------------

def enqueue_run(
    sources: list[str],
    config_path: str,
    db_path: str,
    phases: list[str] | None = None,
) -> list[int]:
    """Pre-insert queued rows and push a RunTask onto the queue.

    ``phases`` controls which phases run (None = fetch + enrich).
    Returns the list of allocated scheduler_run IDs.
    """
    resolved_phases = phases if phases is not None else ["fetch", "enrich"]
    conn = connect(db_path)
    queued_ids: dict[tuple[str, str], int] = {}
    try:
        for source in sources:
            for phase in resolved_phases:
                run_id = log_run_queued(conn, source, phase)
                queued_ids[(source, phase)] = run_id
    finally:
        conn.close()
    _run_queue.put(RunTask(sources, config_path, db_path, phases, queued_ids))
    log.info("Enqueued: sources=%s phases=%s", sources, resolved_phases)
    return list(queued_ids.values())


def enqueue_score(
    config_path: str,
    db_path: str,
    rescore: bool = False,
    progress_cb: Any = None,
    preset_name: str | None = None,
    preset_params: dict | list[dict] | None = None,
) -> ScoreTask:
    """Push a ScoreTask onto the shared run queue.  Returns the task object
    so callers can wait on task.done_event.

    The task will start after any in-progress run/score task finishes.
    Pre-inserts a 'queued' scheduler_run row so the task is immediately
    visible on the Runs page.
    """
    conn = connect(db_path)
    try:
        queued_run_id = log_run_queued(conn, "ai", "score", scope=preset_name)
    finally:
        conn.close()
    task = ScoreTask(
        config_path, db_path, rescore, progress_cb,
        run_id=queued_run_id,
        preset_name=preset_name,
        preset_params=preset_params,
    )
    _run_queue.put(task)
    log.info("Score task enqueued  rescore=%s  run_id=%d  scope=%s", rescore, queued_run_id, preset_name)
    return task


def _run_score(task: ScoreTask) -> None:
    """Worker body for a ScoreTask."""
    from .web.services.analyse import get_field_defs, score_all_jobs
    conn = connect(task.db_path)
    if task.run_id is not None:
        if not mark_run_active(conn, task.run_id):
            log.info("Score task run_id=%d was cancelled before it started.", task.run_id)
            conn.close()
            task.done_event.set()
            return
        run_id = task.run_id
    else:
        run_id = log_run_start(conn, "ai", "score")
    try:
        cfg        = load_config(task.config_path).ai
        field_defs = get_field_defs(conn)
        if not field_defs:
            log.warning("Score task skipped — no scoring fields defined.")
            log_run_finish(conn, run_id, status="ok", jobs_found=0)
            return
        original_cb = task.progress_cb

        def _progress(done: int, total: int, errors: int) -> None:
            set_run_jobs_total(conn, run_id, total)
            update_run_jobs_found(conn, run_id, done)
            if original_cb:
                original_cb(done, total, errors)

        scored, errors = score_all_jobs(
            conn, cfg, field_defs,
            rescore=task.rescore,
            progress_cb=_progress,
            run_id=run_id,
            filter_params=task.preset_params,
            cancel_event=_cancel_event,
        )
        log_run_finish(conn, run_id, status="ok", jobs_found=scored)
        log.info("Score task done  scored=%d  errors=%d", scored, errors)
    except Exception as exc:
        log_run_finish(conn, run_id, status="error", error=f"{type(exc).__name__}: {exc}")
        log.error("Score task failed: %s", exc)
        raise
    finally:
        conn.close()
        task.done_event.set()


# ---------------------------------------------------------------------------
# Scheduled enqueue — called by APScheduler on each cron tick
# ---------------------------------------------------------------------------

def _schedule_enqueue(sources: list[str], config_path: str, db_path: str) -> None:
    """Bridge between APScheduler and the run queue.

    If a run is already active or queued, the tick is skipped.
    """
    if _run_active.is_set() or not _run_queue.empty():
        log.warning("Scheduled tick skipped — a run is already active or queued.")
        return
    enqueue_run(sources, config_path, db_path, phases=None)
    log.info("Scheduled run enqueued: sources=%s", sources)
    cfg = load_config(config_path)
    if cfg.ai.auto_score:
        preset_names = cfg.ai.auto_score_preset_names
        if preset_names:
            conn = connect(db_path)
            try:
                from .web.services.filter_presets import get_preset_by_name
                for pname in preset_names:
                    preset = get_preset_by_name(conn, pname)
                    enqueue_score(
                        config_path, db_path, rescore=False,
                        preset_name=pname,
                        preset_params=preset["params"] if preset else None,
                    )
                    log.info("Auto-score enqueued for preset '%s'", pname)
            finally:
                conn.close()
        else:
            enqueue_score(config_path, db_path, rescore=False)
            log.info("Auto-score enqueued after scheduled run")


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
        _schedule_enqueue,
        trigger=CronTrigger.from_crontab(sched_cfg.cron, timezone="Asia/Hong_Kong"),
        kwargs={
            "sources": sources,
            "config_path": config_path,
            "db_path": db_path,
        },
        id="run_all",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=120,
    )
    log.info("Registered: all sources %s  cron='%s'", sources, sched_cfg.cron)

    return scheduler


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m jobboard.scheduler")
    parser.add_argument(
        "config", nargs="?", default="config.yaml", help="Path to config.yaml"
    )
    parser.add_argument(
        "--kill", action="store_true",
        help="Send a cancel signal to the currently running job and exit.",
    )
    args = parser.parse_args()

    if args.kill:
        cfg = load_config(args.config)
        cancel_path = _cancel_file(cfg.storage.sqlite_path)
        cancel_path.touch()
        print("Kill signal sent — the running job will stop at the next checkpoint.")
        print(f"(Sentinel: {cancel_path})")
        return

    config_path: str = args.config
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
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        _shutdown(0, None)


if __name__ == "__main__":
    main()
