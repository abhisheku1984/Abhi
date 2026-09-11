"""
SQLite persistence layer.

Deliberately dependency-free (stdlib ``sqlite3``) so the application can always
boot, with a real migration runner so the schema can evolve safely. Writes are
serialised through a process-wide lock and the database runs in WAL mode, which
is a correct fit for this workload (many readers, short writes).

A ``DB_URL`` style abstraction is intentionally avoided for now; when Postgres
becomes necessary (Phase 3.5 in NEXT_BUILD_PLAN.md) only this module changes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .config import get_settings

_write_lock = threading.RLock()
_local = threading.local()

SCHEMA: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'admin',
            created_at    TEXT NOT NULL,
            last_login_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token      TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS api_keys (
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            key_hash     TEXT NOT NULL UNIQUE,
            prefix       TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            last_used_at TEXT,
            revoked_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS projects (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            style_prompt TEXT NOT NULL DEFAULT '',
            aspect_ratio TEXT NOT NULL DEFAULT '16:9',
            status      TEXT NOT NULL DEFAULT 'active',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS assets (
            id             TEXT PRIMARY KEY,
            project_id     TEXT REFERENCES projects(id) ON DELETE CASCADE,
            kind           TEXT NOT NULL,
            filename       TEXT NOT NULL,
            rel_path       TEXT NOT NULL,
            thumb_path     TEXT,
            mime           TEXT NOT NULL DEFAULT 'application/octet-stream',
            size_bytes     INTEGER NOT NULL DEFAULT 0,
            width          INTEGER,
            height         INTEGER,
            duration_s     REAL,
            sha256         TEXT NOT NULL DEFAULT '',
            provider       TEXT NOT NULL DEFAULT '',
            engine         TEXT NOT NULL DEFAULT '',
            prompt         TEXT NOT NULL DEFAULT '',
            params_json    TEXT NOT NULL DEFAULT '{}',
            source_job_id  TEXT,
            parent_asset_id TEXT,
            meta_json      TEXT NOT NULL DEFAULT '{}',
            created_at     TEXT NOT NULL,
            deleted_at     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_assets_project ON assets(project_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);

        CREATE TABLE IF NOT EXISTS jobs (
            id             TEXT PRIMARY KEY,
            project_id     TEXT REFERENCES projects(id) ON DELETE CASCADE,
            kind           TEXT NOT NULL,
            provider       TEXT NOT NULL DEFAULT '',
            engine         TEXT NOT NULL DEFAULT '',
            label          TEXT NOT NULL DEFAULT '',
            status         TEXT NOT NULL DEFAULT 'queued',
            progress       REAL NOT NULL DEFAULT 0,
            priority       INTEGER NOT NULL DEFAULT 5,
            params_json    TEXT NOT NULL DEFAULT '{}',
            result_json    TEXT NOT NULL DEFAULT '{}',
            error          TEXT,
            attempts       INTEGER NOT NULL DEFAULT 0,
            max_attempts   INTEGER NOT NULL DEFAULT 1,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            logs_json      TEXT NOT NULL DEFAULT '[]',
            created_at     TEXT NOT NULL,
            started_at     TEXT,
            finished_at    TEXT,
            duration_ms    INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS characters (
            id             TEXT PRIMARY KEY,
            project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name           TEXT NOT NULL,
            role           TEXT NOT NULL DEFAULT '',
            description    TEXT NOT NULL DEFAULT '',
            appearance     TEXT NOT NULL DEFAULT '',
            style          TEXT NOT NULL DEFAULT '',
            seed           INTEGER NOT NULL DEFAULT 0,
            ref_asset_ids  TEXT NOT NULL DEFAULT '[]',
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_characters_project ON characters(project_id);

        CREATE TABLE IF NOT EXISTS stories (
            id          TEXT PRIMARY KEY,
            project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title       TEXT NOT NULL,
            logline     TEXT NOT NULL DEFAULT '',
            genre       TEXT NOT NULL DEFAULT '',
            tone        TEXT NOT NULL DEFAULT '',
            premise     TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'draft',
            scenes_json TEXT NOT NULL DEFAULT '[]',
            engine      TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_stories_project ON stories(project_id);

        CREATE TABLE IF NOT EXISTS shots (
            id             TEXT PRIMARY KEY,
            story_id       TEXT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
            project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            index_no       INTEGER NOT NULL DEFAULT 0,
            scene_no       INTEGER NOT NULL DEFAULT 1,
            title          TEXT NOT NULL DEFAULT '',
            description    TEXT NOT NULL DEFAULT '',
            prompt         TEXT NOT NULL DEFAULT '',
            character_ids  TEXT NOT NULL DEFAULT '[]',
            duration_s     REAL NOT NULL DEFAULT 3,
            transition     TEXT NOT NULL DEFAULT 'fade',
            motion         TEXT NOT NULL DEFAULT 'zoom_in',
            caption        TEXT NOT NULL DEFAULT '',
            asset_id       TEXT REFERENCES assets(id) ON DELETE SET NULL,
            status         TEXT NOT NULL DEFAULT 'draft',
            audio_asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_shots_story ON shots(story_id, index_no);

        CREATE TABLE IF NOT EXISTS workflows (
            id          TEXT PRIMARY KEY,
            project_id  TEXT REFERENCES projects(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key        TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id             TEXT PRIMARY KEY,
            workflow_id    TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
            project_id     TEXT,
            status         TEXT NOT NULL DEFAULT 'pending',
            error          TEXT,
            variables_json TEXT NOT NULL DEFAULT '{}',
            steps_json     TEXT NOT NULL DEFAULT '[]',
            created_at     TEXT NOT NULL,
            finished_at    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_workflow_runs ON workflow_runs(workflow_id, created_at DESC);
        """,
    ),
]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:16]
    return f"{prefix}{raw}" if prefix else raw


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_connection() -> sqlite3.Connection:
    """One connection per thread, reused (cheap and safe with WAL)."""
    conn = getattr(_local, "conn", None)
    db_path = get_settings().db_path
    if conn is None or getattr(_local, "db_path", None) != db_path:
        conn = _connect(db_path)
        _local.conn = conn
        _local.db_path = db_path
    return conn


def close_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    with _write_lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# --------------------------------------------------------------------------
# migrations
# --------------------------------------------------------------------------
def init_db() -> int:
    """Apply pending migrations. Returns the resulting schema version."""
    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            row["version"] for row in conn.execute("SELECT version FROM schema_migrations")
        }
        for version, sql in SCHEMA:
            if version in applied:
                continue
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, utcnow()),
            )
        row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(row["v"] or 0)


# --------------------------------------------------------------------------
# query helpers
# --------------------------------------------------------------------------
def _rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def query_all(sql: str, params: Sequence = ()) -> list[dict[str, Any]]:
    cur = get_connection().execute(sql, params)
    return _rows_to_dicts(cur.fetchall())


def query_one(sql: str, params: Sequence = ()) -> dict[str, Any] | None:
    cur = get_connection().execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row else None


def execute(sql: str, params: Sequence = ()) -> int:
    with transaction() as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount


def columns_of(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}  # noqa: S608


def insert(table: str, values: dict[str, Any]) -> dict[str, Any]:
    """
    Insert a row, auto-filling ``id`` / ``created_at`` / ``updated_at`` when the
    table defines them. Tables with a natural primary key (for example
    ``sessions``, keyed by ``token``) are handled correctly.
    """
    payload = dict(values)
    now = utcnow()
    with transaction() as conn:
        available = columns_of(conn, table)
        unknown = set(payload) - available
        if unknown:
            raise ValueError(f"unknown column(s) for {table}: {', '.join(sorted(unknown))}")
        if "id" in available and not payload.get("id"):
            payload["id"] = new_id()
        if "created_at" in available and "created_at" not in payload:
            payload["created_at"] = now
        if "updated_at" in available and "updated_at" not in payload:
            payload["updated_at"] = now
        columns = ", ".join(payload.keys())
        placeholders = ", ".join(["?"] * len(payload))
        conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",  # noqa: S608 - internal table name
            list(payload.values()),
        )
    return payload


def update(table: str, row_id: str, values: dict[str, Any]) -> None:
    payload = {k: v for k, v in values.items() if k != "id"}
    if not payload:
        return
    with transaction() as conn:
        existing = {
            r["name"]
            for r in conn.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608
        }
        if "updated_at" in existing and "updated_at" not in payload:
            payload["updated_at"] = utcnow()
        sets = ", ".join(f"{k} = ?" for k in payload)
        conn.execute(
            f"UPDATE {table} SET {sets} WHERE id = ?",  # noqa: S608
            [*payload.values(), row_id],
        )


def update_where(table: str, where: str, params: Sequence, values: dict[str, Any]) -> None:
    """Update by an arbitrary predicate (used for tables keyed on a natural key)."""
    payload = {k: v for k, v in values.items() if k != "id"}
    if not payload:
        return
    sets = ", ".join(f"{k} = ?" for k in payload)
    with transaction() as conn:
        conn.execute(
            f"UPDATE {table} SET {sets} WHERE {where}",  # noqa: S608
            [*payload.values(), *params],
        )


def jloads(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def jdumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)
