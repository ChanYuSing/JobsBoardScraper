"""FastAPI application — entry point.

Run with:
    uvicorn jobboard.web.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from ..db import init_schema, sweep_orphan_runs, sync_fields_full
from ..scheduler import build_scheduler, start_queue_worker
from .deps import CONFIG_PATH, get_config, init_db
from .routes import jobs, runs, schedule, sources, analyse, presets
from .services.analyse import get_score_job_counts

_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    cfg = get_config()
    conn = init_db(cfg.storage.sqlite_path)  # open once — kept alive for the process lifetime
    init_schema(conn)
    sweep_orphan_runs(conn)
    sync_fields_full(conn, cfg.ai.fields)
    get_score_job_counts(conn)  # warm the large table pages into the page cache at startup
    if cfg.scheduler.cron and cfg.scheduler.enabled and cfg.enabled_sources():
        _scheduler = build_scheduler(cfg, config_path=CONFIG_PATH)
        _scheduler.start()
    app.state.scheduler = _scheduler
    start_queue_worker()
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)
    from ..scheduler import _run_queue
    _run_queue.put(None)   # signal the queue worker to exit cleanly
    conn.close()


app = FastAPI(title="JobBoard", lifespan=lifespan)

app.include_router(jobs.router)
app.include_router(sources.router)
app.include_router(schedule.router)
app.include_router(runs.router)
app.include_router(analyse.router)
app.include_router(presets.router)
