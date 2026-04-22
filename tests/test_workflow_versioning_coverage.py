"""Three explicit coverage cases for ADR-001 workflow_changed funnel.

Beyond the seven-scenario matrix, three specific failure modes need
explicit pinning — they each correspond to a subtle silent-degradation
the discovery report flagged:

(a) Session with checkpointed intermediate_artifacts + post-start YAML
    mutation — must return workflow_changed rather than stale
    artifact_checkpoints.
(b) Session at __complete__ whose workflow was then mutated —
    generate_artifact must return workflow_changed rather than a
    well-shaped empty artifact.
(c) Call-frame parent whose child-workflow YAML was mutated between
    parent-start and child-spawn — enter_sub_workflow returns
    workflow_changed naming the *child* workflow_type; parent and child
    fingerprints are independent.
"""

from __future__ import annotations

import copy
import hashlib
import json

import pytest  # type: ignore[import-not-found]

from megalos_server import state
from megalos_server.main import WORKFLOWS, mcp
from tests.conftest import call_tool


def _fingerprints() -> dict[str, str]:
    return mcp._megalos_workflow_fingerprints  # type: ignore[attr-defined]


def _install(name: str, wf: dict) -> None:
    WORKFLOWS[name] = wf
    canonical = json.dumps(wf, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _fingerprints()[name] = hashlib.sha256(canonical).hexdigest()


def _uninstall(name: str) -> None:
    WORKFLOWS.pop(name, None)
    _fingerprints().pop(name, None)


# --- (a) intermediate_artifacts + post-start mutation --------------------


def _wf_with_artifacts() -> dict:
    return {
        "name": "cov-artifacts",
        "description": "workflow with intermediate_artifacts",
        "category": "test",
        "output_format": "text",
        "steps": [
            {
                "id": "gather",
                "title": "Gather",
                "directive_template": "gather data",
                "gates": ["done"],
                "anti_patterns": [],
                "intermediate_artifacts": [
                    {"id": "notes", "title": "Notes", "description": "field notes"},
                ],
            },
            {
                "id": "finish",
                "title": "Finish",
                "directive_template": "wrap",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


def test_checkpointed_artifacts_plus_mutation_returns_workflow_changed():
    name = "cov-artifacts"
    _install(name, _wf_with_artifacts())
    try:
        r = call_tool("start_workflow", {"workflow_type": name, "context": ""})
        sid = r["session_id"]

        # Stamp an intermediate artifact — the surface that would otherwise
        # silently come back stale.
        state.store_artifact(sid, "gather", "notes", "partial draft")

        # Mutate the workflow — the artifact_checkpoints path should not
        # be reachable anymore, workflow_changed wins first.
        v2 = copy.deepcopy(_wf_with_artifacts())
        v2["steps"][0]["directive_template"] = "edited directive"
        _install(name, v2)

        resp = call_tool("get_state", {"session_id": sid})
        assert resp["code"] == "workflow_changed"
        # Crucially, the stale artifact surface is absent.
        assert "artifact_checkpoints" not in resp
    finally:
        _uninstall(name)


# --- (b) completed session + post-mutation generate_artifact --------------


def _wf_two_step() -> dict:
    return {
        "name": "cov-complete",
        "description": "two-step workflow",
        "category": "test",
        "output_format": "text",
        "steps": [
            {
                "id": "first",
                "title": "First",
                "directive_template": "d1",
                "gates": ["done"],
                "anti_patterns": [],
            },
            {
                "id": "last",
                "title": "Last",
                "directive_template": "d2",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


def test_completed_session_then_mutation_blocks_generate_artifact():
    name = "cov-complete"
    _install(name, _wf_two_step())
    try:
        r = call_tool("start_workflow", {"workflow_type": name, "context": ""})
        sid = r["session_id"]
        call_tool("submit_step", {"session_id": sid, "step_id": "first", "content": "a"})
        call_tool("submit_step", {"session_id": sid, "step_id": "last", "content": "b"})
        assert state.get_session(sid)["current_step"] == state.COMPLETE

        # Rename the final step — V1 final was "last", V2 final is "last_renamed".
        # Pre-T02 this produced a well-shaped empty artifact from generate_artifact.
        v2 = copy.deepcopy(_wf_two_step())
        v2["steps"][-1]["id"] = "last_renamed"
        _install(name, v2)

        resp = call_tool("generate_artifact", {"session_id": sid})
        assert resp["code"] == "workflow_changed"
        # No empty-string artifact snuck through.
        assert "artifact" not in resp
    finally:
        _uninstall(name)


# --- (c) child-spawn-timing: parent vs child fingerprints are independent -


_PARENT = "cov-parent"
_CHILD = "cov-child"


def _parent_wf() -> dict:
    return {
        "name": _PARENT,
        "description": "parent with call step",
        "category": "test",
        "output_format": "text",
        "steps": [
            {
                "id": "call1",
                "title": "Call child",
                "directive_template": "hand off",
                "gates": ["done"],
                "anti_patterns": [],
                "call": _CHILD,
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
                "title": "C1",
                "directive_template": "do c1",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


@pytest.fixture(autouse=True)
def _install_parent_child():
    _install(_PARENT, _parent_wf())
    _install(_CHILD, _child_wf())
    yield
    _uninstall(_PARENT)
    _uninstall(_CHILD)


def test_edit_child_only_surfaces_at_child_side():
    """Edit only the child workflow before enter_sub_workflow. The child is
    spawned against V2 child fingerprint (child-spawn-timing property),
    so tools on the child see no mismatch — but the parent's fingerprint
    is untouched, so parent-side tools succeed too. This pins the
    independence property.
    """
    # Mutate the child BEFORE start_workflow — the parent's fingerprint in
    # the parent session would be stamped at parent-start, then
    # enter_sub_workflow stamps the CURRENT child fingerprint onto the
    # child session. No mismatch on either side.
    r = call_tool("start_workflow", {"workflow_type": _PARENT, "context": ""})
    parent_sid = r["session_id"]

    # Now edit the child BEFORE spawning.
    v2_child = copy.deepcopy(_child_wf())
    v2_child["steps"][0]["directive_template"] = "do c1 (edited pre-spawn)"
    _install(_CHILD, v2_child)

    r2 = call_tool(
        "enter_sub_workflow",
        {"parent_session_id": parent_sid, "call_step_id": "call1"},
    )
    # The child session exists and is healthy — child stamp was captured at
    # spawn time against V2.
    assert r2.get("status") != "error", f"unexpected error at spawn: {r2}"
    child_sid = r2["session_id"]

    # Parent-side call succeeds (parent fingerprint unchanged).
    state_resp = call_tool("get_state", {"session_id": parent_sid})
    assert state_resp.get("status") != "error"

    # Child-side call succeeds too.
    child_state = call_tool("get_state", {"session_id": child_sid})
    assert child_state.get("status") != "error"


def test_edit_child_after_parent_start_but_before_spawn_surfaces_at_child_spawn():
    """Edit the child AFTER the parent has started but BEFORE
    enter_sub_workflow. The child session is stamped with V2 at spawn —
    so no *child-side* mismatch at the moment of spawn. BUT if we then
    mutate V2→V3 before the next child tool call, the child-side
    mismatch surfaces naming the child workflow_type.
    """
    r = call_tool("start_workflow", {"workflow_type": _PARENT, "context": ""})
    parent_sid = r["session_id"]

    # Edit child to V2 between parent start and child spawn.
    v2_child = copy.deepcopy(_child_wf())
    v2_child["steps"][0]["directive_template"] = "V2"
    _install(_CHILD, v2_child)

    r2 = call_tool(
        "enter_sub_workflow",
        {"parent_session_id": parent_sid, "call_step_id": "call1"},
    )
    child_sid = r2["session_id"]

    # Edit child again — now V3 vs the child session's stamped V2.
    v3_child = copy.deepcopy(v2_child)
    v3_child["steps"][0]["directive_template"] = "V3"
    _install(_CHILD, v3_child)

    child_fp = state.get_session(child_sid)["fingerprint"]
    resp = call_tool("get_state", {"session_id": child_sid})
    assert resp["code"] == "workflow_changed"
    assert resp["workflow_type"] == _CHILD
    assert resp["session_fingerprint"] == child_fp


def test_edit_parent_only_surfaces_at_parent_side():
    """Parent mutated; child (not yet spawned) untouched. The parent-side
    resolver trips on the parent session; enter_sub_workflow against the
    parent also trips on the parent's own fingerprint before it would
    spawn the child. Child session is never created.
    """
    r = call_tool("start_workflow", {"workflow_type": _PARENT, "context": ""})
    parent_sid = r["session_id"]

    # Mutate only the parent.
    v2_parent = copy.deepcopy(_parent_wf())
    v2_parent["steps"][0]["directive_template"] = "edited parent directive"
    _install(_PARENT, v2_parent)

    resp = call_tool(
        "enter_sub_workflow",
        {"parent_session_id": parent_sid, "call_step_id": "call1"},
    )
    assert resp["code"] == "workflow_changed"
    assert resp["workflow_type"] == _PARENT
