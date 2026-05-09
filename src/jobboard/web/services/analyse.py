"""Analyse service — job fetching and prompt building for AI scoring."""
from __future__ import annotations

import json
import logging
import random
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

import yaml

from ...config import AiCfg, FieldCfg, config_write_lock, load_config
from ...scheduler_db import log_run_job
from ...db import sync_fields_full
from .ai_client import score_job, PROVIDER_PARAMS

_write_lock = config_write_lock

# ---------------------------------------------------------------------------
# Internal per-job prompt (not user-editable)
# ---------------------------------------------------------------------------

_SKIP_COLS = frozenset({
    # unparseable raw blobs only
    "raw_card_json", "raw_detail_json", "description_html",
    "detail_fetched_at", "detail_error",
})

# Ordered list of all selectable columns for the prompt-fields UI.
ALL_PROMPT_FIELDS: list[dict[str, str]] = [
    {"key": "title",              "label": "Title"},
    {"key": "company",            "label": "Company"},
    {"key": "location",           "label": "Location"},
    {"key": "description_text",   "label": "Description"},
    {"key": "bullet_points",      "label": "Bullet points"},
    {"key": "work_types",         "label": "Work type"},
    {"key": "work_arrangement",   "label": "Work arrangement"},
    {"key": "salary_label",       "label": "Salary"},
    {"key": "listing_date_label", "label": "Date listed (jobsdb)"},
    {"key": "date_posted",        "label": "Date posted (LinkedIn)"},
    {"key": "employment_type",    "label": "Employment type (LinkedIn)"},
    {"key": "seniority_level",    "label": "Seniority (LinkedIn)"},
    {"key": "classification",     "label": "Classification (jobsdb)"},
    {"key": "subclassification",  "label": "Subclassification (jobsdb)"},
    {"key": "teaser",             "label": "Teaser (jobsdb)"},
    {"key": "abstract",           "label": "Abstract (jobsdb)"},
    {"key": "benefit_text",       "label": "Benefits (LinkedIn)"},
    {"key": "job_function",       "label": "Job function (LinkedIn)"},
    {"key": "industries",         "label": "Industries (LinkedIn)"},
    {"key": "num_applicants",     "label": "Applicants (LinkedIn)"},
]

DEFAULT_PROMPT_FIELDS: list[str] = [
    "title", "company", "location", "description_text", "bullet_points",
    "work_types", "work_arrangement", "salary_label",
    "listing_date_label", "date_posted", "employment_type", "seniority_level",
]

JOB_PROMPT = (
    "Candidate CV:\n{cv}\n\n"
    "---\n\n"
    "Job listing:\n{job_info}\n\n"
    "---\n\n"
    "Score this job against the candidate's CV on each criterion below:\n{fields_spec}\n\n"
    "Respond with ONLY valid JSON — no markdown, no explanation, no extra text:\n{json_example}"
)


def _build_job_info(job: dict[str, Any], allowed_fields: list[str]) -> str:
    """Format selected job columns as 'key: value' lines for the AI prompt."""
    skip = _SKIP_COLS | {"job_id", "source"}
    lines = []
    for key in allowed_fields:
        if key in skip:
            continue
        val = job.get(key)
        if val is None or val == "":
            continue
        if key == "bullet_points":
            try:
                bullets = json.loads(val)
                if isinstance(bullets, list):
                    if not bullets:
                        continue
                    lines.append("bullet_points:\n" + "\n".join(f"  - {b}" for b in bullets))
                    continue
            except Exception:
                pass
        lines.append(f"{key}: {val}")
    return "\n".join(lines)


def _pick_preview_job(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Return one random non-dismissed job, strongly preferring enriched ones.

    Queries the DB directly so recency ordering doesn't hide the enriched pool.
    """
    for source, table in (("jobsdb", "job_jobsdb"), ("linkedin_guest", "job_linkedin")):
        skip_cols = _SKIP_COLS | {"job_id"}
        rows = conn.execute(
            f"""
            SELECT j.* FROM {table} j
            LEFT JOIN job_status s ON s.source = ? AND s.job_id = j.job_id
            WHERE COALESCE(s.status, 'new') != 'dismissed'
              AND j.description_text IS NOT NULL AND j.description_text != ''
            ORDER BY RANDOM() LIMIT 20
            """,
            (source,),
        ).fetchall()
        if rows:
            r = random.choice(rows)
            d = {k: r[k] for k in r.keys() if k not in skip_cols}
            d["source"] = source
            return d
    return None


def build_preview_prompts(
    conn: sqlite3.Connection,
    system_prompt: str,
    cv: str,
    field_defs: list[dict[str, Any]],
    prompt_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Pick a random non-dismissed enriched job and return fully assembled prompts."""
    allowed = prompt_fields or DEFAULT_PROMPT_FIELDS
    job = _pick_preview_job(conn)
    if job is None:
        # fall back to any job (nothing enriched yet)
        jobs = get_jobs_for_analysis(conn, max_jobs=50)
        if not jobs:
            return {"error": "No jobs in database"}
        job = random.choice(jobs)
    user_msg = (
        JOB_PROMPT
        .replace("{cv}", cv or "[CV not provided]")
        .replace("{job_info}", _build_job_info(job, allowed))
        .replace("{fields_spec}", build_fields_spec(field_defs))
        .replace("{json_example}", build_json_example(field_defs))
    )
    return {
        "system": system_prompt,
        "user": user_msg,
        "job_title": job.get("title") or "(no title)",
        "job_company": job.get("company") or "(unknown)",
        "source": job.get("source", ""),
    }


# ---------------------------------------------------------------------------
# Field definition CRUD
# ---------------------------------------------------------------------------

_FIELD_NAME_RE = re.compile(r'^[a-z][a-z0-9_]*$')


def _derive_label(name: str) -> str:
    return name.replace("_", " ").title()


def get_field_defs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, name, type, description, sort_order "
        "FROM field_def ORDER BY sort_order, id"
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["label"] = _derive_label(d["name"])
        result.append(d)
    return result


def save_field_defs(conn: sqlite3.Connection, fields: list[dict[str, Any]]) -> list[str]:
    """Full sync submitted fields into SQLite and return list of deleted field names."""
    for f in fields:
        if not _FIELD_NAME_RE.match(f["name"]):
            raise ValueError(
                f"Invalid field name '{f['name']}': use lowercase letters, digits, "
                "underscores only, must start with a letter."
            )
        if f["type"] not in ("int", "str"):
            raise ValueError(f"Invalid type '{f['type']}': must be 'int' or 'str'.")
    field_cfgs = [FieldCfg(name=f["name"], type=f["type"], description=f["description"]) for f in fields]
    return sync_fields_full(conn, field_cfgs)


# ---------------------------------------------------------------------------
# AI config CRUD (YAML-backed)
# ---------------------------------------------------------------------------

def get_ai_config(config_path: str) -> dict[str, Any]:
    cfg = load_config(config_path)
    return {
        "provider":              cfg.ai.provider,
        "model":                 cfg.ai.model,
        "base_url":              cfg.ai.base_url,
        "api_key":               cfg.ai.api_key,
        "api_keys":              dict(cfg.ai.api_keys),
        "models":                dict(cfg.ai.models),
        "base_urls":             dict(cfg.ai.base_urls),
        "provider_params":       dict(cfg.ai.provider_params),
        "provider_param_support": {p: list(keys) for p, keys in PROVIDER_PARAMS.items()},
        "system_prompt":         cfg.ai.system_prompt,
        "cv":                    cfg.ai.cv,
        "fields":                [{"name": f.name, "type": f.type, "description": f.description} for f in cfg.ai.fields],
        "prompt_fields":         cfg.ai.prompt_fields or DEFAULT_PROMPT_FIELDS,
    }


def save_ai_config(
    config_path: str,
    system_prompt: str | None = None,
    cv: str | None = None,
    fields: list[dict[str, Any]] | None = None,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    provider_params: dict | None = None,   # {provider: {temperature, max_tokens, ...}}
    prompt_fields: list[str] | None = None,
) -> None:
    with _write_lock:
        with open(config_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        raw.setdefault("ai", {})
        if provider  is not None: raw["ai"]["provider"] = provider
        if model     is not None: raw["ai"]["model"]    = model
        if base_url  is not None: raw["ai"]["base_url"] = base_url
        if api_key is not None:
            raw["ai"]["api_key"] = api_key
            _prov = raw["ai"].get("provider")
            if _prov:
                raw["ai"].setdefault("api_keys", {})[_prov] = api_key
        _prov2 = raw["ai"].get("provider")
        if _prov2:
            if model    is not None: raw["ai"].setdefault("models",    {})[_prov2] = model
            if base_url is not None: raw["ai"].setdefault("base_urls", {})[_prov2] = base_url
        # Per-provider params are the single runtime store — never write to global fields
        if provider_params is not None:
            raw["ai"].setdefault("provider_params", {}).update(provider_params)
        if system_prompt is not None: raw["ai"]["system_prompt"] = system_prompt
        if cv            is not None: raw["ai"]["cv"]            = cv
        if fields is not None:
            raw["ai"]["fields"] = [{"name": f["name"], "type": f["type"], "description": f["description"]} for f in fields]
        if prompt_fields is not None:
            raw["ai"]["prompt_fields"] = prompt_fields
        with open(config_path, "w", encoding="utf-8") as fh:
            yaml.dump(raw, fh, allow_unicode=True, sort_keys=False, default_flow_style=False)


# ---------------------------------------------------------------------------
# Batch scoring
# ---------------------------------------------------------------------------

def _already_scored_keys(
    conn: sqlite3.Connection,
    field_names: list[str],
) -> set[tuple[str, str]]:
    """Return (source, job_id) pairs that already have all fields scored."""
    if not field_names:
        return set()
    placeholders = ",".join("?" * len(field_names))
    rows = conn.execute(
        f"""
        SELECT ja.source, ja.job_id
        FROM job_analysis ja
        JOIN field_def fd ON fd.id = ja.field_id
        WHERE fd.name IN ({placeholders})
        GROUP BY ja.source, ja.job_id
        HAVING COUNT(DISTINCT fd.name) = ?
        """,
        (*field_names, len(field_names)),
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def score_all_jobs(
    conn: sqlite3.Connection,
    cfg: AiCfg,
    field_defs: list[dict[str, Any]],
    *,
    rescore: bool = False,
    progress_cb: Any = None,
    run_id: int | None = None,
    filter_params: dict | list[dict] | None = None,
    cancel_event: Any = None,   # threading.Event — checked between jobs
) -> tuple[int, int]:
    """Score non-dismissed enriched jobs.  Returns (scored, errors).

    Args:
        rescore:       When True, re-score jobs that already have all fields stored.
        progress_cb:   Optional callable(done: int, total: int, errors: int).
        filter_params: Restrict scoring scope. A single params dict, or a list of dicts
                       whose matching jobs are unioned together.
    """
    log = logging.getLogger(__name__)

    field_names = [f["name"] for f in field_defs]
    if not field_names:
        raise ValueError("No scoring fields defined — add fields before scoring.")

    jobs = get_jobs_for_analysis(conn, max_jobs=999_999)

    if filter_params:
        from .jobs import build_jobs_filter
        params_list = filter_params if isinstance(filter_params, list) else [filter_params]
        scope_keys: set[tuple[str, str]] = set()
        for fp in params_list:
            f_where, f_bind = build_jobs_filter(fp)
            f_clause = ("WHERE " + " AND ".join(f_where)) if f_where else ""
            rows = conn.execute(
                f"SELECT j.source, j.job_id FROM job_all j"
                f" LEFT JOIN job_status js ON js.source = j.source AND js.job_id = j.job_id"
                f" {f_clause}",
                f_bind,
            ).fetchall()
            scope_keys.update((r[0], r[1]) for r in rows)
        jobs = [j for j in jobs if (j["source"], j["job_id"]) in scope_keys]

    if not rescore:
        scored_keys = _already_scored_keys(conn, field_names)
        jobs = [j for j in jobs if (j["source"], j["job_id"]) not in scored_keys]

    _MAX_CONSECUTIVE_ERRORS = 10

    total   = len(jobs)
    scored  = 0
    errors  = 0
    consecutive_errors = 0

    system        = cfg.system_prompt
    cv            = cfg.cv
    allowed_fields = cfg.prompt_fields or DEFAULT_PROMPT_FIELDS

    for i, job in enumerate(jobs):
        if cancel_event is not None and cancel_event.is_set():
            log.info("Score cancelled after %d/%d jobs.", i, total)
            break
        user_msg = (
            JOB_PROMPT
            .replace("{cv}", cv or "[CV not provided]")
            .replace("{job_info}", _build_job_info(job, allowed_fields))
            .replace("{fields_spec}", build_fields_spec(field_defs))
            .replace("{json_example}", build_json_example(field_defs))
        )
        try:
            result = score_job(cfg, system, user_msg)
            save_job_analysis(conn, job["source"], job["job_id"], result)
            if run_id is not None:
                log_run_job(conn, run_id, job["source"], job["job_id"])
            scored += 1
            consecutive_errors = 0
        except Exception as exc:
            errors += 1
            consecutive_errors += 1
            log.warning(
                "Score failed [%s/%s]: %s (%d/%d consecutive)",
                job["source"], job["job_id"], exc,
                consecutive_errors, _MAX_CONSECUTIVE_ERRORS,
            )
            if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                log.error("Aborting score after %d consecutive errors.", consecutive_errors)
                if progress_cb is not None:
                    try:
                        progress_cb(i + 1, total, errors)
                    except Exception:
                        pass
                raise

        if progress_cb is not None:
            try:
                progress_cb(i + 1, total, errors)
            except Exception:
                pass

    return scored, errors


# ---------------------------------------------------------------------------
# Job fetching
# ---------------------------------------------------------------------------

def get_score_job_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Return total non-dismissed jobs and how many need scoring (missing ≥1 field).

    "Scored" means every current field_def has a row in job_analysis — matching
    exactly the logic used by _already_scored_keys / score_all_jobs.
    """
    field_names = [r[0] for r in conn.execute("SELECT name FROM field_def").fetchall()]
    n_fields = len(field_names)

    total = 0
    scored = 0
    for source, table in (("jobsdb", "job_jobsdb"), ("linkedin_guest", "job_linkedin")):
        t = conn.execute(
            f"SELECT COUNT(*) FROM {table} j"
            " LEFT JOIN job_status s ON s.source = ? AND s.job_id = j.job_id"
            " WHERE COALESCE(s.status, 'new') != 'dismissed'",
            (source,),
        ).fetchone()[0]
        total += t
        if n_fields > 0:
            placeholders = ",".join("?" * n_fields)
            s = conn.execute(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT ja.job_id
                    FROM job_analysis ja
                    JOIN {table} j ON j.job_id = ja.job_id
                    JOIN field_def fd ON fd.id = ja.field_id
                    LEFT JOIN job_status js
                        ON js.source = ? AND js.job_id = ja.job_id
                    WHERE ja.source = ?
                      AND fd.name IN ({placeholders})
                      AND COALESCE(js.status, 'new') != 'dismissed'
                    GROUP BY ja.job_id
                    HAVING COUNT(DISTINCT fd.name) = ?
                )
                """,
                (source, source, *field_names, n_fields),
            ).fetchone()[0]
            scored += s
    return {"total": total, "new": total - scored}


def get_jobs_for_analysis(conn: sqlite3.Connection, max_jobs: int) -> list[dict[str, Any]]:
    result = []
    for source, table in (("jobsdb", "job_jobsdb"), ("linkedin_guest", "job_linkedin")):
        rows = conn.execute(
            f"""
            SELECT j.* FROM {table} j
            LEFT JOIN job_status s ON s.source = ? AND s.job_id = j.job_id
            WHERE COALESCE(s.status, 'new') != 'dismissed'
            ORDER BY j.first_seen_at DESC
            """,
            (source,),
        ).fetchall()
        for r in rows:
            d = {k: r[k] for k in r.keys() if k not in _SKIP_COLS}
            d["source"] = source
            result.append(d)
    result.sort(key=lambda x: x.get("first_seen_at", ""), reverse=True)
    return result[:max_jobs]


# ---------------------------------------------------------------------------
# EAV storage
# ---------------------------------------------------------------------------

def save_job_analysis(
    conn: sqlite3.Connection,
    source: str,
    job_id: str,
    field_results: dict[str, Any],
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for name, value in field_results.items():
        row = conn.execute(
            "SELECT id, type FROM field_def WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            continue
        field_id, field_type = row[0], row[1]
        value_int = _clamp(value) if field_type == "int" else None
        value_str = str(value)[:500] if field_type == "str" else None
        conn.execute(
            """
            INSERT INTO job_analysis (source, job_id, field_id, value_int, value_str, analysed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, job_id, field_id) DO UPDATE SET
                value_int=excluded.value_int,
                value_str=excluded.value_str,
                analysed_at=excluded.analysed_at
            """,
            (source, job_id, field_id, value_int, value_str, now),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def build_fields_spec(field_defs: list[dict[str, Any]]) -> str:
    lines = []
    for fd in field_defs:
        type_hint = "integer 1-10" if fd["type"] == "int" else "text (1-2 sentences)"
        lines.append(f'- "{fd["name"]}" ({type_hint}): {fd["description"]}')
    return "\n".join(lines)


def build_json_example(field_defs: list[dict[str, Any]]) -> str:
    _example_ints = [5, 8, 3, 7, 6, 4, 9, 2]
    int_idx = 0
    parts = []
    for fd in field_defs:
        if fd["type"] == "int":
            parts.append(f'"{fd["name"]}": {_example_ints[int_idx % len(_example_ints)]}')
            int_idx += 1
        else:
            parts.append(f'"{fd["name"]}": "brief text here"')
    return "{" + ", ".join(parts) + "}"


def _clamp(val: Any, default: int = 5) -> int:
    try:
        return max(1, min(10, int(val)))
    except (TypeError, ValueError):
        return default

