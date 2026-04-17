"""Tests for step-level precondition grammar and parse-time rejects."""

import os
import tempfile

import pytest

from megalos_server import state
from megalos_server.main import WORKFLOWS
from megalos_server.schema import validate_workflow
from megalos_server.tools import (
    _SkippedPredecessor,
    _evaluate_precondition,
)
from tests.conftest import call_tool


def _write_and_validate(yaml_str: str) -> list[str]:
    fd, path = tempfile.mkstemp(suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(yaml_str)
        errors, _ = validate_workflow(path)
        return errors
    finally:
        os.unlink(path)


def test_precondition_parses_when_equals():
    yaml_str = """\
name: pc_eq
description: precondition when_equals parses
category: testing
output_format: text
steps:
  - id: step_1
    title: First
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
    output_schema:
      type: object
      properties:
        field_a: {type: string}
  - id: step_2
    title: Second
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
    precondition:
      when_equals:
        ref: step_data.step_1.field_a
        value: yes
"""
    errors = _write_and_validate(yaml_str)
    assert errors == [], errors


def test_precondition_parses_when_present():
    yaml_str = """\
name: pc_pres
description: precondition when_present parses
category: testing
output_format: text
steps:
  - id: step_1
    title: First
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
  - id: step_2
    title: Second
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
    precondition:
      when_present: step_data.step_1
"""
    errors = _write_and_validate(yaml_str)
    assert errors == [], errors


def test_precondition_rejects_malformed_grammar():
    yaml_str = """\
name: pc_bad
description: when_equals missing value
category: testing
output_format: text
steps:
  - id: step_1
    title: First
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
  - id: step_2
    title: Second
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
    precondition:
      when_equals:
        ref: step_data.step_1
"""
    errors = _write_and_validate(yaml_str)
    assert any(
        "step_2" in e and "precondition.when_equals" in e and "'value'" in e
        for e in errors
    ), errors


def test_precondition_rejects_dotted_ref_path():
    yaml_str = """\
name: pc_dotref
description: dotted/escaped ref-path rejected
category: testing
output_format: text
steps:
  - id: step_1
    title: First
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
  - id: step_2
    title: Second
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
    precondition:
      when_equals:
        ref: 'step_data.step_1."field.with.dots"'
        value: x
"""
    errors = _write_and_validate(yaml_str)
    assert any(
        "step_2" in e and "precondition.when_equals.ref is not a valid ref-path" in e
        for e in errors
    ), errors


def test_precondition_rejects_forward_ref():
    yaml_str = """\
name: pc_fwd
description: precondition references a later step
category: testing
output_format: text
steps:
  - id: step_1
    title: First
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
  - id: step_2
    title: Second
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
    precondition:
      when_present: step_data.step_3
  - id: step_3
    title: Third
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
"""
    errors = _write_and_validate(yaml_str)
    assert any(
        "step_2" in e and "step_3" in e and "forward ref" in e
        for e in errors
    ), errors


def test_precondition_rejects_first_step():
    yaml_str = """\
name: pc_first
description: precondition on the first step
category: testing
output_format: text
steps:
  - id: step_1
    title: First
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
    precondition:
      when_present: step_data.step_1
  - id: step_2
    title: Second
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
"""
    errors = _write_and_validate(yaml_str)
    assert any(
        "step_1" in e and "first step" in e
        for e in errors
    ), errors


def test_precondition_rejects_subpath_against_schemaless_step():
    yaml_str = """\
name: pc_sub
description: sub-path ref against a step lacking output_schema and collect
category: testing
output_format: text
steps:
  - id: step_1
    title: First
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
  - id: step_2
    title: Second
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
    precondition:
      when_equals:
        ref: step_data.step_1.some_field
        value: ok
"""
    errors = _write_and_validate(yaml_str)
    assert any(
        "step_2" in e and "step_1" in e
        and "output_schema" in e and "collect" in e
        for e in errors
    ), errors


# --- T01: Evaluator unit tests ---


def test_evaluate_when_equals_true():
    pc = {"when_equals": {"ref": "step_data.s1.mode", "value": "run"}}
    assert _evaluate_precondition(pc, {"s1": '{"mode": "run"}'}, set(), "s2") is True


def test_evaluate_when_equals_false():
    pc = {"when_equals": {"ref": "step_data.s1.mode", "value": "other"}}
    assert _evaluate_precondition(pc, {"s1": '{"mode": "run"}'}, set(), "s2") is False


def test_evaluate_when_present_with_predecessor_in_step_data():
    pc = {"when_present": "step_data.s1"}
    assert _evaluate_precondition(pc, {"s1": "anything"}, set(), "s2") is True


def test_evaluate_when_present_with_predecessor_absent_not_skipped():
    pc = {"when_present": "step_data.s1"}
    assert _evaluate_precondition(pc, {}, set(), "s2") is False


def test_evaluate_when_present_with_predecessor_in_skipped_set():
    pc = {"when_present": "step_data.s1"}
    with pytest.raises(_SkippedPredecessor) as exc_info:
        _evaluate_precondition(pc, {}, {"s1"}, "s2")
    assert exc_info.value.sid == "s1"
    assert exc_info.value.referencing_step_id == "s2"


# --- T01: Integration-lite tests (live runtime via call_tool) ---


def _precondition_workflow() -> dict:
    """3-step workflow: step_1 sets mode, step_2 skipped when mode == skip_me, step_3 terminal."""
    return {
        "name": "pc-runtime-test",
        "description": "runtime precondition test",
        "category": "testing",
        "output_format": "text",
        "steps": [
            {
                "id": "step_1",
                "title": "First",
                "directive_template": "do it",
                "gates": ["done"],
                "anti_patterns": [],
                "output_schema": {
                    "type": "object",
                    "properties": {"mode": {"type": "string"}},
                    "required": ["mode"],
                },
            },
            {
                "id": "step_2",
                "title": "Second",
                "directive_template": "do it",
                "gates": ["done"],
                "anti_patterns": [],
                "precondition": {
                    "when_equals": {"ref": "step_data.step_1.mode", "value": "run_me"}
                },
            },
            {
                "id": "step_3",
                "title": "Third",
                "directive_template": "do it",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


def _register_pc_workflow():
    WORKFLOWS["pc-runtime-test"] = _precondition_workflow()


def _teardown_pc_workflow():
    state.clear_sessions()
    WORKFLOWS.pop("pc-runtime-test", None)


def test_submit_step_skips_on_false_precondition():
    _register_pc_workflow()
    try:
        r = call_tool("start_workflow", {"workflow_type": "pc-runtime-test", "context": "test"})
        sid = r["session_id"]
        r = call_tool("submit_step", {
            "session_id": sid, "step_id": "step_1", "content": '{"mode": "skip_me"}'
        })
        assert r["next_step"]["id"] == "step_3"
        st = call_tool("get_state", {"session_id": sid})
        assert "step_2" not in st["step_data"]
        assert st["step_data"].get("step_1") == '{"mode": "skip_me"}'
    finally:
        _teardown_pc_workflow()


def test_get_state_surfaces_skipped_steps():
    _register_pc_workflow()
    try:
        r = call_tool("start_workflow", {"workflow_type": "pc-runtime-test", "context": "test"})
        sid = r["session_id"]
        call_tool("submit_step", {
            "session_id": sid, "step_id": "step_1", "content": '{"mode": "skip_me"}'
        })
        st = call_tool("get_state", {"session_id": sid})
        assert st.get("skipped_steps") == ["step_2"]
    finally:
        _teardown_pc_workflow()
