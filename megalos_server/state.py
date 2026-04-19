"""SQLite-backed session store for megalos MCP server.

Named invariant — 'detached snapshot': get_session returns a freshly-constructed
dict on every call. Mutations to the returned dict (or its nested dicts) do NOT
persist. Use update_session (and the dedicated RMW helpers) to persist changes.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from . import db, errors

COMPLETE = "__complete__"

_log = logging.getLogger("megalos_server.state")


class StackFull(Exception):
    """Raised inside create_session when pushing a frame would exceed
    max_stack_depth. Caller catches this and maps to session_stack_full.

    Carries the root's current depth (pre-insert) and the cap so the error
    body at the call site can report both without a follow-up query."""

    def __init__(self, root_session_id: str, current_depth: int, max_depth: int):
        self.root_session_id = root_session_id
        self.current_depth = current_depth
        self.max_depth = max_depth
        super().__init__(
            f"stack full for root '{root_session_id}': depth {current_depth} >= max {max_depth}"
        )


def _log_eviction(reason: str, ids: list[str], **extra: object) -> None:
    """Emit one INFO line per eviction event. One line per CALL, not per row."""
    _log.info(
        "session_eviction",
        extra={
            "event": "session_eviction",
            "reason": reason,
            "count": len(ids),
            "session_ids": ids[:10],
            **extra,
        },
    )


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
        "called_session": row[10],
        "parent_session_id": row[11],
    }


# Column order is coupled to the CREATE TABLE DDL in db.py — new columns append at the end, never reorder.
_SELECT_COLS = (
    "session_id, workflow_type, current_step, step_data, retry_counts, "
    "step_visit_counts, escalation, artifact_checkpoints, created_at, updated_at, "
    "called_session, parent_session_id"
)


def create_session(
    workflow_type: str,
    current_step: str = "",
    parent_session_id: str | None = None,
    frame_type: str = "call",
    call_step_id: str | None = None,
    max_stack_depth: int | None = None,
) -> str:
    """Create a new session. Returns session ID.

    If total session count would exceed MEGALOS_SESSION_CAP, evict oldest
    completed sessions first (by completed_at ASC), falling through to oldest
    active sessions (by updated_at ASC). Cap enforcement + INSERT are atomic.

    parent_session_id: when set, marks this session as a child of the given
    parent. Stored in parent_session_id column; never mutated after create.
    When set, a session_stack frame is pushed in the same transaction so the
    stack and the legacy column agree on commit.

    frame_type: 'call' (default, M004 sub-workflow call) or 'digression'
    (M005 push_flow). Only consulted when parent_session_id is set. The
    call_step_id column on session_stack is semantically overloaded: it
    stores the parent's call-step ID for frame_type='call' (stamped later
    by set_called_session) and the paused_at_step for frame_type='digression'
    (stamped directly here). Future cleanup can rename the column.

    max_stack_depth: when set and parent_session_id is set, reject pushes
    that would bring the stack depth above this cap. Raise StackFull INSIDE
    the same BEGIN IMMEDIATE transaction that would have done the insert;
    rollback guarantees two concurrent pushes can't both observe 'depth under
    cap' and both insert. See session_stack_full body at push_flow's
    construction site for the depth-semantics definition: depth = number
    of frames above root = stack_depth(root_session_id) return value.
    Lone session = depth 0; one push = depth 1; at depth N==max_stack_depth,
    the next push is rejected.
    """
    sid = uuid.uuid4().hex[:12]
    now = _now_iso()
    empty = "{}"
    cap = errors.get_session_cap()
    evicted_ids: list[str] = []
    with db.transaction() as conn:
        count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        if count >= cap:
            # Evict down to cap-1 so the new INSERT fits.
            to_evict = count - (cap - 1)
            rows = conn.execute(
                "SELECT session_id FROM sessions "
                "ORDER BY (current_step = ?) DESC, "
                "COALESCE(completed_at, updated_at) ASC LIMIT ?",
                (COMPLETE, to_evict),
            ).fetchall()
            evicted_ids = [r[0] for r in rows]
            if evicted_ids:
                conn.executemany(
                    "DELETE FROM sessions WHERE session_id = ?",
                    [(eid,) for eid in evicted_ids],
                )
                # Evicted sessions may have had stack frames; drop those too.
                conn.executemany(
                    "DELETE FROM session_stack WHERE session_id = ?",
                    [(eid,) for eid in evicted_ids],
                )
        conn.execute(
            "INSERT INTO sessions (session_id, workflow_type, current_step, "
            "step_data, retry_counts, step_visit_counts, escalation, "
            "artifact_checkpoints, created_at, updated_at, completed_at, "
            "called_session, parent_session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, ?)",
            (sid, workflow_type, current_step, empty, empty, empty, empty, now, now, parent_session_id),
        )
        if parent_session_id is not None:
            # Locate the parent's root + depth so this child lands one level deeper.
            # Parent may itself be a framed child (multi-level stack), or a root.
            parent_row = conn.execute(
                "SELECT root_session_id, depth FROM session_stack WHERE session_id = ?",
                (parent_session_id,),
            ).fetchone()
            if parent_row is None:
                root_sid = parent_session_id
                child_depth = 1
            else:
                root_sid = parent_row[0]
                child_depth = parent_row[1] + 1
            # Atomic depth-cap: compute the current stack depth INSIDE this
            # BEGIN IMMEDIATE txn, compare to max, and insert only if under cap.
            # BEGIN IMMEDIATE serialises writers at transaction start; any
            # competing txn that has already committed a frame above this root
            # will be visible to our read here. Two concurrent push_flow calls
            # that both observed depth=N-1 pre-flight cannot both commit: the
            # second txn waits, then re-reads a fresh COUNT and raises StackFull.
            if max_stack_depth is not None:
                depth_row = conn.execute(
                    "SELECT COUNT(*) FROM session_stack WHERE root_session_id = ?",
                    (root_sid,),
                ).fetchone()
                current_stack_depth = int(depth_row[0])
                if current_stack_depth >= max_stack_depth:
                    raise StackFull(
                        root_session_id=root_sid,
                        current_depth=current_stack_depth,
                        max_depth=max_stack_depth,
                    )
            conn.execute(
                "INSERT INTO session_stack (session_id, root_session_id, depth, "
                "frame_type, call_step_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sid, root_sid, child_depth, frame_type, call_step_id, now),
            )
    # Log AFTER commit — if INSERT rolled back, DELETE rolled back too; don't lie.
    if evicted_ids:
        _log_eviction(reason="cap_exceeded", ids=evicted_ids, session_cap=cap)
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
    """Return all sessions with status field (active/completed) and parent link.

    Each entry also carries stack annotations:
      stack_depth: depth of this session's own frame (0 if session has no
        stack row — i.e. the session is a root or a bare session).
      under_session_id: the root session at depth 0 of the chain, or None
        if this session is not in any stack (bare, or itself a root with no
        frames above — roots are not 'under' anything).
    """
    conn = db._get_conn()
    rows = conn.execute(
        "SELECT session_id, workflow_type, current_step, created_at, updated_at, "
        "parent_session_id FROM sessions"
    ).fetchall()
    # Single round-trip for all stack rows; zip into a dict by session_id.
    stack_rows = conn.execute(
        "SELECT session_id, root_session_id, depth FROM session_stack"
    ).fetchall()
    stack_by_sid = {r[0]: (r[1], int(r[2])) for r in stack_rows}
    result = []
    for row in rows:
        status = "completed" if row[2] == COMPLETE else "active"
        sid = row[0]
        if sid in stack_by_sid:
            root_sid, depth = stack_by_sid[sid]
            stack_depth_val = depth
            under_sid: str | None = root_sid
        else:
            stack_depth_val = 0
            under_sid = None
        result.append({
            "session_id": sid,
            "workflow_type": row[1],
            "current_step": row[2],
            "status": status,
            "created_at": row[3],
            "updated_at": row[4],
            "parent_session_id": row[5],  # None for top-level sessions
            "stack_depth": stack_depth_val,
            "under_session_id": under_sid,
        })
    return result


def clear_sessions() -> None:
    """Remove all sessions. Used by tests."""
    with db.transaction() as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM session_stack")


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


def clear_step_data_key(session_id: str, key: str) -> None:
    """Delete a single step_data key. RMW; mirrors set_called_session shape.

    Used by revise_step when the target is a call-step: standard
    invalidate_steps_after only clears steps AFTER the target, but the
    propagated child artifact lives at step_data[target], so the target's own
    key must be cleared too. Raises KeyError if session not found.
    """
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT step_data FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Session not found: {session_id}")
        step_data = json.loads(row[0])
        step_data.pop(key, None)
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


def set_called_session(
    parent_session_id: str,
    child_session_id: str | None,
    call_step_id: str | None = None,
) -> None:
    """Set or clear the parent's called_session link. Pass None to clear.

    Keeps the legacy called_session column and the session_stack table in sync
    in a single transaction. When linking a child, stamps the child frame's
    call_step_id (the frame row itself was pushed at child create time).
    When clearing (child_session_id is None), removes the topmost frame above
    parent_session_id so the stack stops reporting the child as in-flight —
    mirroring what delete_session does when the child is hard-terminated.

    Raises KeyError if parent not found.
    """
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE sessions SET called_session = ?, updated_at = ? WHERE session_id = ?",
            (child_session_id, _now_iso(), parent_session_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"Session not found: {parent_session_id}")
        if child_session_id is not None and call_step_id is not None:
            conn.execute(
                "UPDATE session_stack SET call_step_id = ? WHERE session_id = ?",
                (call_step_id, child_session_id),
            )
        if child_session_id is None:
            # Find the frame sitting above parent_session_id (in the same root chain)
            # and remove it. Parent may be a root (no frame row of its own) or
            # itself a framed child in a deeper stack.
            parent_frame = conn.execute(
                "SELECT root_session_id, depth FROM session_stack WHERE session_id = ?",
                (parent_session_id,),
            ).fetchone()
            if parent_frame is None:
                root_sid = parent_session_id
                next_depth = 1
            else:
                root_sid = parent_frame[0]
                next_depth = parent_frame[1] + 1
            conn.execute(
                "DELETE FROM session_stack WHERE root_session_id = ? AND depth = ?",
                (root_sid, next_depth),
            )


# --- Stack accessors -----------------------------------------------------
#
# session_stack is the runtime source of truth for parent/child relationships.
# Legacy columns (sessions.parent_session_id, sessions.called_session) stay
# populated on writes as a cache for migration compat and external queries,
# but runtime reads route through these functions exclusively.


def push_frame(
    root_session_id: str,
    session_id: str,
    frame_type: str,
    call_step_id: str | None = None,
) -> int:
    """Push a frame above root_session_id. Returns the new depth.

    Caller is responsible for computing the correct root_session_id
    (a root_session_id must refer to the top-level session; push_frame
    computes depth as one greater than the deepest existing frame for that
    root, or 1 if the stack is empty)."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(depth), 0) FROM session_stack WHERE root_session_id = ?",
            (root_session_id,),
        ).fetchone()
        depth = int(row[0]) + 1
        conn.execute(
            "INSERT INTO session_stack (session_id, root_session_id, depth, "
            "frame_type, call_step_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, root_session_id, depth, frame_type, call_step_id, _now_iso()),
        )
    return depth


def pop_frame(session_id: str) -> dict | None:
    """Remove the frame whose session_id matches. Returns the removed row dict
    (session_id, root_session_id, depth, frame_type, call_step_id, created_at)
    or None if no frame existed for that session."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT session_id, root_session_id, depth, frame_type, call_step_id, created_at "
            "FROM session_stack WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM session_stack WHERE session_id = ?", (session_id,))
    return _frame_row_to_dict(row)


def peek_frame(root_session_id: str) -> dict | None:
    """Return the topmost (max depth) frame for the given root, or None if empty."""
    conn = db._get_conn()
    row = conn.execute(
        "SELECT session_id, root_session_id, depth, frame_type, call_step_id, created_at "
        "FROM session_stack WHERE root_session_id = ? ORDER BY depth DESC LIMIT 1",
        (root_session_id,),
    ).fetchone()
    return _frame_row_to_dict(row) if row else None


def stack_depth(root_session_id: str) -> int:
    """Return the number of frames sitting above root_session_id (0 if none)."""
    conn = db._get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM session_stack WHERE root_session_id = ?",
        (root_session_id,),
    ).fetchone()
    return int(row[0])


def top_frame_for(session_id: str) -> dict | None:
    """Return the frame sitting one level above session_id, or None if session_id
    is itself at the top of its chain (or has no frames rooted/framed at it).

    Works whether session_id is a root (no frame row of its own) or a framed
    child. For a root, 'above it' is the frame at depth 1 in its chain.
    For a framed child at depth N, 'above it' is the frame at depth N+1 in
    the same root chain.
    """
    conn = db._get_conn()
    own = conn.execute(
        "SELECT root_session_id, depth FROM session_stack WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if own is None:
        # Treat session_id as a root; look for depth=1 in its chain.
        root_sid = session_id
        next_depth = 1
    else:
        root_sid = own[0]
        next_depth = own[1] + 1
    row = conn.execute(
        "SELECT session_id, root_session_id, depth, frame_type, call_step_id, created_at "
        "FROM session_stack WHERE root_session_id = ? AND depth = ?",
        (root_sid, next_depth),
    ).fetchone()
    return _frame_row_to_dict(row) if row else None


def own_frame(session_id: str) -> dict | None:
    """Return the stack row for session_id (session_id's own frame), or None
    if session_id is a root (no stack row). Sibling of parent_of / top_frame_for
    that answers 'what kind of frame am I?' uniformly for call and digression frames.
    """
    conn = db._get_conn()
    row = conn.execute(
        "SELECT session_id, root_session_id, depth, frame_type, call_step_id, created_at "
        "FROM session_stack WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return _frame_row_to_dict(row) if row else None


def parent_of(session_id: str) -> str | None:
    """Return the session_id of the frame (or root) immediately below session_id.
    Returns None if session_id is a root (no frame row).
    """
    conn = db._get_conn()
    own = conn.execute(
        "SELECT root_session_id, depth FROM session_stack WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if own is None:
        return None
    root_sid, depth = own[0], int(own[1])
    if depth == 1:
        return root_sid
    row = conn.execute(
        "SELECT session_id FROM session_stack WHERE root_session_id = ? AND depth = ?",
        (root_sid, depth - 1),
    ).fetchone()
    return row[0] if row else None


def depth_breakdown() -> list[dict]:
    """Return per-root depth entries for every root session currently in state.

    Shape: list of {"root_session_id": str, "depth": int}. One entry per root.
    A 'root' is a session whose session_id does NOT appear in session_stack
    (i.e. it has no frame of its own above some deeper root). For each such
    root, depth = count of frames where root_session_id = root.session_id,
    equal to stack_depth(root). Roots with no frames above report depth 0
    (honest — they occupy a cap slot but have no stack).

    Ordering: depth DESC (deepest stacks surface first as likely cap offenders),
    ties broken by root_session_id ASC for determinism.

    Single SQL round-trip — the LEFT JOIN + GROUP BY covers both framed and
    lone-root sessions in one query.
    """
    conn = db._get_conn()
    rows = conn.execute(
        """
        SELECT s.session_id AS root_session_id,
               COUNT(st.session_id) AS depth
        FROM sessions s
        LEFT JOIN session_stack st ON st.root_session_id = s.session_id
        WHERE s.session_id NOT IN (SELECT session_id FROM session_stack)
        GROUP BY s.session_id
        ORDER BY depth DESC, s.session_id ASC
        """
    ).fetchall()
    return [{"root_session_id": r[0], "depth": int(r[1])} for r in rows]


def _frame_row_to_dict(row: tuple) -> dict:
    return {
        "session_id": row[0],
        "root_session_id": row[1],
        "depth": row[2],
        "frame_type": row[3],
        "call_step_id": row[4],
        "created_at": row[5],
    }


def full_stack(root_session_id: str) -> list[dict]:
    """Return the full frame chain rooted at root_session_id, ordered by depth ASC.

    Synthesises a depth-0 entry for the root itself (session_stack only stores
    frames at depth >= 1 — the root is implicit). Each entry:
      {depth, frame_type, session_id, paused_at_step, call_step_id}.
    Root entry: frame_type='digression', paused_at_step=None, call_step_id=None
    (root was started via start_workflow, not pushed; 'digression' is the
    closest-honest label for a non-call non-framed origin).

    For depth >= 1 entries, the session_stack.call_step_id column is overloaded:
    it stores the parent's call-step ID when frame_type='call' and the
    paused_at_step when frame_type='digression'. This function splits that
    overload into two distinct keys in the returned dict so callers never have
    to know about the column's semantic duality.

    If root_session_id has no stack (bare root with no frames above), returns
    a single-entry list (the synthesised depth-0 entry). Callers that need the
    'no stack at all' signal should check whether the queried session is itself
    in any chain before calling this.
    """
    conn = db._get_conn()
    rows = conn.execute(
        "SELECT session_id, root_session_id, depth, frame_type, call_step_id, created_at "
        "FROM session_stack WHERE root_session_id = ? ORDER BY depth ASC",
        (root_session_id,),
    ).fetchall()
    result: list[dict] = [
        {
            "depth": 0,
            "frame_type": "digression",
            "session_id": root_session_id,
            "paused_at_step": None,
            "call_step_id": None,
        }
    ]
    for row in rows:
        frame_type = row[3]
        overloaded = row[4]
        if frame_type == "digression":
            paused_at_step = overloaded
            call_step_id = None
        else:  # 'call'
            paused_at_step = None
            call_step_id = overloaded
        result.append(
            {
                "depth": int(row[2]),
                "frame_type": frame_type,
                "session_id": row[0],
                "paused_at_step": paused_at_step,
                "call_step_id": call_step_id,
            }
        )
    return result


def count_active() -> int:
    """Return count of non-complete sessions."""
    conn = db._get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE current_step != ?",
        (COMPLETE,),
    ).fetchone()
    return row[0]


def expire_sessions(ttl_hours: int = 24) -> list[str]:
    """Delete sessions past TTL. Returns deleted IDs.

    Two-clause semantic: completed sessions expire when completed_at < cutoff;
    active sessions expire when updated_at < cutoff.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ttl_hours)).isoformat()
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT session_id FROM sessions WHERE "
            "(current_step = ? AND completed_at < ?) OR "
            "(current_step != ? AND updated_at < ?)",
            (COMPLETE, cutoff, COMPLETE, cutoff),
        ).fetchall()
        expired = [r[0] for r in rows]
        if expired:
            conn.executemany(
                "DELETE FROM sessions WHERE session_id = ?",
                [(sid,) for sid in expired],
            )
            conn.executemany(
                "DELETE FROM session_stack WHERE session_id = ?",
                [(sid,) for sid in expired],
            )
    if expired:
        _log_eviction(reason="ttl_expired", ids=expired, ttl_hours=ttl_hours)
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
    """Remove session and return its data. Raises KeyError if not found.

    Also removes any session_stack frame for the deleted session (child
    termination implies the frame that represented it is gone too)."""
    with db.transaction() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Session not found: {session_id}")
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM session_stack WHERE session_id = ?", (session_id,))
    return _row_to_session(row)


def _set_updated_at_for_test(session_id: str, iso_ts: str) -> None:
    """For tests only: backdate updated_at without touching other columns."""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (iso_ts, session_id),
        )


def _set_completed_at_for_test(session_id: str, iso_ts: str) -> None:
    """For tests only: backdate completed_at without touching other columns."""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE sessions SET completed_at = ? WHERE session_id = ?",
            (iso_ts, session_id),
        )
