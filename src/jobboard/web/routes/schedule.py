"""Schedule routes."""
from __future__ import annotations

import sqlite3
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..deps import CONFIG_PATH, get_db, templates
from ..services.schedule import (
    DAY_NAMES, get_schedule, get_scraper, get_ai_auto_score,
    get_ai_score_preset_names, save_schedule, save_scraper,
    save_ai_score_settings, toggle_schedule_enabled,
)

router = APIRouter()


@router.get("/schedule", response_class=HTMLResponse)
def schedule_page(
    request: Request,
    flash: str = "",
    flash_type: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    from ...scheduler import _run_active
    from ..services.filter_presets import list_presets
    sched = get_schedule(CONFIG_PATH)
    scraper = get_scraper(CONFIG_PATH)
    auto_score = get_ai_auto_score(CONFIG_PATH)
    auto_score_preset_names = get_ai_score_preset_names(CONFIG_PATH)
    presets = list_presets(conn)
    return templates.TemplateResponse(
        request,
        "schedule.html",
        {
            "active": "schedule",
            "sched": sched,
            "day_names": DAY_NAMES,
            "scraper": scraper,
            "auto_score": auto_score,
            "auto_score_preset_names": auto_score_preset_names,
            "presets": presets,
            "running": _run_active.is_set(),
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
        save_schedule(CONFIG_PATH, hour, minute, days, order)
        return RedirectResponse("/schedule?flash=Schedule+saved", status_code=303)
    except Exception as exc:
        return RedirectResponse(
            f"/schedule?flash={quote_plus(str(exc)[:120])}&flash_type=error", status_code=303
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
            f"/schedule?flash={quote_plus(str(exc)[:120])}&flash_type=error", status_code=303
        )


@router.post("/schedule/run-all")
def run_all_now():
    """Enqueue an immediate run for all enabled sources."""
    from ...scheduler import enqueue_run
    from ..deps import get_config

    cfg = get_config()
    enabled = set(cfg.enabled_sources())
    order = cfg.scheduler.order
    if order is not None:
        sources = [s for s in order if s in enabled]
    else:
        sources = cfg.enabled_sources()
    if not sources:
        return RedirectResponse(
            "/schedule?flash=No+enabled+sources+to+run.&flash_type=error",
            status_code=303,
        )
    enqueue_run(sources, CONFIG_PATH, cfg.storage.sqlite_path, phases=None)
    return RedirectResponse(
        "/schedule?flash=Run+queued+for+all+sources.+Check+Runs+page+for+status.",
        status_code=303,
    )


@router.post("/schedule/toggle-enabled")
def toggle_enabled(request: Request):
    sched = get_schedule(CONFIG_PATH)
    new_enabled = not sched["enabled"]
    toggle_schedule_enabled(CONFIG_PATH, new_enabled)

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        try:
            if new_enabled:
                scheduler.resume_job("run_all")
            else:
                scheduler.pause_job("run_all")
        except Exception:
            pass
    elif new_enabled:
        # Scheduler wasn't running — start it now
        from ...scheduler import build_scheduler
        from ..deps import get_config
        cfg = get_config()
        if cfg.scheduler.cron:
            try:
                new_sched = build_scheduler(cfg, config_path=CONFIG_PATH)
                new_sched.start()
                request.app.state.scheduler = new_sched
            except Exception:
                pass

    msg = "Scheduling+enabled" if new_enabled else "Scheduling+disabled"
    return RedirectResponse(f"/schedule?flash={msg}", status_code=303)


@router.post("/schedule/kill-run")
def kill_run():
    from ...scheduler import _cancel_event, _cancel_file
    from ..deps import get_config
    _cancel_event.set()
    try:
        cfg = get_config()
        _cancel_file(cfg.storage.sqlite_path).touch()
    except Exception:
        pass
    return RedirectResponse(
        "/schedule?flash=Kill+signal+sent.+Run+will+stop+at+the+next+checkpoint.",
        status_code=303,
    )


@router.post("/schedule/save-all")
async def save_all(request: Request):
    """Unified save for run settings + scraper settings + AI scoring."""
    form = await request.form()
    try:
        hour   = int(form.get("hour", 1))
        minute = int(form.get("minute", 0))
        days   = [int(d) for d in form.getlist("days")]
        order  = [s for s in form.getlist("order") if s]
        save_schedule(CONFIG_PATH, hour, minute, days, order)
        save_scraper(
            CONFIG_PATH,
            timeout=int(form.get("request_timeout_seconds", 20)),
            retries=int(form.get("retries", 3)),
            jitter_min=int(form.get("jitter_ms_min", 1000)),
            jitter_max=int(form.get("jitter_ms_max", 3000)),
            user_agent=str(form.get("user_agent", "")),
        )
        save_ai_score_settings(
            CONFIG_PATH,
            auto_score=form.get("auto_score") == "1",
            preset_names=list(form.getlist("auto_score_preset_name")),
        )
        # Apply new cron to the live APScheduler job immediately (no restart needed).
        scheduler = getattr(request.app.state, "scheduler", None)
        if scheduler is not None:
            from apscheduler.triggers.cron import CronTrigger
            from ..deps import get_config
            cfg = get_config()
            if cfg.scheduler.cron:
                try:
                    scheduler.reschedule_job(
                        "run_all",
                        trigger=CronTrigger.from_crontab(
                            cfg.scheduler.cron, timezone="Asia/Hong_Kong"
                        ),
                    )
                except Exception:
                    pass  # job may not exist yet; restart will pick it up
        return RedirectResponse("/schedule?flash=Settings+saved", status_code=303)
    except Exception as exc:
        return RedirectResponse(
            f"/schedule?flash={quote_plus(str(exc)[:120])}&flash_type=error", status_code=303
        )
