"""In-memory session store for mikros MCP server."""

import uuid
from datetime import datetime, timezone

COMPLETE = "__complete__"

_sessions: dict[str, dict] = {}


def create_session(workflow_type: str, current_step: str = "") -> str:
    """Create a new session. Returns session ID."""
    sid = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    _sessions[sid] = {
        "session_id": sid,
        "workflow_type": workflow_type,
        "current_step": current_step,
        "step_data": {},
        "created_at": now,
        "updated_at": now,
    }
    return sid


def get_session(session_id: str) -> dict:
    """Get session by ID. Raises KeyError if not found."""
    if session_id not in _sessions:
        raise KeyError(f"Session not found: {session_id}")
    return _sessions[session_id]


def update_session(session_id: str, **kwargs: object) -> None:
    """Update session fields. Raises KeyError if not found.

    Accepted kwargs: current_step (str), step_data (dict).
    """
    if session_id not in _sessions:
        raise KeyError(f"Session not found: {session_id}")
    session = _sessions[session_id]
    if "current_step" in kwargs:
        session["current_step"] = kwargs["current_step"]
    if "step_data" in kwargs:
        session["step_data"] = kwargs["step_data"]
    session["updated_at"] = datetime.now(timezone.utc).isoformat()


def list_sessions() -> list[dict]:
    """Return all sessions with status field (active/completed)."""
    result = []
    for s in _sessions.values():
        status = "completed" if s["current_step"] == COMPLETE else "active"
        result.append({
            "session_id": s["session_id"],
            "workflow_type": s["workflow_type"],
            "current_step": s["current_step"],
            "status": status,
            "created_at": s["created_at"],
            "updated_at": s["updated_at"],
        })
    return result


def clear_sessions() -> None:
    """Remove all sessions. Used by tests."""
    _sessions.clear()


def invalidate_steps_after(session_id: str, step_ids: list[str]) -> None:
    """Delete step_data entries for the given step IDs."""
    session = get_session(session_id)
    for sid in step_ids:
        session["step_data"].pop(sid, None)
    session["updated_at"] = datetime.now(timezone.utc).isoformat()


def count_active() -> int:
    """Return count of non-complete sessions."""
    return sum(1 for s in _sessions.values() if s["current_step"] != COMPLETE)


def expire_sessions(ttl_hours: int = 24) -> list[str]:
    """Delete sessions whose updated_at is older than ttl_hours. Returns deleted IDs."""
    now = datetime.now(timezone.utc)
    expired = []
    for sid, s in list(_sessions.items()):
        updated = datetime.fromisoformat(s["updated_at"])
        if (now - updated).total_seconds() > ttl_hours * 3600:
            expired.append(sid)
            del _sessions[sid]
    return expired


def delete_session(session_id: str) -> dict:
    """Remove session and return its data. Raises KeyError if not found."""
    if session_id not in _sessions:
        raise KeyError(f"Session not found: {session_id}")
    return _sessions.pop(session_id)
