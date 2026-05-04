"""Jobs routes."""
from __future__ import annotations

import sqlite3
from datetime import date as _date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..deps import get_db, templates
from ..services.jobs import (
    ALL_DISPLAY_COLS, DEFAULT_COLS, SORTABLE, get_filter_options, get_job, list_jobs, mark_job,
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
    arrangement: str = "",
    company: str = "",
    location_kw: str = "",
    classification: str = "",
    triage: str = "",
    sort_by: str = "",
    sort_dir: str = "desc",
    cols: str = "",
    col: list[str] = Query(default=[]),
    page: int = 1,
    page_size: int = 15,
    conn: sqlite3.Connection = Depends(get_db),
):
    page_size = max(1, min(page_size, 1000))
    active_cols = col if col else (cols.split(",") if cols else DEFAULT_COLS)
    cols_param = ",".join(active_cols)
    _col_label = {key: label for label, key in ALL_DISPLAY_COLS}
    ordered_cols = [(_col_label[k], k) for k in active_cols if k in _col_label]
    _active_keys = set(active_cols)
    picker_cols = ordered_cols + [(lbl, k) for lbl, k in ALL_DISPLAY_COLS if k not in _active_keys]

    jobs, total = list_jobs(
        conn,
        keyword=keyword, source=source,
        date_from=date_from, date_to=date_to,
        work_type=work_type, arrangement=arrangement,
        company=company, location_kw=location_kw,
        classification=classification, triage=triage,
        sort_by=sort_by, sort_dir=sort_dir,
        page=page, page_size=page_size,
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    filter_options = get_filter_options(conn)

    # build base filter params dict (without page) for pagination links
    scalar_fp = dict(
        keyword=keyword, source=source, date_from=date_from, date_to=date_to,
        arrangement=arrangement, company=company,
        location_kw=location_kw, classification=classification, triage=triage,
        sort_by=sort_by, sort_dir=sort_dir,
        cols=cols_param, page_size=str(page_size) if page_size != 15 else "",
    )
    active_filter_count = (
        sum(1 for k, v in scalar_fp.items() if v and k not in ("cols", "triage", "sort_by", "sort_dir", "page_size"))
        + (1 if work_type else 0)
    )
    qs_parts: dict[str, object] = {k: v for k, v in scalar_fp.items() if v}
    if work_type:
        qs_parts["work_type"] = work_type
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
            "all_cols": ALL_DISPLAY_COLS,
            "active_cols": active_cols,
            "ordered_cols": ordered_cols,
            "picker_cols": picker_cols,
            "cols_param": cols_param,
            "filter_options": filter_options,
            "active_filter_count": active_filter_count,
            # individual filter values
            "f_keyword": keyword,
            "f_source": source,
            "f_date_from": date_from,
            "f_date_to": date_to,
            "f_work_type": work_type,        # list[str]
            "f_arrangement": arrangement,
            "f_company": company,
            "f_location_kw": location_kw,
            "f_classification": classification,
            "f_triage": triage,
            "f_sort_by": sort_by,
            "f_sort_dir": sort_dir,
            "sortable_cols": SORTABLE,
            # pagination base query string
            "page_qs": page_qs,
        },
    )


@router.get("/jobs/{source}/{job_id}", response_class=HTMLResponse)
def job_detail(
    request: Request,
    source: str,
    job_id: str,
    conn: sqlite3.Connection = Depends(get_db),
):
    job = get_job(conn, source, job_id)
    if job is None:
        return HTMLResponse("Not found", status_code=404)
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {"active": "jobs", "job": job},
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
    return templates.TemplateResponse(
        request,
        "job_panel.html",
        {"job": job, "posted_ago": _days_ago(job.get("date_posted"))},
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
    mark_job(conn, source, job_id, status)
    return JSONResponse({"status": status})


@router.post("/jobs/{source}/{job_id}/mark")
def mark(
    source: str,
    job_id: str,
    status: str = Form(...),
    keyword: str = Form(""),
    filter_source: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
    work_type: list[str] = Form(default=[]),
    arrangement: str = Form(""),
    company: str = Form(""),
    location_kw: str = Form(""),
    classification: str = Form(""),
    triage: str = Form(""),
    sort_by: str = Form(""),
    sort_dir: str = Form("desc"),
    cols: str = Form(""),
    page: int = Form(1),
    page_size: int = Form(15),
    conn: sqlite3.Connection = Depends(get_db),
):
    mark_job(conn, source, job_id, status)
    scalar = {k: v for k, v in dict(
        keyword=keyword, source=filter_source,
        date_from=date_from, date_to=date_to,
        arrangement=arrangement, company=company,
        location_kw=location_kw, classification=classification,
        triage=triage, sort_by=sort_by, sort_dir=sort_dir,
        cols=cols, page=page,
        page_size=str(page_size) if page_size != 15 else "",
    ).items() if v}
    if work_type:
        scalar["work_type"] = work_type
    params = urlencode(scalar, doseq=True)
    return RedirectResponse(f"/jobs?{params}", status_code=303)
