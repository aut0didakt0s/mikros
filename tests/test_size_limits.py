"""Tests for CONTENT_MAX, ARTIFACT_MAX, YAML_MAX size-limit enforcement."""

from pathlib import Path

from megalos_server import state
from megalos_server.errors import ARTIFACT_MAX, CONTENT_MAX, YAML_MAX
from megalos_server.main import WORKFLOWS
from megalos_server.schema import validate_workflow
from tests.conftest import call_tool


def _two_step_workflow():
    """Minimal in-fixture workflow used to drive size-limit tests."""
    return {
        "name": "size-limit-test",
        "description": "size limit test workflow",
        "category": "testing",
        "output_format": "text",
        "steps": [
            {
                "id": "first",
                "title": "First",
                "directive_template": "First step",
                "gates": ["done"],
                "anti_patterns": [],
            },
            {
                "id": "second",
                "title": "Second",
                "directive_template": "Second step",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


def setup_function():
    state.clear_sessions()


def teardown_function():
    state.clear_sessions()
    WORKFLOWS.pop("size-limit-test", None)


def test_submit_step_rejects_oversize_content():
    """submit_step with content > CONTENT_MAX returns oversize_payload."""
    WORKFLOWS["size-limit-test"] = _two_step_workflow()
    r = call_tool("start_workflow", {"workflow_type": "size-limit-test", "context": "x"})
    sid = r["session_id"]

    big = "a" * 300_000  # > CONTENT_MAX (262_144)
    r = call_tool("submit_step", {"session_id": sid, "step_id": "first", "content": big})
    assert r["status"] == "error"
    assert r["code"] == "oversize_payload"
    assert r["field"] == "content"
    assert r["max_bytes"] == CONTENT_MAX
    assert r["actual_bytes"] == 300_000
    assert r["actual_bytes"] > CONTENT_MAX


def test_generate_artifact_rejects_oversize_artifact():
    """generate_artifact with combined step_data > ARTIFACT_MAX returns oversize_payload."""
    WORKFLOWS["size-limit-test"] = _two_step_workflow()
    r = call_tool("start_workflow", {"workflow_type": "size-limit-test", "context": "x"})
    sid = r["session_id"]

    # Each step content is just under CONTENT_MAX; sum of 5 fills > ARTIFACT_MAX (1_048_576).
    # Two steps × ~250_000 bytes is only ~500KB, so we stash large content directly via state.
    big_chunk = "z" * 600_000
    new_step_data = {"first": big_chunk, "second": big_chunk}
    state.update_session(sid, step_data=new_step_data, current_step=state.COMPLETE)

    r = call_tool("generate_artifact", {"session_id": sid, "output_format": "text"})
    assert r["status"] == "error"
    assert r["code"] == "oversize_payload"
    assert r["field"] == "artifact"
    assert r["max_bytes"] == ARTIFACT_MAX
    assert r["actual_bytes"] > ARTIFACT_MAX


def test_load_workflow_rejects_oversize_yaml(tmp_path: Path):
    """validate_workflow on a > YAML_MAX file raises an error naming the file and cap."""
    big_yaml = tmp_path / "huge.yaml"
    # 600_000 bytes of valid-looking YAML comment filler.
    big_yaml.write_text("# " + ("x" * 599_998))
    assert big_yaml.stat().st_size > YAML_MAX

    raised = None
    try:
        validate_workflow(str(big_yaml))
    except (RuntimeError, ValueError) as e:
        raised = e
    assert raised is not None, "expected oversize YAML to raise RuntimeError or ValueError"
    msg = str(raised)
    assert str(big_yaml) in msg
    assert str(YAML_MAX) in msg
