"""Jobs routes."""
from __future__ import annotations

from datetime import date as _date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..deps import get_db, templates
from ..services.analyse import get_field_defs
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

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    keyword: list[str] = Query(default=[]),
    keyword_op: str = "or",
    source: str = "",
    date_from: str = "",
    date_to: str = "",
    work_type: list[str] = Query(default=[]),
    work_type_op: str = "or",
    company: list[str] = Query(default=[]),
    company_op: str = "or",
    location_kw: list[str] = Query(default=[]),
    location_kw_op: str = "or",
    classification: list[str] = Query(default=[]),
    classification_op: str = "or",
    subclassification_kw: list[str] = Query(default=[]),
    subclassification_kw_op: str = "or",
    description_exclude: list[str] = Query(default=[]),
    description_exclude_op: str = "or",
    first_seen_from: str = "",
    first_seen_to: str = "",
    triage: str = "",
    sort_by: str = "",
    sort_dir: str = "desc",
    cols: str = "",
    col: list[str] = Query(default=[]),
    page: int = 1,
    page_size: int = 15,
    conn: object = Depends(get_db),
):
    page_size = max(1, min(page_size, 1000))
    # Strip blank entries that arise from empty <input>s in the form post.
    def _clean(xs: list[str]) -> list[str]:
        return [x for x in (s.strip() for s in xs) if x]
    keyword              = _clean(keyword)
    company              = _clean(company)
    location_kw          = _clean(location_kw)
    classification       = _clean(classification)
    subclassification_kw = _clean(subclassification_kw)
    description_exclude  = _clean(description_exclude)
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
        keyword=keyword, keyword_op=keyword_op,
        source=source,
        date_from=date_from, date_to=date_to,
        work_type=work_type, work_type_op=work_type_op,
        company=company, company_op=company_op,
        location_kw=location_kw, location_kw_op=location_kw_op,
        classification=classification, classification_op=classification_op,
        subclassification_kw=subclassification_kw, subclassification_kw_op=subclassification_kw_op,
        description_exclude=description_exclude, description_exclude_op=description_exclude_op,
        first_seen_from=first_seen_from,
        first_seen_to=first_seen_to,
        triage=triage,
        sort_by=sort_by, sort_dir=sort_dir,
        page=page, page_size=page_size,
        score_field_names=score_int_keys,
        score_filters=score_filters or None,
    )
    get_job_scores(conn, jobs, field_defs)
    total_pages = max(1, (total + page_size - 1) // page_size)
    filter_options = get_filter_options(conn)

    # build base filter params dict (without page) for pagination links
    scalar_fp = dict(
        source=source, date_from=date_from, date_to=date_to,
        first_seen_from=first_seen_from,
        first_seen_to=first_seen_to,
        triage=triage,
        sort_by=sort_by, sort_dir=sort_dir,
        cols=cols_param, page_size=str(page_size) if page_size != 15 else "",
    )
    list_fp: dict[str, list[str]] = {
        "keyword": keyword, "company": company, "location_kw": location_kw,
        "classification": classification, "subclassification_kw": subclassification_kw,
        "description_exclude": description_exclude, "work_type": work_type,
    }
    op_fp = {
        "keyword_op": keyword_op if keyword else "",
        "company_op": company_op if company else "",
        "location_kw_op": location_kw_op if location_kw else "",
        "classification_op": classification_op if classification else "",
        "subclassification_kw_op": subclassification_kw_op if subclassification_kw else "",
        "description_exclude_op": description_exclude_op if description_exclude else "",
        "work_type_op": work_type_op if work_type else "",
    }
    active_filter_count = (
        (1 if source else 0)
        + (1 if (date_from or date_to) else 0)
        + (1 if (first_seen_from or first_seen_to) else 0)
        + (1 if triage else 0)
        + sum(1 for v in list_fp.values() if v)
        + len(score_filters)
    )
    qs_parts: dict[str, object] = {k: v for k, v in scalar_fp.items() if v}
    for k, vs in list_fp.items():
        if vs:
            qs_parts[k] = vs
    for k, v in op_fp.items():
        if v:
            qs_parts[k] = v
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
            "f_keyword_op": keyword_op,
            "f_source": source,
            "f_date_from": date_from,
            "f_date_to": date_to,
            "f_work_type": work_type,        # list[str]
            "f_work_type_op": work_type_op,
            "f_company": company,
            "f_company_op": company_op,
            "f_location_kw": location_kw,
            "f_location_kw_op": location_kw_op,
            "f_classification": classification,
            "f_classification_op": classification_op,
            "f_subclassification_kw": subclassification_kw,
            "f_subclassification_kw_op": subclassification_kw_op,
            "f_description_exclude": description_exclude,
            "f_description_exclude_op": description_exclude_op,
            "f_first_seen_from": first_seen_from,
            "f_first_seen_to": first_seen_to,
            "f_triage": triage,
            "f_sort_by": sort_by,
            "f_sort_dir": sort_dir,
            "sortable_cols": SORTABLE | score_int_keys,
            # pagination base query string
            "page_qs": page_qs,
        },
    )


@router.get("/jobs/{source}/{job_id}", response_class=HTMLResponse)
def job_detail(
    request: Request,
    source: str,
    job_id: str,
    conn: object = Depends(get_db),
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
    conn: object = Depends(get_db),
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
    conn: object = Depends(get_db),
):
    body = await request.json()
    status = body.get("status", "new")
    try:
        mark_job(conn, source, job_id, status)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"status": status})


@router.post("/jobs/{source}/{job_id}/mark")
async def mark(
    source: str,
    job_id: str,
    request: Request,
    conn: object = Depends(get_db),
):
    form_data = await request.form()
    status               = str(form_data.get("status", "new"))
    keyword              = str(form_data.get("keyword", ""))
    filter_source        = str(form_data.get("filter_source", ""))
    date_from            = str(form_data.get("date_from", ""))
    date_to              = str(form_data.get("date_to", ""))
    work_type            = list(form_data.getlist("work_type"))
    company              = str(form_data.get("company", ""))
    location_kw          = str(form_data.get("location_kw", ""))
    classification       = str(form_data.get("classification", ""))
    subclassification_kw = str(form_data.get("subclassification_kw", ""))
    first_seen_from      = str(form_data.get("first_seen_from", ""))
    first_seen_to        = str(form_data.get("first_seen_to", ""))
    triage               = str(form_data.get("triage", ""))
    sort_by              = str(form_data.get("sort_by", ""))
    sort_dir             = str(form_data.get("sort_dir", "desc"))
    cols                 = str(form_data.get("cols", ""))
    page_s               = str(form_data.get("page", "1"))
    page_size_s          = str(form_data.get("page_size", "15"))
    try:
        mark_job(conn, source, job_id, status)
    except ValueError as exc:
        return RedirectResponse(
            f"/jobs?flash={str(exc)[:120]}&flash_type=error", status_code=303
        )
    scalar: dict[str, object] = {k: v for k, v in {
        "keyword": keyword, "source": filter_source,
        "date_from": date_from, "date_to": date_to,
        "company": company,
        "location_kw": location_kw, "classification": classification,
        "subclassification_kw": subclassification_kw,
        "first_seen_from": first_seen_from,
        "first_seen_to": first_seen_to,
        "triage": triage, "sort_by": sort_by, "sort_dir": sort_dir,
        "cols": cols, "page": page_s,
        "page_size": page_size_s if page_size_s != "15" else "",
    }.items() if v}
    if work_type:
        scalar["work_type"] = work_type
    for key, val in form_data.multi_items():
        if key.startswith("score_min_") and val:
            scalar[key] = val
    params = urlencode(scalar, doseq=True)
    return RedirectResponse(f"/jobs?{params}", status_code=303)
