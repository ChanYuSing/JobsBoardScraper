"""Analyse routes."""
from __future__ import annotations

import sqlite3
import threading
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..deps import CONFIG_PATH, get_db, templates
from ...config import load_config
from ...scheduler import enqueue_score
from ..services.analyse import (
    ALL_PROMPT_FIELDS,
    build_preview_prompts,
    get_ai_config,
    get_field_defs,
    get_score_job_counts,
    save_ai_config,
    save_field_defs,
    score_all_jobs,
)
from ..services.ai_client import score_job
from ..services.filter_presets import get_preset, list_presets

router = APIRouter()

# ---------------------------------------------------------------------------
# Score-all task state (one task at a time)
# ---------------------------------------------------------------------------

_score_lock = threading.Lock()
_score_state: dict[str, Any] = {
    "running": False,
    "done":    0,
    "total":   0,
    "errors":  0,
    "message": "",   # last error message if any
}


def _form_ai(form) -> dict:
    """Extract all AI settings from a form submission."""
    return {
        "provider":         str(form.get("provider",         "")),
        "model":            str(form.get("model",            "")),
        "base_url":         str(form.get("base_url",         "")),
        "api_key":          str(form.get("api_key",          "")),
        "temperature":      form.get("temperature",         ""),
        "max_tokens":       form.get("max_tokens",          ""),
        "top_p":            form.get("top_p",               ""),
        "frequency_penalty": form.get("frequency_penalty",  ""),
        "presence_penalty":  form.get("presence_penalty",   ""),
        "seed":              form.get("seed",               ""),
        "reasoning_effort": str(form.get("reasoning_effort", "")),
        "thinking_enabled": form.get("thinking_enabled",    ""),
        "system_prompt":    str(form.get("system_prompt",   "")),
        "cv":               str(form.get("cv",              "")),
        "prompt_fields":    list(form.getlist("prompt_fields[]")),
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


def _num(val: str, cast):
    """Parse a form string to a number, returning None when blank or invalid."""
    try:
        return cast(val) if val else None
    except (ValueError, TypeError):
        return None


@router.get("/analyse", response_class=HTMLResponse)
def analyse_page(
    request: Request,
    flash: str = "",
    flash_type: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    ai = get_ai_config(CONFIG_PATH)
    field_defs = get_field_defs(conn)
    job_counts = get_score_job_counts(conn)
    presets = list_presets(conn)
    return templates.TemplateResponse(
        request,
        "analyse.html",
        {
            "active": "analyse",
            "ai": ai,
            "field_defs": field_defs,
            "flash": flash,
            "flash_type": flash_type,
            "all_prompt_fields": ALL_PROMPT_FIELDS,
            "job_counts": job_counts,
            "providers": _PROVIDERS,
            "presets": presets,
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
        thinking_raw = form_ai["thinking_enabled"]
        provider_key = form_ai["provider"] or None
        provider_params = {provider_key: {
            "temperature":       _num(form_ai["temperature"], float),
            "max_tokens":        _num(form_ai["max_tokens"], int),
            "top_p":             _num(form_ai["top_p"], float),
            "frequency_penalty": _num(form_ai["frequency_penalty"], float),
            "presence_penalty":  _num(form_ai["presence_penalty"], float),
            "seed":              _num(form_ai["seed"], int),
            "reasoning_effort":  form_ai["reasoning_effort"] or None,
            "thinking_enabled":  True if thinking_raw == "true" else (False if thinking_raw == "false" else None),
        }} if provider_key else None
        save_ai_config(
            CONFIG_PATH,
            system_prompt=form_ai["system_prompt"],
            cv=form_ai["cv"],
            fields=fields or None,
            provider=form_ai["provider"] or None,
            model=form_ai["model"] or None,
            base_url=form_ai["base_url"],
            api_key=form_ai["api_key"] if form_ai["api_key"] != "" else None,
            provider_params=provider_params,
            prompt_fields=form_ai["prompt_fields"] or None,
        )
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
        result = build_preview_prompts(
            conn, form_ai["system_prompt"], form_ai["cv"], field_defs,
            prompt_fields=form_ai["prompt_fields"] or None,
        )
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/analyse/score")
async def analyse_score(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Enqueue a score-all task on the shared run queue.  Returns immediately."""
    # Read form data BEFORE acquiring the lock so the async yield happens
    # outside the critical section, eliminating the TOCTOU race.
    form = await request.form()
    rescore = form.get("rescore") == "1"

    # Resolve optional preset scope (multi-select)
    preset_name: str | None = None
    preset_params: list[dict] | None = None
    preset_id_list = [v for v in form.getlist("preset_id") if v]
    if preset_id_list:
        selected_presets = []
        for pid_raw in preset_id_list:
            try:
                preset = get_preset(conn, int(pid_raw))
                if preset:
                    selected_presets.append(preset)
            except (ValueError, Exception):
                pass
        if selected_presets:
            preset_name   = ", ".join(p["name"] for p in selected_presets)
            preset_params = [p["params"] for p in selected_presets]

    field_defs = get_field_defs(conn)
    if not field_defs:
        return JSONResponse({"error": "No scoring fields defined."}, status_code=400)

    with _score_lock:
        if _score_state["running"]:
            return JSONResponse({"error": "Scoring already in progress."}, status_code=409)
        _score_state.update(running=True, done=0, total=0, errors=0, message="")

    cfg = load_config(CONFIG_PATH)
    scored_ref = [0]
    errors_ref = [0]

    def _progress(done: int, total: int, errors: int) -> None:
        scored_ref[0] = done - errors
        errors_ref[0] = errors
        with _score_lock:
            _score_state["done"]   = done
            _score_state["total"]  = total
            _score_state["errors"] = errors

    task = enqueue_score(
        CONFIG_PATH, cfg.storage.sqlite_path,
        rescore=rescore,
        progress_cb=_progress,
        preset_name=preset_name,
        preset_params=preset_params,
    )

    def _finish_watcher() -> None:
        task.done_event.wait()
        with _score_lock:
            _score_state["message"] = f"Done — {scored_ref[0]} scored, {errors_ref[0]} errors."
            _score_state["running"] = False

    threading.Thread(target=_finish_watcher, daemon=True).start()

    with _score_lock:
        return JSONResponse({"ok": True, "state": dict(_score_state)})


@router.post("/analyse/test")
async def analyse_test(request: Request):
    """Send a single prompt pair to the AI and return the parsed result."""
    try:
        body = await request.json()
        system = body.get("system", "")
        user   = body.get("user", "")
        cfg    = load_config(CONFIG_PATH).ai
        result = score_job(cfg, system, user)
        return JSONResponse({"ok": True, "result": result})
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:500]}, status_code=400)


@router.get("/analyse/score/status")
def analyse_score_status():
    """Poll endpoint — returns current scoring task state."""
    with _score_lock:
        return JSONResponse(dict(_score_state))


# ---------------------------------------------------------------------------
# AI provider settings page
# ---------------------------------------------------------------------------

_PROVIDERS = [
    {"value": "ollama",        "label": "Ollama (local)"},
    {"value": "lmstudio",      "label": "LM Studio (local)"},
    {"value": "openai",        "label": "OpenAI"},
    {"value": "grok",          "label": "Grok (xAI)"},
    {"value": "gemini",        "label": "Gemini (Google)"},
    {"value": "deepseek",      "label": "DeepSeek"},
    {"value": "anthropic",     "label": "Anthropic (Claude)"},
    {"value": "openai_compat", "label": "OpenAI-compatible (custom)"},
]


@router.get("/analyse/settings")
def ai_settings_page():
    """Redirects to the unified Analyse page."""
    return RedirectResponse("/analyse", status_code=301)


@router.post("/analyse/settings/save")
async def ai_settings_save():
    """Redirects to the unified Analyse page."""
    return RedirectResponse("/analyse", status_code=303)
