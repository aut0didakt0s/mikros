"""Tests for child→parent propagation bridge.

Covers:
- successful artifact propagation + parent.step_data write
- child auto-termination on propagation
- parent.called_session cleared after propagation
- bridge response shape (parent's next_step, propagated_from_sub_workflow marker)
- parent output_schema validation on child artifact (fail path → escalate + retain)
- call-step with branches → default_branch selection
- revise on call-step: retained-child cleanup + step_data clear + re-spawn
"""

import pytest  # type: ignore[import-not-found]

from megalos_server import state
from megalos_server.main import WORKFLOWS
from tests.conftest import call_tool


_PARENT = "prop-parent"
_CHILD = "prop-child"
_PARENT_BR = "prop-parent-branched"
_PARENT_SCHEMA = "prop-parent-schema"


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


def _parent_wf_branched() -> dict:
    # Parent call-step with branches + default_branch (post-S01-amendment: both present).
    return {
        "name": _PARENT_BR,
        "description": "parent with branched call step",
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
                "title": "Branched call",
                "directive_template": "call and branch",
                "gates": ["done"],
                "anti_patterns": [],
                "call": _CHILD,
                "branches": [
                    {"next": "p3a", "when_output_contains": "ignored-in-s02"},
                    {"next": "p3b", "when_output_contains": "also-ignored"},
                ],
                "default_branch": "p3b",
            },
            {
                "id": "p3a",
                "title": "Parent branch A",
                "directive_template": "do p3a",
                "gates": ["done"],
                "anti_patterns": [],
            },
            {
                "id": "p3b",
                "title": "Parent branch B (default)",
                "directive_template": "do p3b",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


def _parent_wf_schema() -> dict:
    # Parent call-step with output_schema that requires a {"verdict": ...} JSON object.
    wf = _parent_wf()
    wf["name"] = _PARENT_SCHEMA
    wf["steps"][1]["output_schema"] = {
        "type": "object",
        "required": ["verdict"],
        "properties": {"verdict": {"type": "string"}},
    }
    return wf


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
            {
                "id": "c2",
                "title": "Child step 2",
                "directive_template": "child finish",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


@pytest.fixture(autouse=True)
def _register_wfs():
    WORKFLOWS[_PARENT] = _parent_wf()
    WORKFLOWS[_PARENT_BR] = _parent_wf_branched()
    WORKFLOWS[_PARENT_SCHEMA] = _parent_wf_schema()
    WORKFLOWS[_CHILD] = _child_wf()
    yield
    WORKFLOWS.pop(_PARENT, None)
    WORKFLOWS.pop(_PARENT_BR, None)
    WORKFLOWS.pop(_PARENT_SCHEMA, None)
    WORKFLOWS.pop(_CHILD, None)


def _spawn_child(parent_wf_name: str = _PARENT) -> tuple[str, str]:
    """Start parent, advance past p1, spawn child. Returns (parent_sid, child_sid)."""
    p = call_tool("start_workflow", {"workflow_type": parent_wf_name, "context": ""})
    parent_sid = p["session_id"]
    r = call_tool("submit_step", {"session_id": parent_sid, "step_id": "p1", "content": "p1-done"})
    assert "error" not in r and r.get("code") is None, r
    spawn = call_tool(
        "enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p2"}
    )
    assert "session_id" in spawn, spawn
    return parent_sid, spawn["session_id"]


def _complete_child(child_sid: str, final_content: str = "child-final-artifact") -> dict:
    """Submit c1 then c2 on the child; return the c2 submit response (the bridge response)."""
    r1 = call_tool(
        "submit_step", {"session_id": child_sid, "step_id": "c1", "content": "c1-done"}
    )
    assert r1.get("code") is None, r1
    r2 = call_tool(
        "submit_step", {"session_id": child_sid, "step_id": "c2", "content": final_content}
    )
    return r2


# --- T02 tests --------------------------------------------------------------


def test_child_complete_propagates_artifact_to_parent_step_data():
    parent_sid, child_sid = _spawn_child()
    _complete_child(child_sid, final_content="final-payload")
    parent = state.get_session(parent_sid)
    assert parent["step_data"]["p2"] == "final-payload"


def test_child_auto_deletes_after_successful_propagation():
    _, child_sid = _spawn_child()
    _complete_child(child_sid)
    r = call_tool("get_state", {"session_id": child_sid})
    assert r.get("code") == "session_not_found"


def test_parent_called_session_cleared_after_propagation():
    parent_sid, child_sid = _spawn_child()
    _complete_child(child_sid)
    parent = state.get_session(parent_sid)
    assert parent["called_session"] is None


def test_bridge_response_contains_parent_next_step():
    _, child_sid = _spawn_child()
    r = _complete_child(child_sid)
    assert r.get("next_step", {}).get("id") == "p3"


def test_bridge_response_marked_propagated_from_sub_workflow():
    _, child_sid = _spawn_child()
    r = _complete_child(child_sid)
    assert r.get("propagated_from_sub_workflow") is True


def test_parent_output_schema_fail_escalates_parent():
    parent_sid, child_sid = _spawn_child(parent_wf_name=_PARENT_SCHEMA)
    # Child's final artifact is plain string — will fail output_schema (requires {"verdict"}).
    _complete_child(child_sid, final_content="not-json-object")
    parent = state.get_session(parent_sid)
    assert parent["escalation"] is not None


def test_parent_output_schema_fail_retains_child():
    parent_sid, child_sid = _spawn_child(parent_wf_name=_PARENT_SCHEMA)
    _complete_child(child_sid, final_content="not-json-object")
    child = state.get_session(child_sid)
    assert child["current_step"] == state.COMPLETE


def test_call_step_with_branches_uses_default_branch():
    # parent's call-step has branches + default_branch=p3b; propagation must go to p3b.
    _, child_sid = _spawn_child(parent_wf_name=_PARENT_BR)
    r = _complete_child(child_sid)
    assert r.get("next_step", {}).get("id") == "p3b"


def _setup_retained_child() -> tuple[str, str]:
    """Produce a retained child via output_schema failure. Returns (parent_sid, child_sid)."""
    parent_sid, child_sid = _spawn_child(parent_wf_name=_PARENT_SCHEMA)
    _complete_child(child_sid, final_content="not-json-object")
    # After failure: child retained at COMPLETE, parent escalated, called_session still set.
    # Clear escalation so revise_step can proceed (revise_step doesn't reject on escalation,
    # but we want to verify called_session was still pointing at the retained child).
    return parent_sid, child_sid


def test_revise_call_step_deletes_retained_child():
    parent_sid, child_sid = _setup_retained_child()
    # Sanity: link still present before revise.
    assert state.get_session(parent_sid)["called_session"] == child_sid
    r = call_tool("revise_step", {"session_id": parent_sid, "step_id": "p2"})
    assert r.get("retained_child_deleted") == child_sid
    r2 = call_tool("get_state", {"session_id": child_sid})
    assert r2.get("code") == "session_not_found"


def test_revise_call_step_clears_called_session_link():
    parent_sid, child_sid = _setup_retained_child()
    call_tool("revise_step", {"session_id": parent_sid, "step_id": "p2"})
    parent = state.get_session(parent_sid)
    assert parent["called_session"] is None


def test_revise_call_step_clears_parent_step_data_for_target():
    # Use successful propagation path so parent.step_data[p2] is populated, then revise.
    parent_sid, child_sid = _spawn_child()
    _complete_child(child_sid, final_content="artifact-to-clear")
    assert state.get_session(parent_sid)["step_data"].get("p2") == "artifact-to-clear"
    call_tool("revise_step", {"session_id": parent_sid, "step_id": "p2"})
    parent = state.get_session(parent_sid)
    assert "p2" not in parent["step_data"]


def test_revise_then_enter_sub_workflow_spawns_fresh_child():
    # Happy-path propagation, then revise p2, then re-spawn. Parent not escalated,
    # so enter_sub_workflow accepts. Fresh child must have a different session_id.
    parent_sid, first_child_sid = _spawn_child()
    _complete_child(first_child_sid)
    call_tool("revise_step", {"session_id": parent_sid, "step_id": "p2"})
    r = call_tool(
        "enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p2"}
    )
    assert r.get("session_id") and r["session_id"] != first_child_sid
