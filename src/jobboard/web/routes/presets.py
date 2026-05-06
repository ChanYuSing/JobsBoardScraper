"""Filter preset routes."""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..deps import get_db
from ..services.filter_presets import delete_preset, list_presets, save_preset

router = APIRouter(prefix="/presets")


@router.get("", response_class=JSONResponse)
def get_presets(conn: sqlite3.Connection = Depends(get_db)):
    return JSONResponse(list_presets(conn))


@router.post("")
async def create_preset(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return JSONResponse({"error": "Name is required."}, status_code=400)
    params_str = str(form.get("params", "{}"))
    try:
        params = json.loads(params_str)
    except Exception:
        return JSONResponse({"error": "Invalid params JSON."}, status_code=400)
    if not isinstance(params, dict):
        return JSONResponse({"error": "Params must be a JSON object."}, status_code=400)
    preset_id = save_preset(conn, name, params)
    return JSONResponse({"ok": True, "id": preset_id, "name": name})


@router.post("/{preset_id}/delete")
def delete_preset_route(preset_id: int, conn: sqlite3.Connection = Depends(get_db)):
    delete_preset(conn, preset_id)
    return JSONResponse({"ok": True})
