"""Tests for sub-workflow contract paths 1-7.

Each test drives one parent+child YAML pair end-to-end through the live MCP
tool surface (start_workflow → submit_step → enter_sub_workflow → submit_step
on child → bridge response on propagation → generate_artifact or get_state).

Pairs covered here:
  1. artifact inlining (markdown heading hierarchy preserved in parent artifact)
  2. call_context_from (parent subtree extracted + seeded as child `context`)
  3. output_schema pass (child JSON matches parent schema; parent advances)
  4. output_schema fail (child misses required field; parent escalates + retains)
  5. revise-the-call clean rerun (revise clears step_data, unlinks child, fresh spawn)
  6. cascade wrap (child cascade → parent called_workflow_error wrapper + retained)
  7. branches + precondition compose (call-step reachable via branches + gate)
"""

import json
import os

from megalos_server import state
from megalos_server.main import WORKFLOWS
from megalos_server.schema import load_workflow
from tests.conftest import call_tool

_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "workflows")


def _load_pair(parent_name: str, child_name: str) -> tuple[str, str]:
    """Load a parent+child YAML pair into WORKFLOWS; return (parent_key, child_key)."""
    parent_doc = load_workflow(os.path.join(_FIXTURE_DIR, f"{parent_name}.yaml"))
    child_doc = load_workflow(os.path.join(_FIXTURE_DIR, f"{child_name}.yaml"))
    WORKFLOWS[parent_name] = parent_doc
    WORKFLOWS[child_name] = child_doc
    return parent_name, child_name


def _teardown_pair(parent_name: str, child_name: str) -> None:
    state.clear_sessions()
    WORKFLOWS.pop(parent_name, None)
    WORKFLOWS.pop(child_name, None)


def _drive(session_id: str, submissions: list[tuple[str, str]]) -> dict:
    """Submit each (step_id, content) in order; return the last response."""
    last: dict = {}
    for step_id, content in submissions:
        last = call_tool(
            "submit_step",
            {"session_id": session_id, "step_id": step_id, "content": content},
        )
    return last


# --- Pair 1: artifact inlining ----------------------------------------------


def test_artifact_inlining_preserves_markdown_hierarchy():
    parent_key, child_key = _load_pair(
        "artifact_inlining_parent",
        "artifact_inlining_child",
    )
    try:
        r = call_tool("start_workflow", {"workflow_type": parent_key, "context": ""})
        parent_sid = r["session_id"]

        _drive(parent_sid, [("intro", "# Intro\n\nA brief introduction paragraph.")])

        spawn = call_tool(
            "enter_sub_workflow",
            {"parent_session_id": parent_sid, "call_step_id": "research"},
        )
        child_sid = spawn["session_id"]

        child_final = "# Research Brief\n\n## Findings\n\nThree key findings emerged."
        _drive(child_sid, [("gather", "sources-list"), ("brief", child_final)])

        # Child's final artifact lives on parent.step_data[research] verbatim.
        parent = state.get_session(parent_sid)
        assert parent["step_data"]["research"] == child_final

        # generate_artifact must preserve the child's heading hierarchy (no flattening).
        r = call_tool("generate_artifact", {"session_id": parent_sid, "output_format": "text"})
        assert "## Findings" in r["artifact"]
        assert "# Research Brief" in r["artifact"]
    finally:
        _teardown_pair(parent_key, child_key)


# --- Pair 2: call_context_from ----------------------------------------------


def test_call_context_from_seeds_child_context():
    parent_key, child_key = _load_pair(
        "call_context_from_parent",
        "call_context_from_child",
    )
    try:
        r = call_tool("start_workflow", {"workflow_type": parent_key, "context": ""})
        parent_sid = r["session_id"]

        authored_topic = "renewable energy"
        payload = json.dumps({"topic": authored_topic, "audience": "policy-makers"})
        _drive(parent_sid, [("s1", payload)])

        spawn = call_tool(
            "enter_sub_workflow",
            {"parent_session_id": parent_sid, "call_step_id": "s2"},
        )

        # Subtree extraction injected just the topic into the child's context.
        assert spawn["context"] == authored_topic
    finally:
        _teardown_pair(parent_key, child_key)


# --- Pair 3: output_schema pass ---------------------------------------------


def test_output_schema_pass_advances_parent():
    parent_key, child_key = _load_pair(
        "output_schema_pass_parent",
        "output_schema_pass_child",
    )
    try:
        r = call_tool("start_workflow", {"workflow_type": parent_key, "context": ""})
        parent_sid = r["session_id"]

        _drive(parent_sid, [("p1", "intro done")])

        spawn = call_tool(
            "enter_sub_workflow",
            {"parent_session_id": parent_sid, "call_step_id": "p2"},
        )
        child_sid = spawn["session_id"]

        verdict_payload = json.dumps({"verdict": "approved"})
        bridge = _drive(child_sid, [("c1", "investigating"), ("c2", verdict_payload)])

        # No escalation; bridge routes parent to its next step (p3).
        assert bridge.get("code") is None, bridge
        assert bridge.get("next_step", {}).get("id") == "p3"

        parent = state.get_session(parent_sid)
        assert parent["escalation"] is None
        # Propagated artifact matches the parent's output_schema.
        assert json.loads(parent["step_data"]["p2"]) == {"verdict": "approved"}
    finally:
        _teardown_pair(parent_key, child_key)


# --- Pair 4: output_schema fail ---------------------------------------------


def test_output_schema_fail_escalates_and_retains_child():
    parent_key, child_key = _load_pair(
        "output_schema_fail_parent",
        "output_schema_fail_child",
    )
    try:
        r = call_tool("start_workflow", {"workflow_type": parent_key, "context": ""})
        parent_sid = r["session_id"]

        _drive(parent_sid, [("p1", "intro done")])

        spawn = call_tool(
            "enter_sub_workflow",
            {"parent_session_id": parent_sid, "call_step_id": "p2"},
        )
        child_sid = spawn["session_id"]

        # Child emits freeform content — missing required `approval` field.
        bridge = _drive(child_sid, [("c1", "investigating"), ("c2", "freeform prose")])

        # Parent escalated on propagation.
        assert bridge.get("code") == "session_escalated"
        # Failure wrapper carries reason=parent_output_schema_fail.
        assert (
            bridge["called_workflow_error"]["child_error"]["reason"]
            == "parent_output_schema_fail"
        )

        parent = state.get_session(parent_sid)
        assert parent["escalation"] is not None

        # Child retained: resolvable via get_state.
        child_state = call_tool("get_state", {"session_id": child_sid})
        assert child_state.get("code") is None
        assert child_state["session_id"] == child_sid
    finally:
        _teardown_pair(parent_key, child_key)


# --- Pair 5: revise-the-call clean rerun ------------------------------------


def test_revise_call_step_unlinks_child_and_respawns_fresh():
    parent_key, child_key = _load_pair(
        "revise_clean_rerun_parent",
        "revise_clean_rerun_child",
    )
    try:
        r = call_tool("start_workflow", {"workflow_type": parent_key, "context": ""})
        parent_sid = r["session_id"]

        _drive(parent_sid, [("p1", "intro done")])

        spawn = call_tool(
            "enter_sub_workflow",
            {"parent_session_id": parent_sid, "call_step_id": "p2"},
        )
        first_child_sid = spawn["session_id"]

        # Drive child to completion; bridge advances parent past the call-step.
        bridge = _drive(
            first_child_sid,
            [("step_1", "first chunk"), ("step_2", "final child artifact")],
        )
        assert bridge.get("code") is None, bridge
        assert bridge.get("next_step", {}).get("id") == "p3"

        parent = state.get_session(parent_sid)
        assert parent["step_data"]["p2"] == "final child artifact"
        assert parent.get("called_session") is None
        # Child auto-deleted after successful propagation.
        r = call_tool("get_state", {"session_id": first_child_sid})
        assert r.get("code") == "session_not_found"

        # Revise the call-step: clears step_data[p2], resets current_step to p2.
        rv = call_tool(
            "revise_step", {"session_id": parent_sid, "step_id": "p2"}
        )
        assert rv.get("code") is None, rv
        parent = state.get_session(parent_sid)
        assert "p2" not in parent["step_data"]
        assert parent["current_step"] == "p2"
        assert parent.get("called_session") is None

        # Re-enter: fresh child session, starting at step_1.
        spawn2 = call_tool(
            "enter_sub_workflow",
            {"parent_session_id": parent_sid, "call_step_id": "p2"},
        )
        second_child_sid = spawn2["session_id"]
        assert second_child_sid != first_child_sid
        second_child = state.get_session(second_child_sid)
        assert second_child["current_step"] == "step_1"
        assert second_child["step_data"] == {}
    finally:
        _teardown_pair(parent_key, child_key)


# --- Pair 6: cascade wrap ---------------------------------------------------


def test_child_cascade_wraps_parent_and_retains_child():
    parent_key, child_key = _load_pair(
        "cascade_wrap_parent",
        "cascade_wrap_child",
    )
    try:
        r = call_tool("start_workflow", {"workflow_type": parent_key, "context": ""})
        parent_sid = r["session_id"]

        _drive(parent_sid, [("p1", "intro done")])

        spawn = call_tool(
            "enter_sub_workflow",
            {"parent_session_id": parent_sid, "call_step_id": "p2"},
        )
        child_sid = spawn["session_id"]

        # flag=no → child step_2 skipped → child step_3 cascade-references step_2.
        bridge = call_tool(
            "submit_step",
            {"session_id": child_sid, "step_id": "step_1", "content": '{"flag": "no"}'},
        )

        # Parent escalated in the same response.
        assert bridge.get("code") == "session_escalated"
        # Canonical cascade marker from _SkippedPredecessor.
        assert (
            bridge["called_workflow_error"]["child_error"]["code"]
            == "skipped_predecessor_reference"
        )
        # Three-field wrapper contract.
        assert set(bridge["called_workflow_error"].keys()) == {
            "child_session_id",
            "child_workflow_type",
            "child_error",
        }
        assert bridge["called_workflow_error"]["child_session_id"] == child_sid
        assert bridge["called_workflow_error"]["child_workflow_type"] == child_key

        parent = state.get_session(parent_sid)
        assert parent["escalation"] is not None
        assert parent["escalation"]["guardrail_id"] == "called_workflow_failed"

        # Child retained: still resolvable via get_state.
        child_state = call_tool("get_state", {"session_id": child_sid})
        assert child_state.get("code") is None
        assert child_state["session_id"] == child_sid
    finally:
        _teardown_pair(parent_key, child_key)


# --- Pair 7: branches + precondition compose --------------------------------


def test_branches_precondition_compose_spawns_child_on_happy_path():
    parent_key, child_key = _load_pair(
        "branches_precondition_compose_parent",
        "branches_precondition_compose_child",
    )
    try:
        r = call_tool("start_workflow", {"workflow_type": parent_key, "context": ""})
        parent_sid = r["session_id"]

        # step_1: pick path=A AND go=yes so branches route to p_call AND
        # p_call's precondition is satisfied.
        payload = json.dumps({"path": "A", "go": "yes"})
        last = _drive(parent_sid, [("step_1", payload)])
        # step_2 is a router; submit with explicit branch selection.
        last = call_tool(
            "submit_step",
            {
                "session_id": parent_sid,
                "step_id": "step_2",
                "content": "routing to A",
                "branch": "p_call",
            },
        )
        assert last.get("code") is None, last
        assert last.get("next_step", {}).get("id") == "p_call"

        # Enter sub-workflow at p_call: composes with upstream branches +
        # on-call precondition cleanly.
        spawn = call_tool(
            "enter_sub_workflow",
            {"parent_session_id": parent_sid, "call_step_id": "p_call"},
        )
        child_sid = spawn["session_id"]
        assert state.get_session(child_sid)["current_step"] == "c1"

        # Drive child to completion → parent advances to p_end.
        bridge = _drive(child_sid, [("c1", "child done")])
        assert bridge.get("code") is None, bridge
        assert bridge.get("next_step", {}).get("id") == "p_end"

        parent = state.get_session(parent_sid)
        assert parent["step_data"]["p_call"] == "child done"
    finally:
        _teardown_pair(parent_key, child_key)
