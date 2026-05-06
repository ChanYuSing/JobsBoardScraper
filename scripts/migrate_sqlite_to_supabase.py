"""One-shot migrator: copy data from the old SQLite DB into Supabase Postgres.

Usage (from repo root, with .env containing DATABASE_URL):

    python scripts/migrate_sqlite_to_supabase.py [path/to/jobs.sqlite]

Default source path: data/jobs.sqlite

Idempotent: uses ON CONFLICT DO NOTHING so re-running won't duplicate rows.
After migrating job_jobsdb / job_linkedin, the sync triggers will
automatically populate job_all.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Make `jobboard` importable without installing the package
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobboard.db import connect, init_schema  # noqa: E402


# Tables to copy. job_all is rebuilt by triggers.
# Tables with auto-increment id columns need special handling for the sequence.
TABLES = [
    # (sqlite_table, pg_table, conflict_cols)
    ("job_jobsdb",    "job_jobsdb",    ["job_id"]),
    ("job_linkedin",  "job_linkedin",  ["job_id"]),
    ("job_status",    "job_status",    ["source", "job_id"]),
    ("field_def",     "field_def",     ["name"]),
    ("scheduler_run", "scheduler_run", ["id"]),
    ("job_analysis",  "job_analysis",  ["source", "job_id", "field_id"]),
]

# Tables whose id sequence must be advanced after insert
SERIAL_TABLES = [("scheduler_run", "id"), ("field_def", "id")]

BATCH = 500


def _common_columns(sqlite_conn: sqlite3.Connection, pg_conn, table: str) -> list[str]:
    sqlite_cols = {r[1] for r in sqlite_conn.execute(f"PRAGMA table_info({table})").fetchall()}
    pg_rows = pg_conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = ?",
        (table,),
    ).fetchall()
    pg_cols = {r[0] for r in pg_rows}
    common = [c for c in sqlite_cols if c in pg_cols]
    return common


def _copy_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn,
    src: str,
    dst: str,
    conflict_cols: list[str],
) -> int:
    cols = _common_columns(sqlite_conn, pg_conn, src)
    if not cols:
        print(f"  [{src}] no common columns — skipping")
        return 0
    src_count = sqlite_conn.execute(f"SELECT COUNT(*) FROM {src}").fetchone()[0]
    if src_count == 0:
        print(f"  [{src}] empty — skipping")
        return 0

    col_list = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    conflict = ", ".join(conflict_cols)
    insert_sql = (
        f"INSERT INTO {dst} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) DO NOTHING"
    )

    total = 0
    cur = sqlite_conn.execute(f"SELECT {col_list} FROM {src}")
    raw_pg = pg_conn._raw  # use the underlying psycopg connection for executemany
    while True:
        batch = cur.fetchmany(BATCH)
        if not batch:
            break
        rows = [tuple(r) for r in batch]
        with raw_pg.cursor() as pgcur:
            pgcur.executemany(insert_sql, rows)
        raw_pg.commit()
        total += len(rows)
        print(f"  [{src}] {total}/{src_count}", flush=True)
    return total


def _bump_serial(pg_conn, table: str, id_col: str) -> None:
    """After copying rows with explicit ids, advance the serial so future
    inserts don't collide."""
    pg_conn.execute(
        f"SELECT setval(pg_get_serial_sequence('{table}', '{id_col}'), "
        f"COALESCE((SELECT MAX({id_col}) FROM {table}), 1))"
    )
    pg_conn.commit()


def main() -> None:
    src_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "jobs.sqlite"
    if not src_path.exists():
        print(f"ERROR: source SQLite file not found: {src_path}")
        sys.exit(1)

    print(f"Source: {src_path}")
    print("Target: (DATABASE_URL from .env)")

    sqlite_conn = sqlite3.connect(str(src_path))
    sqlite_conn.row_factory = sqlite3.Row

    pg_conn = connect()
    print("Applying Postgres schema (idempotent)…")
    init_schema(pg_conn)

    print("Copying tables…")
    for src, dst, conflict in TABLES:
        # Skip if source table doesn't exist
        exists = sqlite_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (src,),
        ).fetchone()
        if not exists:
            print(f"  [{src}] not in source — skipping")
            continue
        _copy_table(sqlite_conn, pg_conn, src, dst, conflict)

    print("Advancing id sequences…")
    for table, col in SERIAL_TABLES:
        _bump_serial(pg_conn, table, col)
        print(f"  [{table}.{col}] OK")

    # Verify
    print("\nFinal row counts in Postgres:")
    for _src, dst, _ in TABLES:
        n = pg_conn.execute(f"SELECT COUNT(*) FROM {dst}").fetchone()[0]
        print(f"  {dst:20s} {n}")
    n_all = pg_conn.execute("SELECT COUNT(*) FROM job_all").fetchone()[0]
    print(f"  {'job_all':20s} {n_all}  (populated by triggers)")

    pg_conn.close()
    sqlite_conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
