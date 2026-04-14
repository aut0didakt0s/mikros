"""Automated server-side verification for M005 demo workflows."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import state
from server.main import WORKFLOWS
from server.schema import validate_workflow
from tests.conftest import call_tool


WORKFLOWS_DIR = os.path.join(os.path.dirname(__file__), "..", "server", "workflows")


def setup_function():
    state.clear_sessions()


def teardown_function():
    state.clear_sessions()


# --- Load / validation ---

def test_all_three_demos_load_and_validate():
    for name in ("demo_branching", "demo_artifacts", "demo_guardrails"):
        errors, doc = validate_workflow(os.path.join(WORKFLOWS_DIR, f"{name}.yaml"))
        assert errors == [], f"{name}: {errors}"
        assert doc["name"] == name
        assert name in WORKFLOWS


# --- demo_branching ---

def _start(workflow_type):
    r = call_tool("start_workflow", {"workflow_type": workflow_type, "context": "test"})
    return r["session_id"]


def test_branching_each_valid_branch_accepted():
    for branch in ("beginner_track", "intermediate_track", "advanced_track"):
        sid = _start("demo_branching")
        r = call_tool("submit_step", {
            "session_id": sid, "step_id": "assess_expertise",
            "content": "assessed", "branch": branch,
        })
        assert "error" not in r
        assert r["next_step"]["id"] == branch


def test_branching_invalid_branch_rejected_with_options():
    sid = _start("demo_branching")
    r = call_tool("submit_step", {
        "session_id": sid, "step_id": "assess_expertise",
        "content": "bad", "branch": "guru_track",
    })
    assert "error" in r
    assert "guru_track" in r["error"]
    for opt in ("beginner_track", "intermediate_track", "advanced_track"):
        assert opt in r["error"]


def test_branching_default_branch_fallback():
    sid = _start("demo_branching")
    r = call_tool("submit_step", {
        "session_id": sid, "step_id": "assess_expertise", "content": "no branch",
    })
    assert r["next_step"]["id"] == "intermediate_track"


# --- demo_artifacts ---

def test_artifacts_outline_standalone_no_advance():
    sid = _start("demo_artifacts")
    outline = json.dumps({"title": "My Report", "sections": ["a", "b", "c"]})
    r = call_tool("submit_step", {
        "session_id": sid, "step_id": "draft_report",
        "content": outline, "artifact_id": "outline",
    })
    assert r["status"] == "artifact_accepted"
    assert r["current_step"] == "draft_report"


def test_artifacts_final_draft_bad_content_rejected():
    sid = _start("demo_artifacts")
    short = json.dumps({"body": "too short"})
    r = call_tool("submit_step", {
        "session_id": sid, "step_id": "draft_report",
        "content": short, "artifact_id": "final_draft",
    })
    assert r["status"] == "validation_error"
    st = call_tool("get_state", {"session_id": sid})
    assert st["current_step"]["id"] == "draft_report"


def test_artifacts_final_draft_valid_advances_step():
    sid = _start("demo_artifacts")
    body = "x" * 250
    r = call_tool("submit_step", {
        "session_id": sid, "step_id": "draft_report",
        "content": json.dumps({"body": body}), "artifact_id": "final_draft",
    })
    assert "next_step" in r
    assert r["next_step"]["id"] == "publish"


def test_artifacts_outline_checkpoint_retrievable():
    sid = _start("demo_artifacts")
    outline = json.dumps({"title": "Great Report", "sections": ["one", "two", "three"]})
    call_tool("submit_step", {
        "session_id": sid, "step_id": "draft_report",
        "content": outline, "artifact_id": "outline",
    })
    st = call_tool("get_state", {"session_id": sid})
    assert "outline" in st["artifact_checkpoints"]
    stored = json.loads(st["artifact_checkpoints"]["outline"])
    assert stored["title"] == "Great Report"
    assert len(stored["sections"]) == 3


# --- demo_guardrails ---

def test_guardrails_crisis_keyword_forces_branch():
    sid = _start("demo_guardrails")
    r = call_tool("submit_step", {
        "session_id": sid, "step_id": "intake",
        "content": "I want to hurt myself",
    })
    assert "error" not in r
    assert r["next_step"]["id"] == "crisis_support"


def test_guardrails_revisit_escalates_and_blocks_further():
    sid = _start("demo_guardrails")
    # intake visit=1 at start. Branch back to intake twice to push visits to 2, then 3.
    call_tool("submit_step", {
        "session_id": sid, "step_id": "intake",
        "content": "again one", "branch": "intake",
    })
    call_tool("submit_step", {
        "session_id": sid, "step_id": "intake",
        "content": "again two", "branch": "intake",
    })
    # visit count is 3 -> escalate on next submit
    r = call_tool("submit_step", {
        "session_id": sid, "step_id": "intake",
        "content": "again three", "branch": "intake",
    })
    assert "error" in r
    assert "escalat" in r["error"].lower()
    # Further submissions rejected
    r2 = call_tool("submit_step", {
        "session_id": sid, "step_id": "intake", "content": "blocked",
    })
    assert "error" in r2
    assert "escalat" in r2["error"].lower()
