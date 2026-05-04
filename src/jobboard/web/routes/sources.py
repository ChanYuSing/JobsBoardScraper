"""Sources routes."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..deps import CONFIG_PATH, templates
from ..services.sources import SOURCE_FIELDS, get_sources, save_source

router = APIRouter()


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
    """Queue fetch then enrich for one source."""
    from ...scheduler import enqueue_run
    from ..deps import CONFIG_PATH, get_config
    cfg = get_config()
    enqueue_run([name], CONFIG_PATH, cfg.storage.sqlite_path, phases=["fetch", "enrich"])
    return RedirectResponse(f"/sources?flash=Fetch+%2B+enrich+queued+for+{name}.", status_code=303)


@router.post("/sources/{name}/fetch")
def fetch_now(name: str):
    """Queue fetch-only for one source."""
    from ...scheduler import enqueue_run
    from ..deps import CONFIG_PATH, get_config
    cfg = get_config()
    enqueue_run([name], CONFIG_PATH, cfg.storage.sqlite_path, phases=["fetch"])
    return RedirectResponse(f"/sources?flash=Fetch+queued+for+{name}.", status_code=303)


@router.post("/sources/{name}/enrich")
def enrich_now(name: str):
    """Queue enrich-only for one source."""
    from ...scheduler import enqueue_run
    from ..deps import CONFIG_PATH, get_config
    cfg = get_config()
    enqueue_run([name], CONFIG_PATH, cfg.storage.sqlite_path, phases=["enrich"])
    return RedirectResponse(f"/sources?flash=Enrich+queued+for+{name}.", status_code=303)
