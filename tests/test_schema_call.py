"""Tests for step-level `call` + `call_context_from` grammar and parse-time rejects (M004/S01/T01).

Also cross-workflow validation: call target existence + cycle detection (M004/S01/T02).
"""

import os
import tempfile

import pytest

from megalos_server import create_app
from megalos_server.schema import validate_workflow, validate_workflow_calls


def _make_wf(name: str, steps: list[dict]) -> dict:
    return {"name": name, "description": "", "category": "test",
            "output_format": "markdown", "steps": steps}


def _step_call(step_id: str, target: str) -> dict:
    return {"id": step_id, "title": "", "directive_template": "",
            "gates": [], "anti_patterns": [], "call": target}


def _write_and_validate(yaml_str: str) -> list[str]:
    fd, path = tempfile.mkstemp(suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(yaml_str)
        errors, _ = validate_workflow(path)
        return errors
    finally:
        os.unlink(path)


def test_call_alone_parses_clean():
    yaml_str = """\
name: call_only
description: step with call field alone
category: testing
output_format: text
steps:
  - id: step_1
    title: First
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
  - id: step_2
    title: Sub
    directive_template: hand off
    gates: [done]
    anti_patterns: [none]
    call: child_wf
"""
    errors = _write_and_validate(yaml_str)
    assert errors == [], errors


def test_call_with_call_context_from_parses_clean():
    yaml_str = """\
name: call_with_ctx
description: call + call_context_from
category: testing
output_format: text
steps:
  - id: s1
    title: First
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
  - id: s2
    title: Sub
    directive_template: hand off
    gates: [done]
    anti_patterns: [none]
    call: child_wf
    call_context_from: step_data.s1.topic
"""
    errors = _write_and_validate(yaml_str)
    assert errors == [], errors


def test_call_with_output_schema_parses_clean():
    yaml_str = """\
name: call_with_schema
description: call + output_schema is allowed (M004 D14)
category: testing
output_format: text
steps:
  - id: step_1
    title: First
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
  - id: step_2
    title: Sub
    directive_template: hand off
    gates: [done]
    anti_patterns: [none]
    call: child_wf
    output_schema:
      type: object
      properties:
        result: {type: string}
"""
    errors = _write_and_validate(yaml_str)
    assert errors == [], errors


def test_call_with_collect_true_rejected():
    yaml_str = """\
name: call_collect
description: call + collect true is rejected
category: testing
output_format: text
steps:
  - id: step_1
    title: First
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
  - id: step_2
    title: Sub
    directive_template: hand off
    gates: [done]
    anti_patterns: [none]
    call: child_wf
    collect: true
    output_schema:
      type: object
      properties:
        result: {type: string}
"""
    errors = _write_and_validate(yaml_str)
    assert any("call_with_collect" in e for e in errors), errors


def test_call_with_intermediate_artifacts_rejected():
    yaml_str = """\
name: call_ia
description: call + intermediate_artifacts is rejected
category: testing
output_format: text
steps:
  - id: step_1
    title: First
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
  - id: step_2
    title: Sub
    directive_template: hand off
    gates: [done]
    anti_patterns: [none]
    call: child_wf
    intermediate_artifacts:
      - id: art1
        description: a thing
        schema:
          type: object
"""
    errors = _write_and_validate(yaml_str)
    assert any("call_with_intermediate_artifacts" in e for e in errors), errors


def test_call_context_from_without_call_rejected():
    yaml_str = """\
name: ccf_no_call
description: call_context_from without call is rejected
category: testing
output_format: text
steps:
  - id: s1
    title: First
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
  - id: s2
    title: Sub
    directive_template: hand off
    gates: [done]
    anti_patterns: [none]
    call_context_from: step_data.s1.topic
"""
    errors = _write_and_validate(yaml_str)
    assert any("call_context_from_without_call" in e for e in errors), errors


def test_call_context_from_malformed_ref_rejected():
    yaml_str = """\
name: ccf_bad_ref
description: call_context_from with malformed ref-path is rejected
category: testing
output_format: text
steps:
  - id: s1
    title: First
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
  - id: s2
    title: Sub
    directive_template: hand off
    gates: [done]
    anti_patterns: [none]
    call: child_wf
    call_context_from: not.a.valid.path
"""
    errors = _write_and_validate(yaml_str)
    assert any("call_invalid_context_ref" in e for e in errors), errors


# --- T02: cross-workflow validation --------------------------------------


def test_clean_dag_passes():
    workflows = {
        "A": _make_wf("A", [_step_call("s1", "B"), _step_call("s2", "C")]),
        "B": _make_wf("B", [_step_call("s1", "D")]),
        "C": _make_wf("C", []),
        "D": _make_wf("D", []),
    }
    assert validate_workflow_calls(workflows) == []


def test_unknown_call_target_rejected():
    workflows = {
        "A": _make_wf("A", [_step_call("s1", "missing_child")]),
    }
    errors = validate_workflow_calls(workflows)
    assert any("unknown_call_target" in e and "missing_child" in e for e in errors), errors


def test_self_cycle_rejected():
    workflows = {
        "A": _make_wf("A", [_step_call("s1", "A")]),
    }
    errors = validate_workflow_calls(workflows)
    assert any("call_cycle_detected" in e for e in errors), errors


def test_direct_cycle_rejected():
    workflows = {
        "A": _make_wf("A", [_step_call("s1", "B")]),
        "B": _make_wf("B", [_step_call("s1", "A")]),
    }
    errors = validate_workflow_calls(workflows)
    assert any("call_cycle_detected" in e for e in errors), errors


def test_indirect_cycle_rejected():
    workflows = {
        "A": _make_wf("A", [_step_call("s1", "B")]),
        "B": _make_wf("B", [_step_call("s1", "C")]),
        "C": _make_wf("C", [_step_call("s1", "A")]),
    }
    errors = validate_workflow_calls(workflows)
    assert any("call_cycle_detected" in e and "A -> B -> C -> A" in e for e in errors), errors


def test_create_app_raises_on_call_cycle(tmp_path):
    parent = tmp_path / "parent.yaml"
    child = tmp_path / "child.yaml"
    parent.write_text(
        "name: parent\n"
        "description: p\n"
        "category: test\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: s1\n"
        "    title: hand off\n"
        "    directive_template: do\n"
        "    gates: [done]\n"
        "    anti_patterns: [none]\n"
        "    call: child\n"
    )
    child.write_text(
        "name: child\n"
        "description: c\n"
        "category: test\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: s1\n"
        "    title: hand back\n"
        "    directive_template: do\n"
        "    gates: [done]\n"
        "    anti_patterns: [none]\n"
        "    call: parent\n"
    )
    with pytest.raises(ValueError, match="call_cycle_detected"):
        create_app(workflow_dir=tmp_path)
