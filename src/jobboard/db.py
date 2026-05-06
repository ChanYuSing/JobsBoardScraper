"""Postgres (Supabase) connection layer.

Provides a thin sqlite3-compatible wrapper around psycopg3 so the rest of the
codebase can keep its existing ``conn.execute(sql, params)`` style. The wrapper
translates SQLite-style placeholders (``?`` and ``:name``) into psycopg's
``%s`` / ``%(name)s`` form on every call.

Connection details are read from the ``DATABASE_URL`` env var (loaded from
``.env`` if present). Designed for Supabase's PgBouncer transaction-mode
pooler â€” prepared statements are disabled.
"""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

import psycopg
from psycopg_pool import ConnectionPool

try:  # optional convenience: load .env at import time
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except Exception:  # noqa: BLE001
    pass


_SCHEMA_SQL = files(__package__).joinpath("schema_pg.sql").read_text(encoding="utf-8")


# â”€â”€â”€ Placeholder translation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SQLite uses `?` (positional) and `:name` (named).
# psycopg3 uses `%s`     (positional) and `%(name)s` (named).
# We translate before sending the SQL to the driver.

_NAMED_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


def _translate_sql(sql: str, params: Any) -> str:
    if isinstance(params, Mapping):
        return _NAMED_RE.sub(r"%(\1)s", sql)
    if params is None:
        return sql
    if "?" in sql:
        return sql.replace("?", "%s")
    return sql


# â”€â”€â”€ object-like row class â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class _Row:
    """Mimics object: subscriptable by index AND name; iterable; .keys()."""
    __slots__ = ("_cols", "_vals", "_idx")

    def __init__(self, cols: list[str], values: tuple) -> None:
        self._cols = cols
        self._vals = values
        self._idx = {c: i for i, c in enumerate(cols)}

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return self._vals[self._idx[key]]

    def get(self, key, default=None):
        if isinstance(key, int):
            return self._vals[key] if -len(self._vals) <= key < len(self._vals) else default
        i = self._idx.get(key)
        return self._vals[i] if i is not None else default

    def keys(self):
        return list(self._cols)

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __contains__(self, key):
        return key in self._idx

    def __repr__(self):
        return f"_Row({dict(zip(self._cols, self._vals))!r})"


def _row_factory(cursor):
    cols = [c.name for c in cursor.description] if cursor.description else []
    def make(values):
        return _Row(cols, tuple(values))
    return make


# â”€â”€â”€ Cursor wrapper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class _CurWrap:
    """Wraps a psycopg cursor so .fetchone() / fetchall() / rowcount feel like sqlite."""

    def __init__(self, cur: psycopg.Cursor) -> None:
        self._cur = cur

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size=None):
        return self._cur.fetchmany(size) if size is not None else self._cur.fetchmany()

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def close(self):
        self._cur.close()

    def __iter__(self):
        return iter(self._cur)


# â”€â”€â”€ Connection wrapper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class PgConn:
    """sqlite3-style facade around a psycopg connection."""

    def __init__(self, raw: psycopg.Connection) -> None:
        self._raw = raw

    def execute(self, sql: str, params: Any = None) -> _CurWrap:
        translated = _translate_sql(sql, params)
        cur = self._raw.cursor(row_factory=_row_factory)
        if params is None:
            cur.execute(translated)
        else:
            cur.execute(translated, params)
        return _CurWrap(cur)

    def executescript(self, sql: str) -> None:
        """Run a multi-statement script. psycopg3 supports `;`-separated DDL."""
        with self._raw.cursor() as cur:
            cur.execute(sql)

    def cursor(self) -> _CurWrap:
        return _CurWrap(self._raw.cursor(row_factory=_row_factory))

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


# â”€â”€â”€ Connect / init â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_url_lock = threading.Lock()
_DATABASE_URL: str | None = None


def _get_url() -> str:
    global _DATABASE_URL
    with _url_lock:
        if _DATABASE_URL is None:
            url = os.environ.get("DATABASE_URL", "").strip()
            if not url:
                raise RuntimeError(
                    "DATABASE_URL is not set. Create a .env file at the repo root "
                    "with: DATABASE_URL=postgresql://...:6543/postgres"
                )
            _DATABASE_URL = url
        return _DATABASE_URL


def connect(_path_unused: Any = None) -> PgConn:
    """Open a new (unpooled) Postgres connection.

    Used by CLI commands and one-off scripts. The path arg is accepted but
    ignored — kept for backward compatibility with the old SQLite call sites.
    For high-throughput web request handling, prefer ``pool_conn()`` instead.
    """
    url = _get_url()
    # Disable prepared statements — required for Supabase's PgBouncer
    # transaction-mode pooler (port 6543).
    raw = psycopg.connect(url, prepare_threshold=None, autocommit=False)
    return PgConn(raw)


# ─── Connection pool (shared across web requests) ──────────────────────────

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> ConnectionPool:
    """Lazy global connection pool.

    Pool size is small (max=8) because the Supabase free tier shares a
    60-connection pooler budget across the project. The pooler itself
    multiplexes our 8 client conns over far fewer Postgres backends.
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(
                conninfo=_get_url(),
                min_size=1,
                max_size=8,
                # Match `connect()` settings — required for Supabase pgbouncer.
                kwargs={"prepare_threshold": None, "autocommit": False},
                open=True,
            )
        return _pool


class _PoolConn(PgConn):
    """A PgConn that returns its underlying connection to the pool on close()."""
    def __init__(self, raw: psycopg.Connection, pool: ConnectionPool) -> None:
        super().__init__(raw)
        self._pool = pool

    def close(self) -> None:
        try:
            # Return to pool instead of really closing.
            self._pool.putconn(self._raw)
        except Exception:
            try:
                self._raw.close()
            except Exception:
                pass


def pool_conn() -> _PoolConn:
    """Check out a connection from the shared pool.

    Call ``.close()`` (or use it as a context manager) to return it.
    Designed for FastAPI's per-request dependency injection pattern.
    """
    pool = _get_pool()
    raw = pool.getconn()
    return _PoolConn(raw, pool)


def close_pool() -> None:
    """Close the shared pool. Call from FastAPI lifespan shutdown."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.close()
            finally:
                _pool = None


def init_schema(conn: PgConn) -> None:
    """Apply the Postgres schema (idempotent). Safe to call on every startup."""
    conn.executescript(_SCHEMA_SQL)
    conn.commit()


def sync_fields_full(conn: PgConn, fields: list) -> list[str]:
    """Full sync: upsert all given fields, delete any not present.

    ``fields`` is a list of FieldCfg objects. Returns names of deleted fields.
    """
    incoming = {f.name for f in fields}
    rows = conn.execute("SELECT id, name FROM field_def").fetchall()
    deleted: list[str] = []
    for r in rows:
        if r["name"] not in incoming:
            conn.execute("DELETE FROM field_def WHERE id = ?", (r["id"],))
            deleted.append(r["name"])
    for i, f in enumerate(fields):
        conn.execute(
            """
            INSERT INTO field_def (name, type, description, sort_order)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (name) DO UPDATE SET
                type        = EXCLUDED.type,
                description = EXCLUDED.description,
                sort_order  = EXCLUDED.sort_order
            """,
            (f.name, f.type, f.description, i),
        )
    conn.commit()
    return deleted


def sweep_orphan_runs(conn: PgConn) -> None:
    """Mark stale scheduler_run rows from a previous crashed process.

    Call once at process startup, before any new work is queued.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        UPDATE scheduler_run
           SET status      = 'error',
               finished_at = COALESCE(finished_at, ?),
               error       = COALESCE(error, 'process did not finish cleanly')
         WHERE status = 'running'
        """,
        (now,),
    )
    conn.execute(
        """
        UPDATE scheduler_run
           SET status = 'cancelled',
               error  = 'server restarted before run started'
         WHERE status = 'queued'
        """
    )
    conn.commit()
