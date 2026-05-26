"""Jobs routes."""
from __future__ import annotations

import json
import sqlite3
from datetime import date as _date, datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ..deps import get_db, templates
from ..services.analyse import get_field_defs
from ..services.filter_presets import extract_params, list_presets
from ..services.jobs import (
    ALL_DISPLAY_COLS, DEFAULT_COLS, SORTABLE, get_filter_options, get_job,
    get_job_scores, list_jobs, mark_job,
)


def _days_ago(date_str: str | None) -> str | None:
    if not date_str:
        return None
    try:
        d = _date.fromisoformat(date_str[:10])
        delta = (_date.today() - d).days
        if delta == 0:
            return "today"
        if delta == 1:
            return "1 day ago"
        return f"{delta} days ago"
    except Exception:
        return None


_SOURCE_TABLE: dict[str, str] = {
    "jobsdb":         "job_jobsdb",
    "linkedin_guest": "job_linkedin",
}
_IMPORT_META = frozenset({"triage_status", "source"})


def _import_job(
    conn: sqlite3.Connection,
    job: dict,
    table_cols: dict[str, set[str]],
) -> str:
    """Insert one job from an import payload.

    Returns 'inserted', 'status_only', or 'skipped'.
    Existing jobs (matched by source + job_id) are never overwritten.
    """
    source  = job.get("source")
    job_id  = job.get("job_id")
    triage  = job.get("triage_status", "new")
    table   = _SOURCE_TABLE.get(source)
    allowed = table_cols.get(table, set())
    if not table or not job_id or not allowed:
        return "skipped"
    row = {k: v for k, v in job.items() if k not in _IMPORT_META and k in allowed}
    if not row or "job_id" not in row:
        return "skipped"
    cols  = ", ".join(f'"{c}"' for c in row)
    marks = ", ".join("?" * len(row))
    conn.execute(
        f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({marks})",
        list(row.values()),
    )
    was_inserted = conn.execute("SELECT changes()").fetchone()[0] > 0
    status_added = False
    if triage in ("saved", "dismissed"):
        conn.execute(
            "INSERT OR IGNORE INTO job_status (source, job_id, status, marked_at)"
            " VALUES (?, ?, ?, datetime('now'))",
            (source, job_id, triage),
        )
        status_added = conn.execute("SELECT changes()").fetchone()[0] > 0
    if was_inserted:
        return "inserted"
    if status_added:
        return "status_only"
    return "skipped"


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    keyword: str = "",
    source: str = "",
    date_from: str = "",
    date_to: str = "",
    work_type: list[str] = Query(default=[]),
    company: str = "",
    location_kw: str = "",
    classification: str = "",
    subclassification_kw: str = "",
    first_seen_from: str = "",
    first_seen_to: str = "",
    triage: str = "",
    sort_by: str = "",
    sort_dir: str = "desc",
    cols: str = "",
    col: list[str] = Query(default=[]),
    page: int = 1,
    page_size: int = 15,
    run_id: int = 0,
    conn: sqlite3.Connection = Depends(get_db),
):
    page_size = max(1, min(page_size, 1000))
    field_defs = get_field_defs(conn)
    score_display_cols = [(f["label"], f["name"]) for f in field_defs]
    score_int_keys = {f["name"] for f in field_defs if f["type"] == "int"}
    score_str_keys = {f["name"] for f in field_defs if f["type"] == "str"}
    extended_display_cols = ALL_DISPLAY_COLS + score_display_cols
    active_cols = col if col else (cols.split(",") if cols else DEFAULT_COLS)
    cols_param = ",".join(active_cols)
    _col_label = {key: label for label, key in extended_display_cols}
    ordered_cols = [(_col_label[k], k) for k in active_cols if k in _col_label]
    _active_keys = set(active_cols)
    picker_cols = ordered_cols + [(lbl, k) for lbl, k in extended_display_cols if k not in _active_keys]

    # Parse dynamic score-min filters from query string
    score_filters: dict[str, int] = {}
    for _name in score_int_keys:
        _raw = request.query_params.get(f"score_min_{_name}", "")
        if _raw:
            try:
                score_filters[_name] = int(_raw)
            except ValueError:
                pass

    jobs, total = list_jobs(
        conn,
        keyword=keyword, source=source,
        date_from=date_from, date_to=date_to,
        work_type=work_type,
        company=company, location_kw=location_kw,
        classification=classification,
        subclassification_kw=subclassification_kw,
        first_seen_from=first_seen_from,
        first_seen_to=first_seen_to,
        triage=triage,
        sort_by=sort_by, sort_dir=sort_dir,
        page=page, page_size=page_size,
        score_field_names=score_int_keys,
        score_filters=score_filters or None,
        run_id=run_id,
    )
    get_job_scores(conn, jobs, field_defs)
    total_pages = max(1, (total + page_size - 1) // page_size)
    filter_options = get_filter_options(conn)

    # Presets: load all and detect which (if any) matches current filters
    presets = list_presets(conn)
    current_params = extract_params(request.query_params, score_int_keys)
    active_preset_id: int | None = None
    active_preset_name: str | None = None
    for _p in presets:
        if _p["params"] == current_params:
            active_preset_id   = _p["id"]
            active_preset_name = _p["name"]
            break

    # build base filter params dict (without page) for pagination links
    scalar_fp = dict(
        keyword=keyword, source=source, date_from=date_from, date_to=date_to,
        company=company,
        location_kw=location_kw, classification=classification,
        subclassification_kw=subclassification_kw,
        first_seen_from=first_seen_from,
        first_seen_to=first_seen_to,
        triage=triage,
        sort_by=sort_by, sort_dir=sort_dir,
        cols=cols_param, page_size=str(page_size) if page_size != 15 else "",
        run_id=str(run_id) if run_id else "",
    )
    active_filter_count = (
        sum(1 for k, v in scalar_fp.items() if v and k not in (
            "cols", "triage", "sort_by", "sort_dir", "page_size",
            "date_from", "date_to", "first_seen_from", "first_seen_to",
        ))
        + (1 if (date_from or date_to) else 0)
        + (1 if (first_seen_from or first_seen_to) else 0)
        + (1 if work_type else 0)
        + len(score_filters)
    )
    qs_parts: dict[str, object] = {k: v for k, v in scalar_fp.items() if v}
    if work_type:
        qs_parts["work_type"] = work_type
    for _name, _val in score_filters.items():
        qs_parts[f"score_min_{_name}"] = _val
    page_qs = urlencode(qs_parts, doseq=True)

    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "active": "jobs",
            "jobs": jobs,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "page_size": page_size,
            "extended_display_cols": extended_display_cols,
            "active_cols": active_cols,
            "ordered_cols": ordered_cols,
            "picker_cols": picker_cols,
            "cols_param": cols_param,
            "filter_options": filter_options,
            "active_filter_count": active_filter_count,
            "score_int_keys": score_int_keys,
            "score_str_keys": score_str_keys,
            "score_filters": score_filters,
            # individual filter values
            "f_keyword": keyword,
            "f_source": source,
            "f_date_from": date_from,
            "f_date_to": date_to,
            "f_work_type": work_type,        # list[str]
            "f_company": company,
            "f_location_kw": location_kw,
            "f_classification": classification,
            "f_subclassification_kw": subclassification_kw,
            "f_first_seen_from": first_seen_from,
            "f_first_seen_to": first_seen_to,
            "f_triage": triage,
            "f_sort_by": sort_by,
            "f_sort_dir": sort_dir,
            "run_id": run_id,
            "sortable_cols": SORTABLE | score_int_keys,
            # pagination base query string
            "page_qs": page_qs,
            # presets
            "presets": presets,
            "active_preset_id": active_preset_id,
            "active_preset_name": active_preset_name,
            "current_filter_params": current_params,
        },
    )


@router.get("/jobs/{source}/{job_id}/panel")
def job_panel(
    request: Request,
    source: str,
    job_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    job = get_job(conn, source, job_id)
    if job is None:
        return HTMLResponse("Not found", status_code=404)
    field_defs = get_field_defs(conn)
    get_job_scores(conn, [job], field_defs)
    return templates.TemplateResponse(
        request,
        "job_panel.html",
        {"job": job, "posted_ago": _days_ago(job.get("date_posted")), "field_defs": field_defs},
    )


@router.post("/jobs/{source}/{job_id}/mark-ajax")
async def mark_ajax(
    source: str,
    job_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    body = await request.json()
    status = body.get("status", "new")
    try:
        mark_job(conn, source, job_id, status)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"status": status})


@router.get("/jobs/export.json")
def jobs_export(
    request: Request,
    keyword: str = "",
    source: str = "",
    date_from: str = "",
    date_to: str = "",
    work_type: list[str] = Query(default=[]),
    company: str = "",
    location_kw: str = "",
    classification: str = "",
    subclassification_kw: str = "",
    first_seen_from: str = "",
    first_seen_to: str = "",
    triage: str = "",
    sort_by: str = "",
    sort_dir: str = "desc",
    run_id: int = 0,
    conn: sqlite3.Connection = Depends(get_db),
):
    # Mirror score_min_* filters from the query string (same as jobs_page)
    score_filters: dict[str, int] = {}
    for key, val in request.query_params.multi_items():
        if key.startswith("score_min_"):
            try:
                score_filters[key[len("score_min_"):]] = int(val)
            except ValueError:
                pass
    jobs, _ = list_jobs(
        conn,
        keyword=keyword, source=source,
        date_from=date_from, date_to=date_to,
        work_type=work_type,
        company=company, location_kw=location_kw,
        classification=classification,
        subclassification_kw=subclassification_kw,
        first_seen_from=first_seen_from,
        first_seen_to=first_seen_to,
        triage=triage,
        sort_by=sort_by, sort_dir=sort_dir,
        page=1, page_size=50_000,
        score_field_names=set(),
        score_filters=score_filters or None,
        run_id=run_id,
    )
    _SKIP = {"description_html"}
    clean = [{k: v for k, v in job.items() if k not in _SKIP} for job in jobs]
    payload = json.dumps(
        {
            "version": 1,
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total": len(clean),
            "jobs": clean,
        },
        default=str,
        ensure_ascii=False,
    ).encode("utf-8")
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="jobs-export.json"'},
    )


@router.post("/jobs/import")
async def jobs_import(
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    raw = await file.read()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "Invalid file — expected a JSON export."}, status_code=400)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return JSONResponse(
            {"error": "Unrecognised format — missing 'jobs' list."},
            status_code=400,
        )
    table_cols: dict[str, set[str]] = {
        tbl: {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
        for tbl in _SOURCE_TABLE.values()
    }
    inserted = skipped = status_only = errors = 0
    for i, job in enumerate(data["jobs"]):
        try:
            result = _import_job(conn, job, table_cols)
            if result == "inserted":
                inserted += 1
            elif result == "status_only":
                status_only += 1
            else:
                skipped += 1
        except Exception:
            errors += 1
        if i % 500 == 499:
            conn.commit()
    conn.commit()
    parts = [f"{inserted} new job{'s' if inserted != 1 else ''} imported"]
    if skipped:
        parts.append(f"{skipped} already existed")
    if status_only:
        parts.append(f"{status_only} status update{'s' if status_only != 1 else ''}")
    if errors:
        parts.append(f"{errors} error{'s' if errors != 1 else ''}")
    return JSONResponse({
        "ok": True,
        "inserted": inserted,
        "skipped": skipped,
        "status_only": status_only,
        "errors": errors,
        "message": " · ".join(parts),
    })
