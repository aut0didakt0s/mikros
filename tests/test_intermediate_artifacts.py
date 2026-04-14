"""Tests for intermediate artifacts in submit_step."""

import json
import os
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import state
from server.main import WORKFLOWS
from server.schema import validate_workflow
from tests.conftest import call_tool


def _artifact_workflow():
    """Workflow with intermediate artifacts on step 2."""
    return {
        "name": "artifact-test",
        "description": "test intermediate artifacts",
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
                "id": "analyze",
                "title": "Analyze",
                "directive_template": "Run analysis",
                "gates": ["analysis done"],
                "anti_patterns": [],
                "intermediate_artifacts": [
                    {
                        "id": "draft",
                        "description": "Draft analysis",
                        "schema": {"type": "object", "properties": {"notes": {"type": "string"}}, "required": ["notes"]},
                        "checkpoint": True,
                    },
                    {
                        "id": "final",
                        "description": "Final analysis",
                        "schema": {"type": "object", "properties": {"result": {"type": "string"}}, "required": ["result"]},
                    },
                ],
                "output_from": "final",
            },
            {
                "id": "finish",
                "title": "Finish",
                "directive_template": "Wrap up",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


def _load_workflow(wf_dict):
    """Write workflow YAML to temp file, validate, and load into WORKFLOWS."""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(wf_dict, f)
        path = f.name
    errors, doc = validate_workflow(path)
    os.unlink(path)
    assert not errors, f"Workflow validation failed: {errors}"
    WORKFLOWS["artifact-test"] = doc
    return doc


def _start_session():
    """Start a session on artifact-test workflow, return (session_id, first_step_id)."""
    result = call_tool("start_workflow", {"workflow_type": "artifact-test", "context": "test"})
    return result["session_id"], result["current_step"]["id"]


def setup_function():
    state.clear_sessions()
    WORKFLOWS.pop("artifact-test", None)


def teardown_function():
    state.clear_sessions()
    WORKFLOWS.pop("artifact-test", None)


# --- Schema validation tests ---

def test_artifact_validates_against_schema():
    """Artifact submission validates against per-artifact schema."""
    _load_workflow(_artifact_workflow())
    sid, _ = _start_session()
    call_tool("submit_step", {"session_id": sid, "step_id": "gather", "content": "info"})
    # Submit draft with valid content
    valid = json.dumps({"notes": "some notes"})
    result = call_tool("submit_step", {"session_id": sid, "step_id": "analyze", "content": valid, "artifact_id": "draft"})
    assert result["status"] == "artifact_accepted"


def test_artifact_rejects_invalid_schema():
    """Artifact submission with invalid content returns validation_error."""
    _load_workflow(_artifact_workflow())
    sid, _ = _start_session()
    call_tool("submit_step", {"session_id": sid, "step_id": "gather", "content": "info"})
    # Submit draft with missing required field
    invalid = json.dumps({"wrong": "field"})
    result = call_tool("submit_step", {"session_id": sid, "step_id": "analyze", "content": invalid, "artifact_id": "draft"})
    assert result["status"] == "validation_error"
    assert result["artifact_id"] == "draft"
    assert len(result["errors"]) > 0


# --- Checkpoint tests ---

def test_checkpoint_stored_and_retrievable():
    """Checkpointed artifact stored in session and retrievable via get_state."""
    _load_workflow(_artifact_workflow())
    sid, _ = _start_session()
    call_tool("submit_step", {"session_id": sid, "step_id": "gather", "content": "info"})
    valid = json.dumps({"notes": "checkpoint data"})
    call_tool("submit_step", {"session_id": sid, "step_id": "analyze", "content": valid, "artifact_id": "draft"})
    # get_state should include the checkpoint
    st = call_tool("get_state", {"session_id": sid})
    assert "artifact_checkpoints" in st
    assert "draft" in st["artifact_checkpoints"]
    assert json.loads(st["artifact_checkpoints"]["draft"])["notes"] == "checkpoint data"


def test_non_checkpoint_artifact_not_stored():
    """Artifact without checkpoint:true is not stored in checkpoints."""
    wf = _artifact_workflow()
    # 'final' has no checkpoint flag
    _load_workflow(wf)
    sid, _ = _start_session()
    call_tool("submit_step", {"session_id": sid, "step_id": "gather", "content": "info"})
    # Submit draft first (checkpointed)
    call_tool("submit_step", {"session_id": sid, "step_id": "analyze", "content": json.dumps({"notes": "n"}), "artifact_id": "draft"})
    # Submit final (output_from, not checkpointed) - this advances the step
    call_tool("submit_step", {"session_id": sid, "step_id": "analyze", "content": json.dumps({"result": "r"}), "artifact_id": "final"})
    # final should not be in artifact_checkpoints (it's not checkpoint:true)
    artifacts = state.get_artifacts(sid, "analyze")
    assert "final" not in artifacts
    assert "draft" in artifacts


# --- Step completion tests ---

def test_step_not_complete_until_output_from():
    """Step with intermediate_artifacts not complete until output_from submitted."""
    _load_workflow(_artifact_workflow())
    sid, _ = _start_session()
    call_tool("submit_step", {"session_id": sid, "step_id": "gather", "content": "info"})
    # Submit draft (not output_from) - step should NOT advance
    valid = json.dumps({"notes": "some notes"})
    result = call_tool("submit_step", {"session_id": sid, "step_id": "analyze", "content": valid, "artifact_id": "draft"})
    assert result["status"] == "artifact_accepted"
    assert result["current_step"] == "analyze"
    # get_state confirms still on analyze
    st = call_tool("get_state", {"session_id": sid})
    assert st["current_step"]["id"] == "analyze"


def test_output_from_advances_step():
    """Submitting the output_from artifact advances to next step."""
    _load_workflow(_artifact_workflow())
    sid, _ = _start_session()
    call_tool("submit_step", {"session_id": sid, "step_id": "gather", "content": "info"})
    # Submit output_from artifact
    result = call_tool("submit_step", {"session_id": sid, "step_id": "analyze", "content": json.dumps({"result": "done"}), "artifact_id": "final"})
    assert "next_step" in result
    assert result["next_step"]["id"] == "finish"


def test_output_from_with_failed_validation_rejected():
    """output_from artifact with invalid schema is rejected."""
    _load_workflow(_artifact_workflow())
    sid, _ = _start_session()
    call_tool("submit_step", {"session_id": sid, "step_id": "gather", "content": "info"})
    invalid = json.dumps({"wrong": "field"})
    result = call_tool("submit_step", {"session_id": sid, "step_id": "analyze", "content": invalid, "artifact_id": "final"})
    assert result["status"] == "validation_error"
    # Step should NOT advance
    st = call_tool("get_state", {"session_id": sid})
    assert st["current_step"]["id"] == "analyze"


# --- Missing artifact_id error ---

def test_missing_artifact_id_error():
    """Submitting without artifact_id when step has intermediate_artifacts returns error."""
    _load_workflow(_artifact_workflow())
    sid, _ = _start_session()
    call_tool("submit_step", {"session_id": sid, "step_id": "gather", "content": "info"})
    result = call_tool("submit_step", {"session_id": sid, "step_id": "analyze", "content": "anything"})
    assert "error" in result
    assert "expected_artifacts" in result
    assert set(result["expected_artifacts"]) == {"draft", "final"}


# --- Backward compat ---

def test_backward_compat_no_intermediate_artifacts():
    """Steps without intermediate_artifacts work exactly as before."""
    _load_workflow(_artifact_workflow())
    sid, _ = _start_session()
    # gather has no intermediate_artifacts
    result = call_tool("submit_step", {"session_id": sid, "step_id": "gather", "content": "info"})
    assert "next_step" in result
    assert result["next_step"]["id"] == "analyze"


# --- Load-time validation ---

def test_schema_validates_output_from_reference():
    """output_from must reference an existing intermediate artifact ID."""
    wf = _artifact_workflow()
    wf["steps"][1]["output_from"] = "nonexistent"
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(wf, f)
        path = f.name
    errors, _ = validate_workflow(path)
    os.unlink(path)
    assert any("nonexistent" in e and "output_from" in e for e in errors)


def test_schema_validates_output_from_without_artifacts():
    """output_from without intermediate_artifacts is an error."""
    wf = _artifact_workflow()
    del wf["steps"][1]["intermediate_artifacts"]
    # Keep output_from
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(wf, f)
        path = f.name
    errors, _ = validate_workflow(path)
    os.unlink(path)
    assert any("output_from" in e and "without" in e for e in errors)
