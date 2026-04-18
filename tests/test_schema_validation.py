"""Tests for output schema validation in submit_step."""

import json
import tempfile
import os

from megalos_server import state
from tests.conftest import call_tool


# Minimal workflow YAML with output_schema on step 1
_SCHEMA_WORKFLOW_YAML = """\
name: test_schema
description: Test workflow with output schema validation
category: testing
output_format: text

steps:
  - id: collect
    title: Collect Data
    directive_template: Collect structured data from the user.
    gates:
      - Data collected
    anti_patterns:
      - Skipping validation
    output_schema:
      type: object
      required:
        - title
        - tags
        - confirmed
      properties:
        title:
          type: string
          minLength: 3
        tags:
          type: array
          minItems: 1
          items:
            type: string
        confirmed:
          type: boolean
    validation_hint: "Submit a JSON object with title (string, 3+ chars), tags (non-empty array of strings), and confirmed (boolean)."
    max_retries: 2
  - id: summarize
    title: Summarize
    directive_template: Summarize the collected data.
    gates:
      - Summary complete
    anti_patterns:
      - Being too verbose
"""

# Same but without output_schema — for backward compat test
_PLAIN_WORKFLOW_YAML = """\
name: test_plain
description: Test workflow without output schema
category: testing
output_format: text

steps:
  - id: step_a
    title: Step A
    directive_template: Do something.
    gates:
      - Done
    anti_patterns:
      - Nothing
  - id: step_b
    title: Step B
    directive_template: Do something else.
    gates:
      - Done
    anti_patterns:
      - Nothing
"""


def _load_workflow_from_string(yaml_str):
    """Write YAML to a temp file and load it via the standard loader."""
    from megalos_server.schema import load_workflow
    fd, path = tempfile.mkstemp(suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(yaml_str)
        return load_workflow(path)
    finally:
        os.unlink(path)


def _register_and_start(yaml_str, wf_name):
    """Register a workflow from YAML string, start a session, return session_id."""
    from megalos_server.main import WORKFLOWS

    wf = _load_workflow_from_string(yaml_str)
    WORKFLOWS[wf_name] = wf
    state.clear_sessions()
    r = call_tool("start_workflow", {"workflow_type": wf_name, "context": "test"})
    return r["session_id"]


class TestSchemaValidation:
    """Output schema validation in submit_step."""

    def setup_method(self):
        state.clear_sessions()
        from megalos_server.main import WORKFLOWS
        self._wf = _load_workflow_from_string(_SCHEMA_WORKFLOW_YAML)
        WORKFLOWS["test_schema"] = self._wf

    def _start(self):
        r = call_tool("start_workflow", {"workflow_type": "test_schema", "context": "test"})
        return r["session_id"]

    def test_valid_submission_accepted(self):
        sid = self._start()
        content = json.dumps({"title": "My Project", "tags": ["python"], "confirmed": True})
        r = call_tool("submit_step", {"session_id": sid, "step_id": "collect", "content": content})
        assert "submitted" in r
        assert r["submitted"]["id"] == "collect"
        assert r["next_step"]["id"] == "summarize"

    def test_missing_required_field_rejected(self):
        sid = self._start()
        content = json.dumps({"title": "My Project", "confirmed": True})  # missing tags
        r = call_tool("submit_step", {"session_id": sid, "step_id": "collect", "content": content})
        assert r["status"] == "validation_error"
        assert any("tags" in e for e in r["errors"])
        assert r["retries_remaining"] == 1  # max_retries=2, first failure

    def test_wrong_type_rejected(self):
        sid = self._start()
        content = json.dumps({"title": "My Project", "tags": "not-an-array", "confirmed": True})
        r = call_tool("submit_step", {"session_id": sid, "step_id": "collect", "content": content})
        assert r["status"] == "validation_error"
        assert any("not-an-array" in e or "array" in e for e in r["errors"])

    def test_constraint_violation_rejected(self):
        sid = self._start()
        content = json.dumps({"title": "ab", "tags": ["x"], "confirmed": True})  # title too short
        r = call_tool("submit_step", {"session_id": sid, "step_id": "collect", "content": content})
        assert r["status"] == "validation_error"
        assert any("short" in e.lower() or "minLength" in e or "characters" in e.lower() for e in r["errors"])

    def test_max_retries_exceeded(self):
        sid = self._start()
        bad = json.dumps({"title": "ok title", "confirmed": True})  # missing tags
        # First attempt
        r1 = call_tool("submit_step", {"session_id": sid, "step_id": "collect", "content": bad})
        assert r1["status"] == "validation_error"
        assert r1["retries_remaining"] == 1
        # Second attempt — exhausts retries (max_retries=2)
        r2 = call_tool("submit_step", {"session_id": sid, "step_id": "collect", "content": bad})
        assert r2["status"] == "validation_error"
        assert r2["retries_exhausted"] is True
        assert "Max retries" in r2["message"]

    def test_validation_hint_included(self):
        sid = self._start()
        content = json.dumps({"title": "ok"})  # missing fields
        r = call_tool("submit_step", {"session_id": sid, "step_id": "collect", "content": content})
        assert r["status"] == "validation_error"
        assert "validation_hint" in r
        assert "JSON object" in r["validation_hint"]

    def test_invalid_json_rejected(self):
        sid = self._start()
        r = call_tool("submit_step", {"session_id": sid, "step_id": "collect", "content": "not json"})
        assert r["status"] == "validation_error"
        assert any("not valid JSON" in e for e in r["errors"])

    def test_field_level_error_message_includes_path(self):
        """Field-level errors prefix messages with the JSON path (e.g., 'title: ...')."""
        sid = self._start()
        # Wrong type for title (int instead of string)
        content = json.dumps({"title": 42, "tags": ["x"], "confirmed": True})
        r = call_tool("submit_step", {"session_id": sid, "step_id": "collect", "content": content})
        assert r["status"] == "validation_error"
        assert any(e.startswith("title:") for e in r["errors"]), r["errors"]

    def test_retry_then_succeed(self):
        """LLM self-correction: fail once, then succeed on retry."""
        sid = self._start()
        bad = json.dumps({"title": "ok title", "confirmed": True})  # missing tags
        r1 = call_tool("submit_step", {"session_id": sid, "step_id": "collect", "content": bad})
        assert r1["status"] == "validation_error"

        good = json.dumps({"title": "ok title", "tags": ["python"], "confirmed": True})
        r2 = call_tool("submit_step", {"session_id": sid, "step_id": "collect", "content": good})
        assert "submitted" in r2
        assert r2["submitted"]["id"] == "collect"


class TestBackwardCompatibility:
    """Steps without output_schema accept any string content."""

    def setup_method(self):
        state.clear_sessions()
        from megalos_server.main import WORKFLOWS
        WORKFLOWS["test_plain"] = _load_workflow_from_string(_PLAIN_WORKFLOW_YAML)

    def test_plain_string_accepted(self):
        r = call_tool("start_workflow", {"workflow_type": "test_plain", "context": "test"})
        sid = r["session_id"]
        r = call_tool("submit_step", {"session_id": sid, "step_id": "step_a", "content": "any string works"})
        assert "submitted" in r
        assert r["submitted"]["id"] == "step_a"

    def test_existing_canonical_workflow_unchanged(self):
        """The canonical fixture has no output_schema — should work as before."""
        r = call_tool("start_workflow", {"workflow_type": "canonical", "context": "test"})
        sid = r["session_id"]
        r = call_tool("submit_step", {"session_id": sid, "step_id": "alpha", "content": "plain text"})
        assert "submitted" in r


class TestSchemaLoadTimeValidation:
    """Invalid output_schema caught at workflow load time."""

    def test_invalid_schema_caught(self):
        from megalos_server.schema import validate_workflow
        bad_yaml = """\
name: bad
description: Bad schema
category: testing
output_format: text
steps:
  - id: step1
    title: Step 1
    directive_template: Do it.
    gates: [done]
    anti_patterns: [none]
    output_schema: "not a dict"
"""
        fd, path = tempfile.mkstemp(suffix=".yaml")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(bad_yaml)
            errors, _ = validate_workflow(path)
            assert any("output_schema" in e for e in errors)
        finally:
            os.unlink(path)

    def test_collect_without_output_schema_rejected(self):
        from megalos_server.schema import validate_workflow
        bad_yaml = """\
name: bad
description: collect missing output_schema
category: testing
output_format: text
steps:
  - id: gather
    title: Gather
    directive_template: Gather info.
    gates: [done]
    anti_patterns: [none]
    collect: true
"""
        fd, path = tempfile.mkstemp(suffix=".yaml")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(bad_yaml)
            errors, _ = validate_workflow(path)
            assert any("gather" in e and "output_schema" in e for e in errors), errors
        finally:
            os.unlink(path)

    def test_collect_with_output_schema_loads(self):
        from megalos_server.schema import validate_workflow
        good_yaml = """\
name: good
description: collect with schema
category: testing
output_format: text
steps:
  - id: gather
    title: Gather
    directive_template: Gather info.
    gates: [done]
    anti_patterns: [none]
    collect: true
    output_schema:
      type: object
      required: [x]
      properties:
        x:
          type: string
"""
        fd, path = tempfile.mkstemp(suffix=".yaml")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(good_yaml)
            errors, _ = validate_workflow(path)
            assert errors == [], errors
        finally:
            os.unlink(path)

    def test_invalid_max_retries_caught(self):
        from megalos_server.schema import validate_workflow
        bad_yaml = """\
name: bad
description: Bad retries
category: testing
output_format: text
steps:
  - id: step1
    title: Step 1
    directive_template: Do it.
    gates: [done]
    anti_patterns: [none]
    max_retries: -1
"""
        fd, path = tempfile.mkstemp(suffix=".yaml")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(bad_yaml)
            errors, _ = validate_workflow(path)
            assert any("max_retries" in e for e in errors)
        finally:
            os.unlink(path)

    def test_step_description_accepted(self):
        from megalos_server.schema import validate_workflow
        good_yaml = """\
name: good
description: step_description present
category: testing
output_format: text
steps:
  - id: s1
    title: Step 1
    step_description: A concise, action-oriented description.
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
"""
        fd, path = tempfile.mkstemp(suffix=".yaml")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(good_yaml)
            errors, _ = validate_workflow(path)
            assert errors == [], errors
        finally:
            os.unlink(path)

    def test_step_description_non_string_rejected(self):
        from megalos_server.schema import validate_workflow
        bad_yaml = """\
name: bad
description: non-string step_description
category: testing
output_format: text
steps:
  - id: s1
    title: Step 1
    step_description: 42
    directive_template: do it
    gates: [done]
    anti_patterns: [none]
"""
        fd, path = tempfile.mkstemp(suffix=".yaml")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(bad_yaml)
            errors, _ = validate_workflow(path)
            assert any("step_description" in e and "string" in e for e in errors), errors
        finally:
            os.unlink(path)


_MINIMAL_YAML = """\
name: toy
description: tiny
category: test
output_format: text
steps:
  - id: s1
    title: Step
    directive_template: do it
    gates: []
    anti_patterns: []
"""


def test_workflow_without_schema_version_defaults_to_04():
    from megalos_server.schema import load_workflow
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(_MINIMAL_YAML)
        path = f.name
    try:
        doc = load_workflow(path)
        assert doc["schema_version"] == "0.4"
    finally:
        os.unlink(path)


def test_workflow_with_explicit_schema_version_01():
    from megalos_server.schema import load_workflow
    yaml_str = 'schema_version: "0.1"\n' + _MINIMAL_YAML
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_str)
        path = f.name
    try:
        doc = load_workflow(path)
        assert doc["schema_version"] == "0.1"
    finally:
        os.unlink(path)


def test_workflow_with_unknown_schema_version_passes_through():
    from megalos_server.schema import load_workflow
    yaml_str = 'schema_version: "9.9"\n' + _MINIMAL_YAML
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_str)
        path = f.name
    try:
        doc = load_workflow(path)
        assert doc["schema_version"] == "9.9"
    finally:
        os.unlink(path)
