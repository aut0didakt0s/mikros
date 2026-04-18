"""SQLite schema + connection/transaction module for megalos sessions."""

import contextlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Iterator

# Anchor to repo layout: <repo>/megalos_server/db.py → <repo>/server/megalos_sessions.db
DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "server" / "megalos_sessions.db")

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
    completed_at TEXT,
    -- called_session NULL = no child in flight (parent's common case).
    -- parent_session_id NULL = this is a root session (the overwhelming common case).
    -- Both are load-bearing defaults — do NOT add NOT NULL constraints here.
    called_session TEXT,
    parent_session_id TEXT
)
"""

_tls = threading.local()


def _resolve_path() -> str:
    return os.environ.get("MEGALOS_DB_PATH", DEFAULT_DB_PATH)


def _get_conn() -> sqlite3.Connection:
    conn = getattr(_tls, "conn", None)
    if conn is not None:
        return conn
    path = _resolve_path()
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0, isolation_level=None)
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(_SCHEMA)
    # Column-order-coupled with _SELECT_COLS + _row_to_session in state.py.
    # New columns MUST append at the end; reordering silently corrupts row reads.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    for col_name, col_type in (("called_session", "TEXT"), ("parent_session_id", "TEXT")):
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col_name} {col_type}")
    _tls.conn = conn
    return conn


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
    """For tests only. Closes the calling thread's connection BEFORE clearing
    the reference so any tmp file handle is released deterministically."""
    conn = getattr(_tls, "conn", None)
    if conn is not None:
        conn.close()
        _tls.conn = None
