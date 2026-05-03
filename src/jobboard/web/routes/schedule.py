"""Schedule routes."""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import sqlite3
from ..deps import CONFIG_PATH, get_db
from ..services.schedule import DAY_NAMES, get_last_runs, get_schedule, get_scraper, save_schedule, save_scraper

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parents[1] / "templates"))


@router.get("/schedule", response_class=HTMLResponse)
def schedule_page(
    request: Request,
    flash: str = "",
    flash_type: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    sched = get_schedule(CONFIG_PATH)
    last_runs = get_last_runs(conn)
    scraper = get_scraper(CONFIG_PATH)
    return templates.TemplateResponse(
        request,
        "schedule.html",
        {
            "active": "schedule",
            "sched": sched,
            "last_runs": last_runs,
            "day_names": DAY_NAMES,
            "scraper": scraper,
            "flash": flash,
            "flash_type": flash_type,
        },
    )


@router.post("/schedule/save")
async def save(request: Request):
    form = await request.form()
    try:
        hour   = int(form.get("hour", 1))
        minute = int(form.get("minute", 0))
        days   = [int(d) for d in form.getlist("days")]
        order  = [s for s in form.getlist("order") if s]
        retry_delay = int(form.get("retry_delay_minutes", 60))
        max_retries = int(form.get("max_retries", 3))
        save_schedule(CONFIG_PATH, hour, minute, days, order, retry_delay, max_retries)
        return RedirectResponse("/schedule?flash=Schedule+saved", status_code=303)
    except Exception as exc:
        return RedirectResponse(
            f"/schedule?flash={str(exc)[:120]}&flash_type=error", status_code=303
        )


@router.post("/schedule/scraper-save")
async def scraper_save(request: Request):
    form = await request.form()
    try:
        save_scraper(
            CONFIG_PATH,
            timeout=int(form.get("request_timeout_seconds", 20)),
            retries=int(form.get("retries", 3)),
            jitter_min=int(form.get("jitter_ms_min", 1000)),
            jitter_max=int(form.get("jitter_ms_max", 3000)),
            user_agent=str(form.get("user_agent", "")),
        )
        return RedirectResponse("/schedule?flash=Scraper+settings+saved", status_code=303)
    except Exception as exc:
        return RedirectResponse(
            f"/schedule?flash={str(exc)[:120]}&flash_type=error", status_code=303
        )


@router.post("/schedule/run-all")
def run_all_now():
    """Trigger _run_all for all enabled sources immediately."""
    from ...scheduler import _run_all
    from ..deps import get_config

    cfg = get_config()
    sources = cfg.scheduler.order or cfg.enabled_sources()
    db_path = cfg.storage.sqlite_path

    def _go():
        try:
            _run_all(sources, CONFIG_PATH, db_path)
        except Exception:
            pass

    threading.Thread(target=_go, daemon=True, name="run_all_now").start()
    return RedirectResponse(
        "/schedule?flash=Run+started+for+all+sources.+Check+Runs+page+for+status.",
        status_code=303,
    )
