"""SQLite-backed session store for megalos MCP server.

Named invariant — 'detached snapshot': get_session returns a freshly-constructed
dict on every call. Mutations to the returned dict (or its nested dicts) do NOT
persist. Use update_session (and the dedicated RMW helpers) to persist changes.
"""

import json
import uuid
from datetime import datetime, timezone

from . import db

COMPLETE = "__complete__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_session(row: tuple) -> dict:
    """Hydrate a sessions row into a detached dict. Column order matches _SELECT_COLS."""
    escalation = json.loads(row[6]) if row[6] else None
    return {
        "session_id": row[0],
        "workflow_type": row[1],
        "current_step": row[2],
        "step_data": json.loads(row[3]),
        "retry_counts": json.loads(row[4]),
        "step_visit_counts": json.loads(row[5]),
        "escalation": escalation,
        "artifact_checkpoints": json.loads(row[7]),
        "created_at": row[8],
        "updated_at": row[9],
    }


_SELECT_COLS = (
    "session_id, workflow_type, current_step, step_data, retry_counts, "
    "step_visit_counts, escalation, artifact_checkpoints, created_at, updated_at"
)


def create_session(workflow_type: str, current_step: str = "") -> str:
    """Create a new session. Returns session ID."""
    sid = uuid.uuid4().hex[:12]
    now = _now_iso()
    empty = "{}"
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, workflow_type, current_step, "
            "step_data, retry_counts, step_visit_counts, escalation, "
            "artifact_checkpoints, created_at, updated_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL)",
            (sid, workflow_type, current_step, empty, empty, empty, empty, now, now),
        )
    return sid


def get_session(session_id: str) -> dict:
    """Get session by ID. Returns a DETACHED snapshot — mutations to the returned
    dict do NOT persist. Raises KeyError if not found."""
    conn = db._get_conn()
    row = conn.execute(
        f"SELECT {_SELECT_COLS} FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Session not found: {session_id}")
    return _row_to_session(row)


def update_session(session_id: str, **kwargs: object) -> None:
    """Update session fields. Raises KeyError if not found.

    Accepted kwargs: current_step (str), step_data (dict). Unspecified columns
    are left untouched. Setting current_step to COMPLETE also stamps completed_at.
    """
    set_clauses: list[str] = []
    params: list[object] = []
    if "current_step" in kwargs:
        set_clauses.append("current_step = ?")
        params.append(kwargs["current_step"])
        if kwargs["current_step"] == COMPLETE:
            set_clauses.append("completed_at = ?")
            params.append(_now_iso())
    if "step_data" in kwargs:
        set_clauses.append("step_data = ?")
        params.append(json.dumps(kwargs["step_data"]))
    set_clauses.append("updated_at = ?")
    params.append(_now_iso())
    params.append(session_id)
    with db.transaction() as conn:
        cur = conn.execute(
            f"UPDATE sessions SET {', '.join(set_clauses)} WHERE session_id = ?",
            params,
        )
        if cur.rowcount == 0:
            raise KeyError(f"Session not found: {session_id}")


def list_sessions() -> list[dict]:
    """Return all sessions with status field (active/completed)."""
    conn = db._get_conn()
    rows = conn.execute(
        "SELECT session_id, workflow_type, current_step, created_at, updated_at FROM sessions"
    ).fetchall()
    result = []
    for row in rows:
        status = "completed" if row[2] == COMPLETE else "active"
        result.append({
            "session_id": row[0],
            "workflow_type": row[1],
            "current_step": row[2],
            "status": status,
            "created_at": row[3],
            "updated_at": row[4],
        })
    return result


def clear_sessions() -> None:
    """Remove all sessions. Used by tests."""
    with db.transaction() as conn:
        conn.execute("DELETE FROM sessions")


def invalidate_steps_after(session_id: str, step_ids: list[str]) -> None:
    """Delete step_data entries for the given step IDs."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT step_data FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Session not found: {session_id}")
        step_data = json.loads(row[0])
        for sid in step_ids:
            step_data.pop(sid, None)
        conn.execute(
            "UPDATE sessions SET step_data = ?, updated_at = ? WHERE session_id = ?",
            (json.dumps(step_data), _now_iso(), session_id),
        )


def increment_retry(session_id: str, step_id: str) -> int:
    """Increment and return retry count for a step."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT retry_counts FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Session not found: {session_id}")
        retry_counts = json.loads(row[0])
        count = retry_counts.get(step_id, 0) + 1
        retry_counts[step_id] = count
        conn.execute(
            "UPDATE sessions SET retry_counts = ?, updated_at = ? WHERE session_id = ?",
            (json.dumps(retry_counts), _now_iso(), session_id),
        )
    return count


def increment_visit(session_id: str, step_id: str) -> int:
    """Increment and return visit count for a step."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT step_visit_counts FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Session not found: {session_id}")
        visits = json.loads(row[0])
        count = visits.get(step_id, 0) + 1
        visits[step_id] = count
        conn.execute(
            "UPDATE sessions SET step_visit_counts = ?, updated_at = ? WHERE session_id = ?",
            (json.dumps(visits), _now_iso(), session_id),
        )
    return count


def set_escalation(session_id: str, guardrail_id: str, message: str) -> None:
    """Set escalation flag on a session."""
    payload = json.dumps({"guardrail_id": guardrail_id, "message": message})
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE sessions SET escalation = ?, updated_at = ? WHERE session_id = ?",
            (payload, _now_iso(), session_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"Session not found: {session_id}")


def count_active() -> int:
    """Return count of non-complete sessions."""
    conn = db._get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE current_step != ?",
        (COMPLETE,),
    ).fetchone()
    return row[0]


def expire_sessions(ttl_hours: int = 24) -> list[str]:
    """Delete sessions whose updated_at is older than ttl_hours. Returns deleted IDs."""
    now = datetime.now(timezone.utc)
    conn = db._get_conn()
    rows = conn.execute("SELECT session_id, updated_at FROM sessions").fetchall()
    expired: list[str] = []
    for sid, updated_at in rows:
        updated = datetime.fromisoformat(updated_at)
        if (now - updated).total_seconds() > ttl_hours * 3600:
            expired.append(sid)
    if expired:
        with db.transaction() as conn_w:
            conn_w.executemany(
                "DELETE FROM sessions WHERE session_id = ?",
                [(sid,) for sid in expired],
            )
    return expired


def store_artifact(session_id: str, step_id: str, artifact_id: str, content: str) -> None:
    """Store a checkpointed artifact for a step."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT artifact_checkpoints FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Session not found: {session_id}")
        checkpoints = json.loads(row[0])
        checkpoints.setdefault(step_id, {})[artifact_id] = content
        conn.execute(
            "UPDATE sessions SET artifact_checkpoints = ?, updated_at = ? WHERE session_id = ?",
            (json.dumps(checkpoints), _now_iso(), session_id),
        )


def get_artifacts(session_id: str, step_id: str) -> dict:
    """Get all checkpointed artifacts for a step. Returns empty dict if none."""
    conn = db._get_conn()
    row = conn.execute(
        "SELECT artifact_checkpoints FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Session not found: {session_id}")
    return json.loads(row[0]).get(step_id, {})


def delete_session(session_id: str) -> dict:
    """Remove session and return its data. Raises KeyError if not found."""
    with db.transaction() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Session not found: {session_id}")
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    return _row_to_session(row)


def _set_updated_at_for_test(session_id: str, iso_ts: str) -> None:
    """For tests only: backdate updated_at without touching other columns."""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (iso_ts, session_id),
        )
