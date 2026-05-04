"""Schedule service — cron ↔ UI conversion and run-now trigger."""
from __future__ import annotations

from typing import Any

import yaml

from ...config import config_write_lock, load_config

# Day names for display
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# cron weekday: 0=Mon … 6=Sun  (APScheduler/standard cron)


def cron_to_ui(cron: str) -> dict[str, Any]:
    """Parse '0 1 * * 1,3,5' → {minute:0, hour:1, days:[1,3,5]} (empty days = every day)."""
    parts = cron.strip().split()
    if len(parts) != 5:
        return {"minute": 0, "hour": 1, "days": []}
    minute_s, hour_s, _, _, dow_s = parts
    try:
        minute = int(minute_s)
        hour = int(hour_s)
    except ValueError:
        minute, hour = 0, 1
    if dow_s == "*":
        days = []
    else:
        try:
            days = [int(d) for d in dow_s.split(",")]
        except ValueError:
            days = []
    return {"minute": minute, "hour": hour, "days": days}


def ui_to_cron(hour: int, minute: int, days: list[int]) -> str:
    dow = ",".join(str(d) for d in sorted(days)) if days else "*"
    return f"{minute} {hour} * * {dow}"


def get_schedule(config_path: str) -> dict[str, Any]:
    cfg = load_config(config_path)
    sched = cfg.scheduler
    ui = cron_to_ui(sched.cron or "0 1 * * *")
    enabled = cfg.enabled_sources()
    return {
        "hour": ui["hour"],
        "minute": ui["minute"],
        "days": ui["days"],
        "order": [s for s in (sched.order or enabled) if s in enabled],
        "all_sources": enabled,
        "enabled": sched.enabled,
    }


def toggle_schedule_enabled(config_path: str, enabled: bool) -> None:
    with config_write_lock:
        with open(config_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        raw.setdefault("scheduler", {})
        raw["scheduler"]["enabled"] = enabled
        with open(config_path, "w", encoding="utf-8") as fh:
            yaml.dump(raw, fh, allow_unicode=True, sort_keys=False, default_flow_style=False)


def save_schedule(config_path: str, hour: int, minute: int, days: list[int],
                  order: list[str]) -> None:
    cron = ui_to_cron(hour, minute, days)
    with config_write_lock:
        with open(config_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        raw.setdefault("scheduler", {})
        raw["scheduler"]["cron"] = cron
        raw["scheduler"]["order"] = order
        with open(config_path, "w", encoding="utf-8") as fh:
            yaml.dump(raw, fh, allow_unicode=True, sort_keys=False, default_flow_style=False)


def get_scraper(config_path: str) -> dict[str, Any]:
    cfg = load_config(config_path)
    s = cfg.scraper
    return {
        "request_timeout_seconds": s.request_timeout_seconds,
        "retries": s.retries,
        "jitter_ms_min": s.jitter_ms[0],
        "jitter_ms_max": s.jitter_ms[1],
        "user_agent": s.user_agent,
    }


def save_scraper(
    config_path: str,
    timeout: int,
    retries: int,
    jitter_min: int,
    jitter_max: int,
    user_agent: str,
) -> None:
    with config_write_lock:
        with open(config_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        raw.setdefault("scraper", {})
        raw["scraper"]["request_timeout_seconds"] = timeout
        raw["scraper"]["retries"] = retries
        raw["scraper"]["jitter_ms"] = [jitter_min, jitter_max]
        raw["scraper"]["user_agent"] = user_agent
        with open(config_path, "w", encoding="utf-8") as fh:
            yaml.dump(raw, fh, allow_unicode=True, sort_keys=False, default_flow_style=False)


def get_last_runs(conn) -> dict[str, dict[str, Any]]:
    """Return the most recent scheduler_run row per source."""
    rows = conn.execute(
        """
        SELECT source, phase, started_at, status, jobs_found, error
        FROM scheduler_run
        WHERE id IN (
            SELECT MAX(id) FROM scheduler_run GROUP BY source
        )
        ORDER BY source
        """
    ).fetchall()
    result = {}
    for r in rows:
        result[r[0]] = {
            "phase": r[1],
            "started_at": r[2],
            "status": r[3],
            "jobs_found": r[4],
            "error": r[5],
        }
    return result
