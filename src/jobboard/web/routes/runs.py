"""Runs routes."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import sqlite3
from ..deps import get_db
from ..services.runs import distinct_sources, list_runs

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parents[1] / "templates"))


@router.get("/runs", response_class=HTMLResponse)
def runs_page(
    request: Request,
    source: str = "",
    status: str = "",
    date_from: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    runs = list_runs(conn, source=source, status=status, date_from=date_from)
    sources = distinct_sources(conn)
    return templates.TemplateResponse(
        request,
        "runs.html",
        {
            "active": "runs",
            "runs": runs,
            "sources": sources,
            "filter_source": source,
            "filter_status": status,
            "filter_date_from": date_from,
        },
    )
