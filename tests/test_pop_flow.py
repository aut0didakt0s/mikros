"""End-to-end coverage of the pop_flow tool.

pop_flow is the client-driven counterpart to auto-resume-on-complete: it lets a
caller explicitly abandon a digression frame and resume the frame below, without
having to drive the digression's workflow to __complete__.

Coverage:
- happy path: pop a depth-1 digression, resume the root at its paused step
- nested: pop intermediate digression in a 2-deep stack, resume inner digression
- call-frame guard: call-frames are author-resumed (frame_type_not_poppable)
- bottom-frame guard: depth-0 stack row rejected (bottom_frame_pop_rejected)
- bare-session / auto-popped path: no stack row returns no_frame_to_pop
- escalated session rejected (session_escalated)
- unknown session rejected (session_not_found)
"""

import pytest  # type: ignore[import-not-found]

from megalos_server import db, state
from megalos_server.main import WORKFLOWS
from tests.conftest import call_tool


_OUTER = "pop-outer"
_DIGRESSION = "pop-digression"
_CALL_PARENT = "pop-call-parent"
_CALL_CHILD = "pop-call-child"


def _outer_wf() -> dict:
    return {
        "name": _OUTER,
        "description": "outer linear workflow",
        "category": "test",
        "output_format": "text",
        "steps": [
            {"id": "o1", "title": "Outer 1", "directive_template": "do o1",
             "gates": ["done"], "anti_patterns": []},
            {"id": "o2", "title": "Outer 2", "directive_template": "do o2",
             "gates": ["done"], "anti_patterns": []},
            {"id": "o3", "title": "Outer 3", "directive_template": "do o3",
             "gates": ["done"], "anti_patterns": []},
        ],
    }


def _digression_wf() -> dict:
    return {
        "name": _DIGRESSION,
        "description": "short digression",
        "category": "test",
        "output_format": "text",
        "steps": [
            {"id": "d1", "title": "Digression 1", "directive_template": "do d1",
             "gates": ["done"], "anti_patterns": []},
            {"id": "d2", "title": "Digression 2", "directive_template": "do d2",
             "gates": ["done"], "anti_patterns": []},
        ],
    }


def _call_parent_wf() -> dict:
    return {
        "name": _CALL_PARENT,
        "description": "parent with call step",
        "category": "test",
        "output_format": "text",
        "steps": [
            {"id": "p1", "title": "P1", "directive_template": "do p1",
             "gates": ["done"], "anti_patterns": []},
            {"id": "p2", "title": "Call step", "directive_template": "hand off",
             "gates": ["done"], "anti_patterns": [], "call": _CALL_CHILD},
            {"id": "p3", "title": "P3", "directive_template": "do p3",
             "gates": ["done"], "anti_patterns": []},
        ],
    }


def _call_child_wf() -> dict:
    return {
        "name": _CALL_CHILD,
        "description": "callable child",
        "category": "test",
        "output_format": "text",
        "steps": [
            {"id": "c1", "title": "C1", "directive_template": "do c1",
             "gates": ["done"], "anti_patterns": []},
        ],
    }


@pytest.fixture(autouse=True)
def _register_wfs():
    WORKFLOWS[_OUTER] = _outer_wf()
    WORKFLOWS[_DIGRESSION] = _digression_wf()
    WORKFLOWS[_CALL_PARENT] = _call_parent_wf()
    WORKFLOWS[_CALL_CHILD] = _call_child_wf()
    yield
    WORKFLOWS.pop(_OUTER, None)
    WORKFLOWS.pop(_DIGRESSION, None)
    WORKFLOWS.pop(_CALL_PARENT, None)
    WORKFLOWS.pop(_CALL_CHILD, None)


def _start_outer() -> str:
    r = call_tool("start_workflow", {"workflow_type": _OUTER, "context": ""})
    return r["session_id"]


def _advance_outer_to_o2(outer_sid: str) -> None:
    r = call_tool("submit_step", {"session_id": outer_sid, "step_id": "o1", "content": "o1-done"})
    assert r.get("code") is None, r


def _push_digression(outer_sid: str, paused_at: str = "o2", ctx: str = "why-digress") -> dict:
    return call_tool(
        "push_flow",
        {
            "session_id": outer_sid,
            "workflow_type": _DIGRESSION,
            "paused_at_step": paused_at,
            "context": ctx,
        },
    )


# --- happy path -------------------------------------------------------------


def test_pop_flow_digression_resumes_frame_below():
    outer_sid = _start_outer()
    _advance_outer_to_o2(outer_sid)
    push = _push_digression(outer_sid)
    child_sid = push["session_id"]

    r = call_tool("pop_flow", {"session_id": child_sid})

    # Shape parity with auto-resume-on-complete (_resume_parent_after_digression):
    # session_id is the resumed (outer) session, resumed_from_digression marker
    # is present, child_session_id carries the popped frame.
    assert r["session_id"] == outer_sid
    assert r["resumed_from_digression"] is True
    assert r["child_session_id"] == child_sid
    assert r["current_step"]["id"] == "o2"
    assert r["directive"] == "do o2"

    # Stack cleaned: popped child gone, no frame above outer.
    assert state.stack_depth(outer_sid) == 0
    assert state.own_frame(child_sid) is None


def test_pop_flow_2_deep_resumes_intermediate():
    outer_sid = _start_outer()
    _advance_outer_to_o2(outer_sid)
    push1 = _push_digression(outer_sid, paused_at="o2")
    d1_sid = push1["session_id"]
    # Push a second digression onto d1 (d1 is at d1 step by construction).
    push2 = call_tool(
        "push_flow",
        {
            "session_id": d1_sid,
            "workflow_type": _DIGRESSION,
            "paused_at_step": "d1",
            "context": "deeper",
        },
    )
    d2_sid = push2["session_id"]
    assert state.stack_depth(outer_sid) == 2

    r = call_tool("pop_flow", {"session_id": d2_sid})

    # Pops d2, resumes d1 (not the root outer).
    assert r["session_id"] == d1_sid
    assert r["child_session_id"] == d2_sid
    assert r["current_step"]["id"] == "d1"
    assert state.stack_depth(outer_sid) == 1
    # d1 is still the top frame of the outer's chain.
    top = state.peek_frame(outer_sid)
    assert top is not None and top["session_id"] == d1_sid


# --- guard: call-frames are not poppable ------------------------------------


def test_pop_flow_rejects_call_frame():
    start = call_tool("start_workflow", {"workflow_type": _CALL_PARENT, "context": ""})
    parent_sid = start["session_id"]
    call_tool("submit_step", {"session_id": parent_sid, "step_id": "p1", "content": "p1"})
    spawn = call_tool(
        "enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p2"}
    )
    child_sid = spawn["session_id"]

    r = call_tool("pop_flow", {"session_id": child_sid})
    assert r["code"] == "frame_type_not_poppable"
    assert r["frame_type"] == "call"
    assert r["session_id"] == child_sid
    # Stack unchanged — the guard didn't mutate state.
    assert state.stack_depth(parent_sid) == 1


# --- guard: bottom-frame (depth 0) -----------------------------------------


def test_pop_flow_rejects_bottom_frame():
    """Depth-0 stack row is unreachable via normal tool surface (S01 pushes
    always land at depth >= 1). Inject one directly to exercise the guard,
    which exists for schema evolutions that might introduce depth-0 rows.
    """
    outer_sid = _start_outer()
    # Inject a depth-0 stack row for outer_sid itself.
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO session_stack (session_id, root_session_id, depth, "
            "frame_type, call_step_id, created_at) VALUES (?, ?, 0, 'digression', NULL, ?)",
            (outer_sid, outer_sid, "2026-04-19T00:00:00+00:00"),
        )

    r = call_tool("pop_flow", {"session_id": outer_sid})
    assert r["code"] == "bottom_frame_pop_rejected"
    assert r["frame_type"] == "digression"
    assert r["session_id"] == outer_sid


# --- guard: bare session / no frame -----------------------------------------


def test_pop_flow_on_completed_auto_popped_frame():
    """Pins auto-pop semantic: completing a digression deletes the child
    session entirely (sessions row AND stack row removed). A subsequent
    pop_flow on that child id resolves to session_not_found, not
    no_frame_to_pop — the session is truly gone. This test exists to make
    that outcome explicit rather than emergent."""
    outer_sid = _start_outer()
    _advance_outer_to_o2(outer_sid)
    push = _push_digression(outer_sid)
    child_sid = push["session_id"]
    # Drive digression to __complete__ — S01's auto-resume deletes the child.
    call_tool("submit_step", {"session_id": child_sid, "step_id": "d1", "content": "d1-done"})
    call_tool("submit_step", {"session_id": child_sid, "step_id": "d2", "content": "d2-done"})

    r = call_tool("pop_flow", {"session_id": child_sid})
    # Child was deleted by auto-resume; _resolve_session raises session_not_found.
    assert r["code"] == "session_not_found"

    # Separately prove no_frame_to_pop fires on a live bare session (root with
    # no stack row) — the other code path into the "no frame" family.
    bare_sid = _start_outer()
    r2 = call_tool("pop_flow", {"session_id": bare_sid})
    assert r2["code"] == "no_frame_to_pop"
    assert r2["session_id"] == bare_sid


# --- guard: escalated session ----------------------------------------------


def test_pop_flow_rejects_escalated():
    outer_sid = _start_outer()
    _advance_outer_to_o2(outer_sid)
    push = _push_digression(outer_sid)
    child_sid = push["session_id"]
    state.set_escalation(child_sid, "test_guardrail", "forced-escalation")

    r = call_tool("pop_flow", {"session_id": child_sid})
    assert r["code"] == "session_escalated"
    assert r["session_id"] == child_sid
    # Frame still in place — guard blocked mutation.
    assert state.own_frame(child_sid) is not None


# --- guard: unknown session ------------------------------------------------


def test_pop_flow_session_not_found():
    r = call_tool("pop_flow", {"session_id": "no-such-session"})
    assert r["code"] == "session_not_found"
