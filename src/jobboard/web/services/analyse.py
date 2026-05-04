"""Analyse service — job fetching and prompt building for AI scoring."""
from __future__ import annotations

import json
import random
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

import yaml

from ...config import FieldCfg, config_write_lock, load_config
from ...db import sync_fields_full

_write_lock = config_write_lock

# ---------------------------------------------------------------------------
# Internal per-job prompt (not user-editable)
# ---------------------------------------------------------------------------

_SKIP_COLS = frozenset({
    # unparseable raw blobs only
    "raw_card_json", "raw_detail_json", "description_html",
    "detail_fetched_at", "detail_error",
})

JOB_PROMPT = (
    "Candidate CV:\n{cv}\n\n"
    "---\n\n"
    "Job listing:\n{job_info}\n\n"
    "---\n\n"
    "Score this job against the candidate's CV on each criterion below:\n{fields_spec}\n\n"
    "Respond with ONLY valid JSON — no markdown, no explanation, no extra text:\n{json_example}"
)


def _build_job_info(job: dict[str, Any]) -> str:
    """Format all meaningful job columns as 'key: value' lines."""
    skip = _SKIP_COLS | {"job_id"}
    lines = []
    for key, val in job.items():
        if key in skip or val is None or val == "":
            continue
        if key == "description_text":
            text = str(val)
            if len(text) > 3000:
                text = text[:3000] + "\u2026[truncated]"
            lines.append(f"{key}: {text}")
        elif key == "bullet_points":
            try:
                bullets = json.loads(val)
                if isinstance(bullets, list):
                    formatted = "\n".join(f"  - {b}" for b in bullets)
                    lines.append(f"bullet_points:\n{formatted}")
                    continue
            except Exception:
                pass
            lines.append(f"{key}: {val}")
        else:
            lines.append(f"{key}: {val}")
    return "\n".join(lines)


def build_preview_prompts(
    conn: sqlite3.Connection,
    system_prompt: str,
    cv: str,
    field_defs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pick a random non-dismissed job and return fully assembled system + user prompts."""
    jobs = get_jobs_for_analysis(conn, max_jobs=50)
    if not jobs:
        return {"error": "No jobs in database"}
    job = random.choice(jobs)
    user_msg = (
        JOB_PROMPT
        .replace("{cv}", cv or "[CV not provided]")
        .replace("{job_info}", _build_job_info(job))
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
        "system_prompt": cfg.ai.system_prompt,
        "cv": cfg.ai.cv,
        "fields": [{"name": f.name, "type": f.type, "description": f.description} for f in cfg.ai.fields],
    }


def save_ai_config(
    config_path: str,
    system_prompt: str,
    cv: str,
    fields: list[dict[str, Any]] | None = None,
) -> None:
    with _write_lock:
        with open(config_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        raw.setdefault("ai", {})
        raw["ai"]["system_prompt"] = system_prompt
        raw["ai"]["cv"] = cv
        if fields is not None:
            raw["ai"]["fields"] = [{"name": f["name"], "type": f["type"], "description": f["description"]} for f in fields]
        with open(config_path, "w", encoding="utf-8") as fh:
            yaml.dump(raw, fh, allow_unicode=True, sort_keys=False, default_flow_style=False)


# ---------------------------------------------------------------------------
# Job fetching
# ---------------------------------------------------------------------------

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
# Analysis results storage and retrieval (EAV)
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


def get_analysis_results(
    conn: sqlite3.Connection,
    field_defs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not field_defs:
        return []
    rows = conn.execute(
        """
        SELECT
            ja.source, ja.job_id, ja.analysed_at,
            fd.name, fd.type,
            ja.value_int, ja.value_str,
            COALESCE(jj.title, jl.title)     AS title,
            COALESCE(jj.company, jl.company) AS company
        FROM job_analysis ja
        JOIN field_def fd ON fd.id = ja.field_id
        LEFT JOIN job_jobsdb jj
            ON ja.source = 'jobsdb' AND ja.job_id = jj.job_id
        LEFT JOIN job_linkedin jl
            ON ja.source = 'linkedin_guest' AND ja.job_id = jl.job_id
        ORDER BY ja.source, ja.job_id, fd.sort_order
        """
    ).fetchall()

    jobs: dict[tuple, dict] = {}
    for r in rows:
        key = (r["source"], r["job_id"])
        if key not in jobs:
            jobs[key] = {
                "source": r["source"],
                "job_id": r["job_id"],
                "analysed_at": r["analysed_at"],
                "title": r["title"],
                "company": r["company"],
                "fields": {},
            }
        value = r["value_int"] if r["type"] == "int" else r["value_str"]
        jobs[key]["fields"][r["name"]] = value

    # Sort by last int field (typically "overall") desc
    int_fields = [f["name"] for f in field_defs if f["type"] == "int"]
    sort_key = int_fields[-1] if int_fields else None

    def _rank(j: dict) -> tuple:
        s = -(j["fields"].get(sort_key) or 0) if sort_key else 0
        return (s, j["analysed_at"])

    return sorted(jobs.values(), key=_rank)


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

