"""Tests for observability surfaces + call-step concurrency guards.

Covers:
- get_state.called_session conditional field (present when child in flight; omitted otherwise).
- list_sessions[].parent_session_id uniform row schema (null top-level; set for children).
- submit_step response next_step.call_target hint (conditional on next step carrying `call:`).
- submit_step call-step guards: sub_workflow_pending when child in flight; requires-enter otherwise.
- T01 spawn path uses SUB_WORKFLOW_PENDING constant with byte-identical wire format.
"""

import pytest  # type: ignore[import-not-found]

from megalos_server.main import WORKFLOWS
from tests.conftest import call_tool


_PARENT = "obs-parent"
_CHILD = "obs-child"


def _parent_wf() -> dict:
    return {
        "name": _PARENT,
        "description": "parent with call step",
        "category": "test",
        "output_format": "text",
        "steps": [
            {
                "id": "p1",
                "title": "Parent step 1",
                "directive_template": "do p1",
                "gates": ["done"],
                "anti_patterns": [],
            },
            {
                "id": "p2",
                "title": "Parent call step",
                "directive_template": "hand off",
                "gates": ["done"],
                "anti_patterns": [],
                "call": _CHILD,
            },
            {
                "id": "p3",
                "title": "Parent step 3",
                "directive_template": "do p3",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


def _child_wf() -> dict:
    return {
        "name": _CHILD,
        "description": "child workflow",
        "category": "test",
        "output_format": "text",
        "steps": [
            {
                "id": "c1",
                "title": "Child step 1",
                "directive_template": "child work",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


@pytest.fixture(autouse=True)
def _register_wfs():
    WORKFLOWS[_PARENT] = _parent_wf()
    WORKFLOWS[_CHILD] = _child_wf()
    yield
    WORKFLOWS.pop(_PARENT, None)
    WORKFLOWS.pop(_CHILD, None)


def _start_parent() -> str:
    r = call_tool("start_workflow", {"workflow_type": _PARENT, "context": ""})
    return r["session_id"]


def _advance_parent_to_call_step(parent_sid: str) -> None:
    r = call_tool("submit_step", {"session_id": parent_sid, "step_id": "p1", "content": "first-content"})
    assert "error" not in r, r


def _spawn_child(parent_sid: str) -> str:
    r = call_tool("enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p2"})
    return r["session_id"]


# --- T04 tests --------------------------------------------------------------


def test_get_state_parent_surfaces_called_session_when_child_in_flight():
    parent_sid = _start_parent()
    _advance_parent_to_call_step(parent_sid)
    child_sid = _spawn_child(parent_sid)
    state_r = call_tool("get_state", {"session_id": parent_sid})
    assert state_r["called_session"] == child_sid


def test_get_state_parent_omits_called_session_when_no_child():
    parent_sid = _start_parent()
    state_r = call_tool("get_state", {"session_id": parent_sid})
    assert "called_session" not in state_r


def test_get_state_child_omits_called_session():
    parent_sid = _start_parent()
    _advance_parent_to_call_step(parent_sid)
    child_sid = _spawn_child(parent_sid)
    state_r = call_tool("get_state", {"session_id": child_sid})
    assert "called_session" not in state_r


def test_list_sessions_sets_parent_session_id_null_on_top_level():
    parent_sid = _start_parent()
    sessions = call_tool("list_sessions", {})["sessions"]
    row = next(s for s in sessions if s["session_id"] == parent_sid)
    assert row["parent_session_id"] is None


def test_list_sessions_sets_parent_session_id_on_child():
    parent_sid = _start_parent()
    _advance_parent_to_call_step(parent_sid)
    child_sid = _spawn_child(parent_sid)
    sessions = call_tool("list_sessions", {})["sessions"]
    row = next(s for s in sessions if s["session_id"] == child_sid)
    assert row["parent_session_id"] == parent_sid


def test_submit_step_next_step_has_call_target_when_next_is_call_step():
    parent_sid = _start_parent()
    r = call_tool("submit_step", {"session_id": parent_sid, "step_id": "p1", "content": "first-content"})
    assert r["next_step"]["call_target"] == _CHILD


def test_submit_step_next_step_omits_call_target_when_next_is_normal_step():
    # After spawning child and letting it complete, parent advances from p2 to p3.
    # p3 is a normal step, so next_step (for a hypothetical subsequent submit) has no call_target.
    # Simpler: submit p1 -> next is p2 (call-step, has call_target). Test the normal case via
    # a workflow where p1's next is p3 instead. Easiest: construct a parent workflow where a
    # linear step's next has no call field. Reuse canonical.
    # Use ad-hoc workflow: linear step -> normal step.
    wf = {
        "name": "obs-linear",
        "description": "linear no call",
        "category": "test",
        "output_format": "text",
        "steps": [
            {"id": "s1", "title": "S1", "directive_template": "d1", "gates": ["g"], "anti_patterns": []},
            {"id": "s2", "title": "S2", "directive_template": "d2", "gates": ["g"], "anti_patterns": []},
        ],
    }
    WORKFLOWS["obs-linear"] = wf
    try:
        start = call_tool("start_workflow", {"workflow_type": "obs-linear", "context": ""})
        r = call_tool("submit_step", {"session_id": start["session_id"], "step_id": "s1", "content": "x"})
        assert "call_target" not in r["next_step"]
    finally:
        WORKFLOWS.pop("obs-linear", None)


def test_submit_step_on_call_step_with_child_in_flight_returns_pending():
    parent_sid = _start_parent()
    _advance_parent_to_call_step(parent_sid)
    _spawn_child(parent_sid)
    r = call_tool("submit_step", {"session_id": parent_sid, "step_id": "p2", "content": "oops"})
    assert r["code"] == "sub_workflow_pending"


def test_submit_step_on_call_step_with_no_child_returns_requires_enter():
    parent_sid = _start_parent()
    _advance_parent_to_call_step(parent_sid)
    r = call_tool("submit_step", {"session_id": parent_sid, "step_id": "p2", "content": "oops"})
    assert r["code"] == "call_step_requires_enter_sub_workflow"


def test_call_step_requires_enter_envelope_includes_hint():
    parent_sid = _start_parent()
    _advance_parent_to_call_step(parent_sid)
    r = call_tool("submit_step", {"session_id": parent_sid, "step_id": "p2", "content": "oops"})
    assert r["hint"] == "enter_sub_workflow"


def test_call_step_requires_enter_envelope_includes_call_target():
    parent_sid = _start_parent()
    _advance_parent_to_call_step(parent_sid)
    r = call_tool("submit_step", {"session_id": parent_sid, "step_id": "p2", "content": "oops"})
    assert r["call_target"] == _CHILD


def test_sub_workflow_pending_envelope_includes_child_session_id():
    parent_sid = _start_parent()
    _advance_parent_to_call_step(parent_sid)
    child_sid = _spawn_child(parent_sid)
    r = call_tool("submit_step", {"session_id": parent_sid, "step_id": "p2", "content": "oops"})
    assert r["child_session_id"] == child_sid


def test_enter_sub_workflow_already_in_flight_uses_pending_constant():
    parent_sid = _start_parent()
    _advance_parent_to_call_step(parent_sid)
    _spawn_child(parent_sid)
    r = call_tool("enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p2"})
    assert r["code"] == "sub_workflow_pending"
