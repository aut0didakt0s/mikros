"""Tests for adaptive branching in the state machine."""

import os
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import state
from server.main import WORKFLOWS
from server.schema import validate_workflow
from tests.conftest import call_tool


def _branching_workflow():
    """A workflow with branching at step 2."""
    return {
        "name": "branch-test",
        "description": "test workflow with branches",
        "category": "testing",
        "output_format": "text",
        "steps": [
            {
                "id": "gather",
                "title": "Gather Info",
                "directive_template": "Gather info",
                "gates": ["info gathered"],
                "anti_patterns": [],
            },
            {
                "id": "decide",
                "title": "Decide Path",
                "directive_template": "Pick a path",
                "gates": ["decision made"],
                "anti_patterns": [],
                "branches": [
                    {"next": "path_a", "condition": "user wants A"},
                    {"next": "path_b", "condition": "user wants B"},
                ],
                "default_branch": "path_a",
            },
            {
                "id": "path_a",
                "title": "Path A",
                "directive_template": "Do path A",
                "gates": ["A done"],
                "anti_patterns": [],
            },
            {
                "id": "path_b",
                "title": "Path B",
                "directive_template": "Do path B",
                "gates": ["B done"],
                "anti_patterns": [],
            },
        ],
    }


def _write_workflow_yaml(data):
    """Write workflow dict to a temp YAML file, return path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(data, f)
    f.close()
    return f.name


def _linear_workflow():
    """Simple linear workflow for backward compat testing."""
    return {
        "name": "linear-test",
        "description": "linear workflow",
        "category": "testing",
        "output_format": "text",
        "steps": [
            {
                "id": "step1",
                "title": "Step 1",
                "directive_template": "Do step 1",
                "gates": ["done"],
                "anti_patterns": [],
            },
            {
                "id": "step2",
                "title": "Step 2",
                "directive_template": "Do step 2",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


def setup_function():
    state.clear_sessions()


def teardown_function():
    state.clear_sessions()
    WORKFLOWS.pop("branch-test", None)
    WORKFLOWS.pop("linear-test", None)


# --- Schema validation tests ---


def test_schema_valid_branches():
    """Valid branches and default_branch pass validation."""
    path = _write_workflow_yaml(_branching_workflow())
    errors, doc = validate_workflow(path)
    os.unlink(path)
    assert errors == []
    assert doc is not None


def test_schema_branch_references_nonexistent_step():
    """branches[].next referencing nonexistent step produces error."""
    wf = _branching_workflow()
    wf["steps"][1]["branches"][0]["next"] = "nonexistent"
    path = _write_workflow_yaml(wf)
    errors, _ = validate_workflow(path)
    os.unlink(path)
    assert any("nonexistent" in e for e in errors)


def test_schema_default_branch_references_nonexistent_step():
    """default_branch referencing nonexistent step produces error."""
    wf = _branching_workflow()
    wf["steps"][1]["default_branch"] = "ghost"
    path = _write_workflow_yaml(wf)
    errors, _ = validate_workflow(path)
    os.unlink(path)
    assert any("ghost" in e for e in errors)


def test_load_time_validation_of_branch_references():
    """Workflow YAML with bad branch references fails at load time."""
    wf = _branching_workflow()
    wf["steps"][1]["branches"].append({"next": "does_not_exist", "condition": "impossible"})
    path = _write_workflow_yaml(wf)
    errors, _ = validate_workflow(path)
    os.unlink(path)
    assert len(errors) > 0
    assert any("does_not_exist" in e for e in errors)


# --- Tool-level branching tests ---


def test_valid_branch_accepted():
    """submit_step with valid branch navigates to correct step."""
    WORKFLOWS["branch-test"] = _branching_workflow()

    r = call_tool("start_workflow", {"workflow_type": "branch-test", "context": "test"})
    sid = r["session_id"]

    r = call_tool("submit_step", {"session_id": sid, "step_id": "gather", "content": "info"})
    assert r["next_step"]["id"] == "decide"

    r = call_tool("submit_step", {"session_id": sid, "step_id": "decide", "content": "chose B", "branch": "path_b"})
    assert r["next_step"]["id"] == "path_b"


def test_invalid_branch_rejected_with_options():
    """submit_step with invalid branch returns error listing valid options."""
    WORKFLOWS["branch-test"] = _branching_workflow()

    r = call_tool("start_workflow", {"workflow_type": "branch-test", "context": "test"})
    sid = r["session_id"]
    call_tool("submit_step", {"session_id": sid, "step_id": "gather", "content": "info"})

    r = call_tool("submit_step", {"session_id": sid, "step_id": "decide", "content": "bad", "branch": "path_c"})
    assert "error" in r
    assert "path_c" in r["error"]
    assert "path_a" in r["error"]
    assert "path_b" in r["error"]


def test_default_branch_fallback():
    """submit_step without branch param uses default_branch."""
    WORKFLOWS["branch-test"] = _branching_workflow()

    r = call_tool("start_workflow", {"workflow_type": "branch-test", "context": "test"})
    sid = r["session_id"]
    call_tool("submit_step", {"session_id": sid, "step_id": "gather", "content": "info"})

    # No branch param -- should fall back to default_branch (path_a)
    r = call_tool("submit_step", {"session_id": sid, "step_id": "decide", "content": "default"})
    assert r["next_step"]["id"] == "path_a"


def test_linear_steps_unchanged():
    """Linear workflow (no branches) still works with steps[idx+1]."""
    WORKFLOWS["linear-test"] = _linear_workflow()

    r = call_tool("start_workflow", {"workflow_type": "linear-test", "context": "test"})
    sid = r["session_id"]

    r = call_tool("submit_step", {"session_id": sid, "step_id": "step1", "content": "done"})
    assert r["next_step"]["id"] == "step2"

    r = call_tool("submit_step", {"session_id": sid, "step_id": "step2", "content": "done"})
    assert r["status"] == "workflow_complete"


def test_step_visit_counts():
    """step_visit_counts incremented on each step entry."""
    WORKFLOWS["branch-test"] = _branching_workflow()

    r = call_tool("start_workflow", {"workflow_type": "branch-test", "context": "test"})
    sid = r["session_id"]

    session = state.get_session(sid)
    # First step visited once at start
    assert session["step_visit_counts"]["gather"] == 1

    call_tool("submit_step", {"session_id": sid, "step_id": "gather", "content": "info"})
    # After submitting gather, "decide" should have visit count 1
    assert session["step_visit_counts"]["decide"] == 1
