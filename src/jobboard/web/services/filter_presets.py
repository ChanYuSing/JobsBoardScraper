"""Filter preset CRUD — named filter configurations for Jobs page and AI scoring."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Keys that belong in a saved preset (excludes page, run_id — those are contextual).
_SCALAR_KEYS: tuple[str, ...] = (
    "keyword", "source", "company", "location_kw", "classification",
    "subclassification_kw", "triage", "sort_by", "sort_dir", "cols",
    "page_size", "date_from", "date_to", "first_seen_from", "first_seen_to",
)


def extract_params(query_params: Any, score_int_keys: set[str] | None = None) -> dict:
    """Build a storable params dict from a URL query-param mapping.

    Accepts any mapping that supports .get() and .getlist() (FastAPI QueryParams,
    Starlette QueryParams, or plain dict).
    """
    result: dict = {}

    def _get(key: str) -> str:
        return query_params.get(key, "") or ""

    def _getlist(key: str) -> list[str]:
        if hasattr(query_params, "getlist"):
            return list(query_params.getlist(key))
        v = query_params.get(key)
        return [v] if v else []

    for key in _SCALAR_KEYS:
        val = _get(key)
        if val and not (key == "page_size" and val == "15"):
            result[key] = val

    wt = _getlist("work_type")
    if wt:
        result["work_type"] = wt

    if score_int_keys:
        for name in score_int_keys:
            val = _get(f"score_min_{name}")
            if val:
                result[f"score_min_{name}"] = val

    return result


def params_to_qs(params: dict) -> str:
    """Convert a preset params dict back to a URL query string."""
    return urlencode(params, doseq=True)


# ---------------------------------------------------------------------------
# DB CRUD
# ---------------------------------------------------------------------------

def list_presets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, name, params, created_at, updated_at FROM filter_preset ORDER BY name COLLATE NOCASE"
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["params"] = json.loads(d["params"])
        d["qs"] = params_to_qs(d["params"])
        result.append(d)
    return result


def get_preset(conn: sqlite3.Connection, preset_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, name, params FROM filter_preset WHERE id = ?", (preset_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["params"] = json.loads(d["params"])
    return d


def get_preset_by_name(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, name, params FROM filter_preset WHERE name = ?", (name,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["params"] = json.loads(d["params"])
    return d


def save_preset(conn: sqlite3.Connection, name: str, params: dict) -> int:
    """Create or overwrite a preset by name. Returns the preset id."""
    now = _now()
    params_json = json.dumps(params, ensure_ascii=False)
    cur = conn.execute(
        """
        INSERT INTO filter_preset (name, params, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            params     = excluded.params,
            updated_at = excluded.updated_at
        """,
        (name, params_json, now, now),
    )
    conn.commit()
    # lastrowid is 0 for ON CONFLICT updates; fetch by name
    row = conn.execute("SELECT id FROM filter_preset WHERE name = ?", (name,)).fetchone()
    return int(row[0])


def delete_preset(conn: sqlite3.Connection, preset_id: int) -> None:
    conn.execute("DELETE FROM filter_preset WHERE id = ?", (preset_id,))
    conn.commit()
