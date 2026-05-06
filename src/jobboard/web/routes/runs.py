"""Runs routes."""
from __future__ import annotations

import sqlite3
import time
from typing import Generator

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from ..deps import get_db, templates
from ..services.runs import list_runs, purge_all_runs, delete_runs_by_ids

router = APIRouter()


@router.get("/runs", response_class=HTMLResponse)
def runs_page(
    request: Request,
    flash: str = "",
    flash_type: str = "",
    conn: sqlite3.Connection = Depends(get_db),
):
    runs = list_runs(conn)
    return templates.TemplateResponse(
        request,
        "runs.html",
        {
            "active": "runs",
            "runs": runs,
            "flash": flash,
            "flash_type": flash_type,
        },
    )


@router.post("/runs/kill-run")
def kill_run(run_id: int = Form(...)):
    from ...scheduler import _cancel_event, _cancel_file, _run_active
    from ...db import connect as db_connect
    from ...scheduler_db import cancel_run_by_id
    from ..deps import get_config

    cfg = get_config()
    conn = db_connect(cfg.storage.sqlite_path)
    try:
        cancelled_count = cancel_run_by_id(conn, run_id)
    finally:
        conn.close()

    # Signal in-process worker to stop.
    _cancel_event.set()
    # Also write cancel file for a CLI-started scheduler process.
    try:
        _cancel_file(cfg.storage.sqlite_path).touch()
    except Exception:
        pass

    if cancelled_count == 0 and not _run_active.is_set():
        return RedirectResponse(
            "/runs?flash=No+active+run+found+%E2%80%94+nothing+to+kill.", status_code=303
        )

    return RedirectResponse(
        "/runs?flash=Run+cancelled.+Worker+stops+at+next+checkpoint.",
        status_code=303,
    )


@router.post("/runs/purge")
def purge_runs(conn: sqlite3.Connection = Depends(get_db)):
    """Delete all non-running run records."""
    deleted = purge_all_runs(conn)
    return RedirectResponse(
        f"/runs?flash=Cleared+{deleted}+run+record(s).", status_code=303
    )


@router.post("/runs/delete-selected")
async def delete_selected(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Delete the run records whose checkboxes were ticked."""
    form = await request.form()
    ids = [int(v) for v in form.getlist("run_ids") if str(v).isdigit()]
    deleted = delete_runs_by_ids(conn, ids)
    return RedirectResponse(
        f"/runs?flash=Deleted+{deleted}+run+record(s).", status_code=303
    )


@router.get("/runs/progress-stream")
def progress_stream():
    """SSE — pushes live jobs_found every 2 s while a run is active, then 'done'."""
    import json
    from ...scheduler import _run_active, _run_queue
    from ...db import connect as db_connect
    from ..deps import get_config

    cfg = get_config()

    def _generate() -> Generator[str, None, None]:
        idle_ticks = 0
        while True:
            time.sleep(2)
            rows = []
            try:
                conn = db_connect(cfg.storage.sqlite_path)
                try:
                    rows = conn.execute(
                        "SELECT id, status, jobs_found, jobs_total, started_at"
                        " FROM scheduler_run WHERE status IN ('running', 'queued')"
                    ).fetchall()
                finally:
                    conn.close()
                if rows:
                    payload = {
                        str(r[0]): {
                            "status":     r[1],
                            "found":      r[2],
                            "total":      r[3],
                            "started_at": r[4],
                        }
                        for r in rows
                    }
                    yield f"event: progress\ndata: {json.dumps(payload)}\n\n"
            except Exception:
                pass

            if _run_active.is_set() or not _run_queue.empty() or rows:
                idle_ticks = 0
            else:
                idle_ticks += 1
                if idle_ticks >= 3:   # ~6 s after run ends
                    yield "event: done\ndata: \n\n"
                    return

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
