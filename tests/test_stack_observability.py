"""Stack observability surfaces: get_state.stack + list_sessions annotations.

Covers:
- get_state.stack returns the full chain (same array from any session in chain)
- get_state.stack is empty for bare sessions not in any chain
- get_state.stack call-frame entry carries call_step_id (not paused_at_step)
- list_sessions entries carry stack_depth + under_session_id across mixed
  stacked + bare sessions
- parent-owned guard (revise_step) fires on an intermediate frame in a 2-deep
  digression chain — pins the guard-uniformity semantic so the guard cannot
  regress to a leaf-only check.
"""

import pytest  # type: ignore[import-not-found]

from megalos_server import state
from megalos_server.main import WORKFLOWS
from tests.conftest import call_tool


_OUTER = "obs-outer"
_DIGRESSION = "obs-digression"
_CALL_PARENT = "obs-call-parent"
_CALL_CHILD = "obs-call-child"
_BARE = "obs-bare"


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


def _bare_wf() -> dict:
    return {
        "name": _BARE,
        "description": "bare standalone workflow",
        "category": "test",
        "output_format": "text",
        "steps": [
            {"id": "b1", "title": "B1", "directive_template": "do b1",
             "gates": ["done"], "anti_patterns": []},
        ],
    }


@pytest.fixture(autouse=True)
def _register_wfs():
    WORKFLOWS[_OUTER] = _outer_wf()
    WORKFLOWS[_DIGRESSION] = _digression_wf()
    WORKFLOWS[_CALL_PARENT] = _call_parent_wf()
    WORKFLOWS[_CALL_CHILD] = _call_child_wf()
    WORKFLOWS[_BARE] = _bare_wf()
    yield
    WORKFLOWS.pop(_OUTER, None)
    WORKFLOWS.pop(_DIGRESSION, None)
    WORKFLOWS.pop(_CALL_PARENT, None)
    WORKFLOWS.pop(_CALL_CHILD, None)
    WORKFLOWS.pop(_BARE, None)


def _start_outer_at_o2() -> str:
    r = call_tool("start_workflow", {"workflow_type": _OUTER, "context": ""})
    sid = r["session_id"]
    call_tool("submit_step", {"session_id": sid, "step_id": "o1", "content": "o1-done"})
    return sid


def _push_digression(outer_sid: str, paused_at: str, ctx: str = "why-digress") -> dict:
    return call_tool(
        "push_flow",
        {
            "session_id": outer_sid,
            "workflow_type": _DIGRESSION,
            "paused_at_step": paused_at,
            "context": ctx,
        },
    )


def _build_two_deep_digression_chain() -> tuple[str, str, str]:
    """Returns (root_sid, d1_sid, d2_sid) for root+D1+D2 chain."""
    root_sid = _start_outer_at_o2()
    push1 = _push_digression(root_sid, "o2", ctx="first-digress")
    d1_sid = push1["session_id"]
    # D1 is now the top of the chain; advance D1 to its own d2 step so we can
    # push another digression on top of it without out_of_order_submission.
    # But we need D1 at a step it can pause at. Start D1 at d1 (its first step);
    # push another digression on top paused at d1.
    push2 = _push_digression(d1_sid, "d1", ctx="second-digress")
    assert push2.get("code") is None, push2
    d2_sid = push2["session_id"]
    return root_sid, d1_sid, d2_sid


# --- get_state.stack --------------------------------------------------------


def test_get_state_stack_full_chain_from_root():
    root_sid, d1_sid, d2_sid = _build_two_deep_digression_chain()
    r = call_tool("get_state", {"session_id": root_sid})
    stack = r["stack"]
    assert [e["depth"] for e in stack] == [0, 1, 2]
    assert [e["session_id"] for e in stack] == [root_sid, d1_sid, d2_sid]
    assert [e["frame_type"] for e in stack] == ["digression", "digression", "digression"]
    # Root entry: synthesised, both step fields null.
    assert stack[0]["paused_at_step"] is None
    assert stack[0]["call_step_id"] is None
    # Digression frames: paused_at_step populated, call_step_id null.
    assert stack[1]["paused_at_step"] == "o2"
    assert stack[1]["call_step_id"] is None
    assert stack[2]["paused_at_step"] == "d1"
    assert stack[2]["call_step_id"] is None


def test_get_state_stack_full_chain_from_intermediate():
    root_sid, d1_sid, d2_sid = _build_two_deep_digression_chain()
    # D1 cannot be queried via get_state directly while owned by root — but
    # get_state has no parent-owned guard; only revise/delete do. Confirm:
    r = call_tool("get_state", {"session_id": d1_sid})
    assert r.get("code") is None, r
    stack = r["stack"]
    # Same full chain, regardless of which session asked.
    assert [e["depth"] for e in stack] == [0, 1, 2]
    assert [e["session_id"] for e in stack] == [root_sid, d1_sid, d2_sid]


def test_get_state_stack_empty_for_bare_session():
    r = call_tool("start_workflow", {"workflow_type": _BARE, "context": ""})
    sid = r["session_id"]
    state_r = call_tool("get_state", {"session_id": sid})
    assert state_r["stack"] == []


def test_get_state_stack_call_frame_has_call_step_id():
    start = call_tool("start_workflow", {"workflow_type": _CALL_PARENT, "context": ""})
    parent_sid = start["session_id"]
    call_tool("submit_step", {"session_id": parent_sid, "step_id": "p1", "content": "p1"})
    spawn = call_tool(
        "enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p2"}
    )
    child_sid = spawn["session_id"]
    r = call_tool("get_state", {"session_id": child_sid})
    stack = r["stack"]
    # Chain: root (parent_sid synthesised) + call-frame (child_sid at depth 1).
    assert [e["session_id"] for e in stack] == [parent_sid, child_sid]
    call_entry = stack[1]
    assert call_entry["frame_type"] == "call"
    assert call_entry["call_step_id"] == "p2"
    assert call_entry["paused_at_step"] is None


# --- list_sessions annotations ----------------------------------------------


def test_list_sessions_annotations_for_stacked_and_bare():
    # Bare session (no stack).
    bare = call_tool("start_workflow", {"workflow_type": _BARE, "context": ""})
    bare_sid = bare["session_id"]
    # 2-deep digression chain.
    root_sid, d1_sid, d2_sid = _build_two_deep_digression_chain()
    entries = {
        s["session_id"]: s
        for s in call_tool("list_sessions", {})["sessions"]
    }
    # Bare: not in any stack.
    assert entries[bare_sid]["stack_depth"] == 0
    assert entries[bare_sid]["under_session_id"] is None
    # Root of chain: itself a root, not 'under' anything.
    assert entries[root_sid]["stack_depth"] == 0
    assert entries[root_sid]["under_session_id"] is None
    # D1 intermediate: depth 1, under root.
    assert entries[d1_sid]["stack_depth"] == 1
    assert entries[d1_sid]["under_session_id"] == root_sid
    # D2 top: depth 2, under root.
    assert entries[d2_sid]["stack_depth"] == 2
    assert entries[d2_sid]["under_session_id"] == root_sid


# --- parent-owned guard uniformity probe ------------------------------------


def test_parent_owned_guard_fires_on_intermediate_frame():
    """revise_step on an intermediate digression frame (D1 in root+D1+D2)
    must raise sub_workflow_parent_owned with frame_type='digression'.
    Pins the guard-uniformity semantic: the guard fires for ANY session with
    an owning parent, not just leaf frames."""
    root_sid, d1_sid, d2_sid = _build_two_deep_digression_chain()
    r = call_tool("revise_step", {"session_id": d1_sid, "step_id": "d1"})
    assert r["code"] == "sub_workflow_parent_owned"
    assert r["session_id"] == d1_sid
    assert r["parent_session_id"] == root_sid
    assert r["frame_type"] == "digression"
    # Sanity: d1 is still framed and d2 is untouched.
    assert state.own_frame(d1_sid) is not None
    assert state.own_frame(d2_sid) is not None
