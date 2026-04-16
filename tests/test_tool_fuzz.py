"""Parametrized fuzz suite for the 9 MCP tools.

Exercises each tool against four fuzz variants:
  (a) None for each required arg          -> invalid_argument, field=<arg>
  (b) Empty string for required-non-empty -> invalid_argument, field=<arg>, "must not be empty"
  (c) Wrong-type (int/dict/list) per str  -> invalid_argument, field=<arg>
  (d) Oversize payload (submit_step,      -> oversize_payload with max_bytes/actual_bytes
       generate_artifact)

Every case asserts a structured error dict; no case may raise to pytest.

The pydantic.ValidationError that FastMCP's argument-validation layer raises for
None / wrong-type args is normalized at the framework boundary by
megalos_server.middleware.ValidationErrorMiddleware, so all four variants assert
the same {status, code, field, error} contract through raw call_tool — no
test-side workaround is needed.
"""

import pytest  # type: ignore[import-not-found]

from megalos_server import state
from megalos_server.errors import ARTIFACT_MAX, CONTENT_MAX
from megalos_server.main import WORKFLOWS
from tests.conftest import call_tool


_FIXTURE_NAME = "fuzz-test"


def _two_step_workflow() -> dict:
    """Minimal in-fixture workflow used to drive oversize-path setup."""
    return {
        "name": _FIXTURE_NAME,
        "description": "fuzz test workflow",
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
    WORKFLOWS[_FIXTURE_NAME] = _two_step_workflow()


def teardown_function():
    state.clear_sessions()
    WORKFLOWS.pop(_FIXTURE_NAME, None)


# ---------------------------------------------------------------------------
# Variant (a): None for each required arg.
# ---------------------------------------------------------------------------
# (tool, field_to_None, base_args)
_NONE_CASES = [
    ("start_workflow", "workflow_type", {"workflow_type": None, "context": "x"}),
    ("start_workflow", "context", {"workflow_type": _FIXTURE_NAME, "context": None}),
    ("get_state", "session_id", {"session_id": None}),
    ("get_guidelines", "session_id", {"session_id": None}),
    ("submit_step", "session_id", {"session_id": None, "step_id": "first", "content": "x"}),
    ("submit_step", "step_id", {"session_id": "sid", "step_id": None, "content": "x"}),
    ("submit_step", "content", {"session_id": "sid", "step_id": "first", "content": None}),
    ("revise_step", "session_id", {"session_id": None, "step_id": "first"}),
    ("revise_step", "step_id", {"session_id": "sid", "step_id": None}),
    ("delete_session", "session_id", {"session_id": None}),
    ("generate_artifact", "session_id", {"session_id": None}),
]


@pytest.mark.parametrize(
    "tool,field,args",
    _NONE_CASES,
    ids=[f"{t}-{f}-None" for t, f, _ in _NONE_CASES],
)
def test_none_for_required_arg(tool, field, args):
    r = call_tool(tool, args)
    assert isinstance(r, dict), f"expected dict, got {type(r).__name__}"
    assert r.get("status") == "error", f"expected status=error, got {r}"
    assert r.get("code") == "invalid_argument", f"expected invalid_argument, got {r}"
    assert r.get("field") == field, f"expected field={field}, got {r}"


# ---------------------------------------------------------------------------
# Variant (b): empty string for semantically-required string args.
# ---------------------------------------------------------------------------
_EMPTY_CASES = [
    ("start_workflow", "workflow_type", {"workflow_type": "", "context": "x"}),
    ("get_state", "session_id", {"session_id": ""}),
    ("get_guidelines", "session_id", {"session_id": ""}),
    ("submit_step", "session_id", {"session_id": "", "step_id": "first", "content": "x"}),
    ("submit_step", "step_id", {"session_id": "sid", "step_id": "", "content": "x"}),
    ("revise_step", "session_id", {"session_id": "", "step_id": "first"}),
    ("revise_step", "step_id", {"session_id": "sid", "step_id": ""}),
    ("delete_session", "session_id", {"session_id": ""}),
    ("generate_artifact", "session_id", {"session_id": ""}),
]


@pytest.mark.parametrize(
    "tool,field,args",
    _EMPTY_CASES,
    ids=[f"{t}-{f}-empty" for t, f, _ in _EMPTY_CASES],
)
def test_empty_string_for_required_arg(tool, field, args):
    r = call_tool(tool, args)
    assert isinstance(r, dict)
    assert r.get("status") == "error", f"expected status=error, got {r}"
    assert r.get("code") == "invalid_argument", f"expected invalid_argument, got {r}"
    assert r.get("field") == field, f"expected field={field}, got {r}"
    assert "must not be empty" in r.get("error", ""), f"expected 'must not be empty', got {r}"


# ---------------------------------------------------------------------------
# Variant (c): wrong-type (int/dict/list) for every str arg of every tool.
# ---------------------------------------------------------------------------
# Each spec: (tool, str_arg, base_args_template) — base_args_template is a dict of
# valid sibling args; the str_arg slot will be replaced with each wrong-type value.
_WRONG_TYPE_SPECS = [
    ("list_workflows", "category", {}),
    ("start_workflow", "workflow_type", {"context": "x"}),
    ("start_workflow", "context", {"workflow_type": _FIXTURE_NAME}),
    ("get_state", "session_id", {}),
    ("get_guidelines", "session_id", {}),
    ("submit_step", "session_id", {"step_id": "first", "content": "x"}),
    ("submit_step", "step_id", {"session_id": "sid", "content": "x"}),
    ("submit_step", "content", {"session_id": "sid", "step_id": "first"}),
    ("submit_step", "branch", {"session_id": "sid", "step_id": "first", "content": "x"}),
    ("submit_step", "artifact_id", {"session_id": "sid", "step_id": "first", "content": "x"}),
    ("revise_step", "session_id", {"step_id": "first"}),
    ("revise_step", "step_id", {"session_id": "sid"}),
    ("delete_session", "session_id", {}),
    ("generate_artifact", "session_id", {}),
    ("generate_artifact", "output_format", {"session_id": "sid"}),
]

_WRONG_TYPE_VALUES = [
    ("int", 42),
    ("dict", {"k": "v"}),
    ("list", ["a", "b"]),
]

_WRONG_TYPE_CASES = [
    (tool, arg, {**base, arg: bad_value}, label)
    for tool, arg, base in _WRONG_TYPE_SPECS
    for label, bad_value in _WRONG_TYPE_VALUES
]


@pytest.mark.parametrize(
    "tool,field,args,label",
    _WRONG_TYPE_CASES,
    ids=[f"{t}-{a}-{lbl}" for t, a, _, lbl in _WRONG_TYPE_CASES],
)
def test_wrong_type_for_str_arg(tool, field, args, label):
    r = call_tool(tool, args)
    assert isinstance(r, dict)
    assert r.get("status") == "error", f"expected status=error, got {r}"
    assert r.get("code") == "invalid_argument", f"expected invalid_argument, got {r}"
    assert r.get("field") == field, f"expected field={field}, got {r}"


# ---------------------------------------------------------------------------
# Variant (d): oversize payloads — submit_step(content) and generate_artifact.
# ---------------------------------------------------------------------------
def test_submit_step_oversize_content():
    r = call_tool("start_workflow", {"workflow_type": _FIXTURE_NAME, "context": "x"})
    sid = r["session_id"]
    big = "a" * (CONTENT_MAX + 1)
    r = call_tool("submit_step", {"session_id": sid, "step_id": "first", "content": big})
    assert isinstance(r, dict)
    assert r.get("status") == "error", f"expected status=error, got {r}"
    assert r.get("code") == "oversize_payload", f"expected oversize_payload, got {r}"
    assert r.get("field") == "content", f"expected field=content, got {r}"
    assert r["max_bytes"] == CONTENT_MAX
    assert r["actual_bytes"] > CONTENT_MAX


def test_generate_artifact_oversize_artifact():
    r = call_tool("start_workflow", {"workflow_type": _FIXTURE_NAME, "context": "x"})
    sid = r["session_id"]
    # Stash big content directly into step_data and force COMPLETE — same trick as
    # tests/test_size_limits.py. Each step content stays under CONTENT_MAX, but the
    # joined artifact exceeds ARTIFACT_MAX.
    big_chunk = "z" * 600_000
    new_step_data = {"first": big_chunk, "second": big_chunk}
    state.update_session(sid, step_data=new_step_data, current_step=state.COMPLETE)

    r = call_tool("generate_artifact", {"session_id": sid, "output_format": "text"})
    assert isinstance(r, dict)
    assert r.get("status") == "error", f"expected status=error, got {r}"
    assert r.get("code") == "oversize_payload", f"expected oversize_payload, got {r}"
    assert r.get("field") == "artifact", f"expected field=artifact, got {r}"
    assert r["max_bytes"] == ARTIFACT_MAX
    assert r["actual_bytes"] > ARTIFACT_MAX
