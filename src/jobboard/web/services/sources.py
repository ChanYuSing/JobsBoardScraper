"""Sources service — read and write source config params."""
from __future__ import annotations

from typing import Any

import yaml

from ...config import config_write_lock, load_config
from ...sources.linkedin.industries import INDUSTRIES
from ...sources.linkedin.job_functions import JOB_FUNCTIONS

_write_lock = config_write_lock

# ── field metadata for rendering the form ──────────────────────────────────

JOBSDB_FIELDS: list[dict[str, Any]] = [
    {"key": "enabled",          "label": "Enabled",          "type": "toggle"},
    {"key": "keywords",         "label": "Keywords",         "type": "textarea", "hint": "AND search — blank = all jobs"},
    {"key": "location",         "label": "Location",         "type": "text",   "hint": "e.g. Hong Kong SAR"},
    {"key": "daterange",        "label": "Date range (days)","type": "select", "options": ["", "1", "3", "7", "14", "31"], "hint": "blank = all time"},
    {"key": "work_arrangement", "label": "Work arrangement", "type": "select", "options": ["", "on-site", "hybrid", "remote"], "hint": "blank = all"},
    {"key": "work_type",        "label": "Work type",        "type": "select", "options": ["", "full-time", "part-time", "contract", "casual", "internship"], "hint": "blank = all"},
    {"key": "classification",   "label": "Classification",    "type": "cls-picker",     "hint": "Tick industries to filter — leave all unticked to search all"},
    {"key": "subclassification","label": "Sub-classification","type": "cls-picker-sub"},
    {"key": "salary_range",     "label": "Salary range",     "type": "text",   "hint": "e.g. 30000-60000 (monthly HKD)"},
    {"key": "salary_type",      "label": "Salary type",      "type": "select", "options": ["", "Monthly", "Annual"], "hint": "blank = all"},
    {"key": "sort_mode",        "label": "Sort",             "type": "select", "options": ["", "ListDate", "Relevance"], "hint": "blank = default (Relevance)"},
    {"key": "page_size",        "label": "Page size",        "type": "number"},
    {"key": "start_page",       "label": "Start page",       "type": "number", "hint": "resume from page N (1 = start from beginning)"},
    {"key": "max_pages",        "label": "Max pages",        "type": "number", "hint": "0 = no cap"},
]

LINKEDIN_FIELDS: list[dict[str, Any]] = [
    {"key": "enabled",          "label": "Enabled",          "type": "toggle"},
    {"key": "keywords",         "label": "Keywords",         "type": "textarea", "hint": "AND search — required by LinkedIn"},
    {"key": "location",         "label": "Location",         "type": "text",   "hint": "e.g. Hong Kong"},
    {"key": "hours_old",        "label": "Hours old",        "type": "number", "hint": "e.g. 720 = last 30 days; blank = all time"},
    {"key": "job_type",         "label": "Job type",         "type": "select",
     "options": ["", "fulltime", "parttime", "contract", "temporary", "internship", "volunteer", "other"],
     "hint": "blank = all"},
    {"key": "is_remote",        "label": "Remote",           "type": "select", "options": ["", "true", "false", "hybrid"], "hint": "blank = all"},
    {"key": "experience_level", "label": "Experience level", "type": "multiselect",
     "options": [("1","Internship"),("2","Entry"),("3","Associate"),("4","Mid-Senior"),("5","Director"),("6","Executive")]},
    {"key": "easy_apply",       "label": "Easy Apply only",  "type": "select", "options": ["", "true", "false"], "hint": "blank = all"},
    {"key": "sort_by_date",     "label": "Sort by date",     "type": "select", "options": ["", "true", "false"], "hint": "blank = relevance sort"},
    {"key": "geo_id",           "label": "Geo ID",           "type": "text",   "hint": "Optional: LinkedIn numeric geoId for a specific district; leave blank to use Location text above"},
    {"key": "industry_id",      "label": "Industry",         "type": "multiselect",
     "options": [(str(k), v) for k, v in sorted(INDUSTRIES.items(), key=lambda kv: kv[1])],
     "hint": "Leave all unticked to search all industries"},
    {"key": "job_function_id",  "label": "Job function",     "type": "multiselect",
     "options": [(k, v) for k, v in sorted(JOB_FUNCTIONS.items(), key=lambda kv: kv[1])],
     "hint": "Leave all unticked to search all functions"},
]

SOURCE_FIELDS = {"jobsdb": JOBSDB_FIELDS, "linkedin_guest": LINKEDIN_FIELDS}


def _cfg_to_display(cfg_obj: Any) -> dict[str, str]:
    """Convert a config model to flat string values for form display."""
    raw = cfg_obj.model_dump()
    out: dict[str, str] = {}
    for key, val in raw.items():
        if isinstance(val, list):
            # keywords: newline-sep for textarea; others: comma-sep for chips/display
            out[key] = "\n".join(str(v) for v in val) if key == "keywords" else ",".join(str(v) for v in val)
        elif val is None:
            out[key] = ""
        else:
            out[key] = str(val)
    return out


def get_sources(config_path: str) -> dict[str, dict[str, str]]:
    cfg = load_config(config_path)
    result: dict[str, dict[str, str]] = {}
    for name, src in cfg.sources.items():
        result[name] = _cfg_to_display(src)
    return result


def _coerce_value(key: str, raw: str, fields: list[dict[str, Any]]) -> Any:
    """Parse a form string back to the right Python type."""
    if raw == "":
        return None
    # find field type hint
    fdef = next((f for f in fields if f["key"] == key), None)
    ftype = fdef["type"] if fdef else "text"

    if key == "enabled":
        return raw.lower() in ("true", "1", "on", "yes")
    if ftype == "toggle":
        return raw.lower() in ("true", "1", "on", "yes")
    if ftype == "number":
        try:
            return int(raw)
        except ValueError:
            return None
    if key in ("classification", "subclassification", "industry_id"):
        try:
            nums = [int(p.strip()) for p in raw.split(",") if p.strip()]
            return nums if nums else None
        except ValueError:
            return None
    if key == "job_function_id":
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return parts if parts else None
    if key in ("keywords", "experience_level"):
        # support textarea (newline-sep) and legacy comma-sep
        raw_clean = raw.replace("\r", "").replace("\n", ",")
        parts = [p.strip() for p in raw_clean.split(",") if p.strip()]
        if key == "experience_level":
            try:
                parts = [int(p) for p in parts]
            except ValueError:
                pass
        # always return a list so yaml saves as sequence, avoiding type mismatch on reload
        return parts if parts else None
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    return raw


def save_source(config_path: str, source: str, form: dict[str, str]) -> None:
    """Write updated source params back to config.yaml."""
    fields = SOURCE_FIELDS.get(source, [])
    coerced = {
        f["key"]: _coerce_value(f["key"], form.get(f["key"], ""), fields)
        for f in fields
    }

    # ── validation ──────────────────────────────────────────────────────────
    if source == "linkedin_guest":
        keywords = coerced.get("keywords")
        if not keywords:
            raise ValueError("LinkedIn requires at least one keyword — keywords cannot be empty.")

    with _write_lock:
        with open(config_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        raw.setdefault("sources", {}).setdefault(source, {})
        raw["sources"][source].update(coerced)
        with open(config_path, "w", encoding="utf-8") as fh:
            yaml.dump(raw, fh, allow_unicode=True, sort_keys=False, default_flow_style=False)
