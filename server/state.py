"""SQLite-backed session persistence for mikros MCP server."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import os

_default_path = Path(__file__).parent / "mikros_sessions.db"
# Horizon deploys to a read-only filesystem; fall back to /tmp.
DB_PATH = _default_path if os.access(_default_path.parent, os.W_OK) else Path("/tmp/mikros_sessions.db")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    workflow_type TEXT NOT NULL,
    current_step TEXT NOT NULL DEFAULT '',
    step_data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_initialized = False


def _connect() -> sqlite3.Connection:
    global _initialized
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    if not _initialized:
        conn.execute(_CREATE_TABLE)
        _initialized = True
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["step_data"] = json.loads(d["step_data"])
    return d


def create_session(workflow_type: str, current_step: str = "") -> str:
    """Create a new session. Returns session ID."""
    sid = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, workflow_type, current_step, step_data, created_at, updated_at) "
            "VALUES (?, ?, ?, '{}', ?, ?)",
            (sid, workflow_type, current_step, now, now),
        )
    return sid


def get_session(session_id: str) -> dict:
    """Get session by ID. Raises KeyError if not found."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        raise KeyError(f"Session not found: {session_id}")
    return _row_to_dict(row)


def update_session(session_id: str, **kwargs: object) -> None:
    """Update session fields. Raises KeyError if not found.

    Accepted kwargs: current_step (str), step_data (dict).
    """
    sets, vals = [], []
    if "current_step" in kwargs:
        sets.append("current_step = ?")
        vals.append(kwargs["current_step"])
    if "step_data" in kwargs:
        sets.append("step_data = ?")
        vals.append(json.dumps(kwargs["step_data"]))
    if not sets:
        return
    sets.append("updated_at = ?")
    vals.append(datetime.now(timezone.utc).isoformat())
    vals.append(session_id)
    with _connect() as conn:
        cursor = conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE session_id = ?", vals)
        if cursor.rowcount == 0:
            raise KeyError(f"Session not found: {session_id}")
