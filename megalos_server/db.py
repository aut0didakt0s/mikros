"""SQLite schema + connection/transaction module for megalos sessions."""

import contextlib
import os
import sqlite3
from typing import Iterator

DEFAULT_DB_PATH = ":memory:"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    workflow_type TEXT NOT NULL,
    current_step TEXT NOT NULL,
    step_data TEXT NOT NULL DEFAULT '{}',
    retry_counts TEXT NOT NULL DEFAULT '{}',
    step_visit_counts TEXT NOT NULL DEFAULT '{}',
    escalation TEXT,
    artifact_checkpoints TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
)
"""

_conn: sqlite3.Connection | None = None


def _resolve_path() -> str:
    return os.environ.get("MEGALOS_DB_PATH", DEFAULT_DB_PATH)


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    path = _resolve_path()
    conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0, isolation_level=None)
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(_SCHEMA)
    _conn = conn
    return _conn


def init_schema() -> None:
    """Create the sessions table if absent. Idempotent."""
    conn = _get_conn()
    conn.execute(_SCHEMA)


@contextlib.contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Yield a connection inside BEGIN IMMEDIATE; COMMIT on clean exit, ROLLBACK on exception."""
    conn = _get_conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _reset_for_test() -> None:
    """For tests only: close and null the cached connection so a new path can take effect."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
