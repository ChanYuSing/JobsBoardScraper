"""Analyse routes."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..deps import CONFIG_PATH, get_db
from ..services.analyse import (
    build_preview_prompts,
    get_ai_config,
    get_analysis_results,
    get_field_defs,
    save_ai_config,
    save_field_defs,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parents[1] / "templates"))


def _form_ai(form) -> dict:
    """Extract prompt-building fields from a form submission."""
    return {
        "system_prompt": str(form.get("system_prompt", "")),
        "cv": str(form.get("cv", "")),
    }


def _form_field_defs(form) -> list[dict]:
    """Parse field definition arrays from a form submission."""
    names = form.getlist("field_name[]")
    types = form.getlist("field_type[]")
    descs = form.getlist("field_desc[]")
    return [
        {"name": n.strip(), "type": t, "description": d.strip()}
        for n, t, d in zip(names, types, descs)
        if n.strip()
    ]


@router.get("/analyse", response_class=HTMLResponse)
def analyse_page(
    request: Request,
    flash: str = "",
    flash_type: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    ai = get_ai_config(CONFIG_PATH)
    field_defs = get_field_defs(conn)
    stored_results = get_analysis_results(conn, field_defs)
    return templates.TemplateResponse(
        request,
        "analyse.html",
        {
            "active": "analyse",
            "ai": ai,
            "field_defs": field_defs,
            "flash": flash,
            "flash_type": flash_type,
            "stored_results": stored_results,
        },
    )


@router.post("/analyse/save")
async def analyse_save(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    form = await request.form()
    form_ai = _form_ai(form)
    confirmed = form.get("confirmed") == "1"
    try:
        fields = _form_field_defs(form)
        if fields:
            # Detect which field names are being removed
            existing_names = {r[0] for r in conn.execute("SELECT name FROM field_def").fetchall()}
            incoming_names = {f["name"] for f in fields}
            removed = existing_names - incoming_names
            if removed and not confirmed:
                # Check if any removed field has scored data
                scored = [
                    name for name in removed
                    if conn.execute(
                        "SELECT 1 FROM job_analysis ja "
                        "JOIN field_def fd ON fd.id = ja.field_id "
                        "WHERE fd.name = ? LIMIT 1", (name,)
                    ).fetchone()
                ]
                if scored:
                    return JSONResponse({
                        "confirm_required": True,
                        "fields": scored,
                    })
            save_field_defs(conn, fields)
        save_ai_config(CONFIG_PATH, system_prompt=form_ai["system_prompt"], cv=form_ai["cv"], fields=fields or None)
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:200]}, status_code=400)


@router.post("/analyse/preview")
async def analyse_preview(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        form = await request.form()
        form_ai = _form_ai(form)
        field_defs = _form_field_defs(form)
        if not field_defs:
            field_defs = get_field_defs(conn)
        result = build_preview_prompts(conn, form_ai["system_prompt"], form_ai["cv"], field_defs)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/analyse/score")
async def analyse_score(request: Request):
    return JSONResponse({"error": "AI scoring is not yet configured."}, status_code=501)
