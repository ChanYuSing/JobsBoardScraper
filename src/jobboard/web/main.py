"""FastAPI application — entry point.

Run with:
    uvicorn jobboard.web.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..db import connect, init_schema, sync_fields_full
from ..scheduler import build_scheduler
from .deps import CONFIG_PATH, get_config
from .routes import jobs, runs, schedule, sources, analyse

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    cfg = get_config()
    conn = connect(cfg.storage.sqlite_path)
    try:
        init_schema(conn)
        sync_fields_full(conn, cfg.ai.fields)
    finally:
        conn.close()
    if cfg.scheduler.cron:
        _scheduler = build_scheduler(cfg)
        _scheduler.start()
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="JobBoard", lifespan=lifespan)

app.include_router(jobs.router)
app.include_router(sources.router)
app.include_router(schedule.router)
app.include_router(runs.router)
app.include_router(analyse.router)
