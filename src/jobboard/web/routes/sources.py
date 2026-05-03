"""Sources routes."""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..deps import CONFIG_PATH
from ..services.sources import SOURCE_FIELDS, get_sources, save_source

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parents[1] / "templates"))


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, flash: str = "", flash_type: str = ""):
    data = get_sources(CONFIG_PATH)
    return templates.TemplateResponse(
        request,
        "sources.html",
        {
            "active": "sources",
            "sources": data,
            "source_fields": SOURCE_FIELDS,
            "flash": flash,
            "flash_type": flash_type,
        },
    )


@router.post("/sources/{name}/save")
async def save(name: str, request: Request):
    form = await request.form()
    # collect multi-value fields (e.g. multiselect chips) as comma-joined strings
    form_dict: dict[str, str] = {}
    for key in set(form.keys()):
        vals = form.getlist(key)
        form_dict[key] = ",".join(str(v) for v in vals) if len(vals) > 1 else (vals[0] if vals else "")
    # toggles that are unchecked send nothing; treat absence as false
    for f in SOURCE_FIELDS.get(name, []):
        if f["type"] == "toggle" and f["key"] not in form_dict:
            form_dict[f["key"]] = "false"
    try:
        save_source(CONFIG_PATH, name, form_dict)
        return RedirectResponse(f"/sources?flash=Saved+{name}+settings", status_code=303)
    except Exception as exc:
        return RedirectResponse(
            f"/sources?flash={str(exc)[:120]}&flash_type=error", status_code=303
        )


@router.post("/sources/{name}/run")
def run_now(name: str):
    """Trigger fetch+enrich for one source in a background thread."""
    from ...scheduler import _run_all
    from ..deps import CONFIG_PATH, get_config

    cfg = get_config()
    db_path = cfg.storage.sqlite_path

    def _go():
        try:
            _run_all([name], CONFIG_PATH, db_path)
        except Exception:
            pass  # errors already logged inside _run_all

    threading.Thread(target=_go, daemon=True, name=f"run_now_{name}").start()
    return RedirectResponse(
        f"/sources?flash=Started+run+for+{name}.+Check+Runs+page+for+status.",
        status_code=303,
    )
