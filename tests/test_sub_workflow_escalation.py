"""Tests for child failure → parent escalation wrapping + parent-owned guards.

Covers:
- `called_workflow_error` wrapper shape on parent-escalation responses
- uniform `called_workflow_failed` escalation label across four failure paths
  (guardrail_escalate, cascade_error, schema_violation, parent_output_schema_fail)
- child retained (not auto-deleted) after each failure path
- parent-owned guard on `revise_step` + `delete_session` returns
  `sub_workflow_parent_owned`
- unlinking parent's `called_session` allows child mutation (orphan recovery)
"""

import pytest  # type: ignore[import-not-found]

from megalos_server import state
from megalos_server.main import WORKFLOWS
from tests.conftest import call_tool


_PARENT = "esc-parent"
_PARENT_OUTSCHEMA = "esc-parent-outschema"
_CHILD_GUARD = "esc-child-guardrail"
_CHILD_CASCADE = "esc-child-cascade"
_CHILD_SCHEMA = "esc-child-schema"
_CHILD_PLAIN = "esc-child-plain"


def _parent_wf(call_target: str, name: str = _PARENT, output_schema: dict | None = None) -> dict:
    step_p2: dict = {
        "id": "p2",
        "title": "Parent call step",
        "directive_template": "hand off",
        "gates": ["done"],
        "anti_patterns": [],
        "call": call_target,
    }
    if output_schema is not None:
        step_p2["output_schema"] = output_schema
    return {
        "name": name,
        "description": "parent with call step",
        "category": "test",
        "output_format": "text",
        "steps": [
            {"id": "p1", "title": "Parent step 1", "directive_template": "do p1",
             "gates": ["done"], "anti_patterns": []},
            step_p2,
            {"id": "p3", "title": "Parent step 3", "directive_template": "do p3",
             "gates": ["done"], "anti_patterns": []},
        ],
    }


def _child_wf_guardrail() -> dict:
    return {
        "name": _CHILD_GUARD,
        "description": "child with escalation guardrail",
        "category": "test",
        "output_format": "text",
        "steps": [
            {"id": "c1", "title": "Child step 1", "directive_template": "child work",
             "gates": ["done"], "anti_patterns": []},
            {"id": "c2", "title": "Child step 2", "directive_template": "child finish",
             "gates": ["done"], "anti_patterns": []},
        ],
        "guardrails": [{
            "id": "block-trigger",
            "trigger": {"type": "keyword_match", "patterns": ["BOOM"]},
            "action": "escalate",
            "message": "trigger word detected",
        }],
    }


def _child_wf_cascade() -> dict:
    # c1 output flag drives c2's precondition (skipped when flag=no). c3 then
    # references step_data.c2 via when_present → _SkippedPredecessor.
    return {
        "name": _CHILD_CASCADE,
        "description": "child triggering cascade on precondition",
        "category": "test",
        "output_format": "text",
        "steps": [
            {"id": "c1", "title": "Child step 1", "directive_template": "emit flag",
             "gates": ["done"], "anti_patterns": [],
             "output_schema": {
                 "type": "object",
                 "required": ["flag"],
                 "properties": {"flag": {"type": "string"}},
             }},
            {"id": "c2", "title": "Child step 2", "directive_template": "only when flag yes",
             "gates": ["done"], "anti_patterns": [],
             "precondition": {"when_equals": {"ref": "step_data.c1.flag", "value": "yes"}}},
            {"id": "c3", "title": "Child step 3", "directive_template": "needs c2",
             "gates": ["done"], "anti_patterns": [],
             "precondition": {"when_present": "step_data.c2"}},
        ],
    }


def _child_wf_schema() -> dict:
    # output_schema + max_retries:1 so first invalid submit yields retries_exhausted.
    return {
        "name": _CHILD_SCHEMA,
        "description": "child whose last step has output_schema + max_retries 1",
        "category": "test",
        "output_format": "text",
        "steps": [
            {"id": "c1", "title": "Child only step", "directive_template": "emit shape",
             "gates": ["done"], "anti_patterns": [],
             "output_schema": {
                 "type": "object",
                 "required": ["verdict"],
                 "properties": {"verdict": {"type": "string"}},
             },
             "max_retries": 1},
        ],
    }


def _child_wf_plain() -> dict:
    # plain child used for parent-output-schema-fail path (child succeeds; parent rejects).
    return {
        "name": _CHILD_PLAIN,
        "description": "plain child; emits freeform",
        "category": "test",
        "output_format": "text",
        "steps": [
            {"id": "c1", "title": "Child only step", "directive_template": "freeform",
             "gates": ["done"], "anti_patterns": []},
        ],
    }


@pytest.fixture(autouse=True)
def _register_wfs():
    WORKFLOWS[_CHILD_GUARD] = _child_wf_guardrail()
    WORKFLOWS[_CHILD_CASCADE] = _child_wf_cascade()
    WORKFLOWS[_CHILD_SCHEMA] = _child_wf_schema()
    WORKFLOWS[_CHILD_PLAIN] = _child_wf_plain()
    WORKFLOWS[_PARENT + "-guard"] = _parent_wf(_CHILD_GUARD, name=_PARENT + "-guard")
    WORKFLOWS[_PARENT + "-cascade"] = _parent_wf(_CHILD_CASCADE, name=_PARENT + "-cascade")
    WORKFLOWS[_PARENT + "-schema"] = _parent_wf(_CHILD_SCHEMA, name=_PARENT + "-schema")
    WORKFLOWS[_PARENT_OUTSCHEMA] = _parent_wf(
        _CHILD_PLAIN,
        name=_PARENT_OUTSCHEMA,
        output_schema={
            "type": "object",
            "required": ["verdict"],
            "properties": {"verdict": {"type": "string"}},
        },
    )
    yield
    for key in (
        _CHILD_GUARD, _CHILD_CASCADE, _CHILD_SCHEMA, _CHILD_PLAIN,
        _PARENT + "-guard", _PARENT + "-cascade", _PARENT + "-schema",
        _PARENT_OUTSCHEMA,
    ):
        WORKFLOWS.pop(key, None)


def _spawn(parent_wf_name: str) -> tuple[str, str]:
    p = call_tool("start_workflow", {"workflow_type": parent_wf_name, "context": ""})
    parent_sid = p["session_id"]
    call_tool("submit_step", {"session_id": parent_sid, "step_id": "p1", "content": "p1-done"})
    spawn = call_tool(
        "enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p2"}
    )
    return parent_sid, spawn["session_id"]


def _trigger_guardrail(parent_sid: str, child_sid: str) -> dict:
    return call_tool(
        "submit_step", {"session_id": child_sid, "step_id": "c1", "content": "BOOM content"}
    )


def _trigger_cascade(parent_sid: str, child_sid: str) -> dict:
    # c1 emits flag=no → c2 skipped → c3 precondition references c2 → raise.
    return call_tool(
        "submit_step",
        {"session_id": child_sid, "step_id": "c1", "content": '{"flag": "no"}'},
    )


def _trigger_retries_exhausted(parent_sid: str, child_sid: str) -> dict:
    return call_tool(
        "submit_step",
        {"session_id": child_sid, "step_id": "c1", "content": "not-json"},
    )


def _trigger_parent_schema_fail(parent_sid: str, child_sid: str) -> dict:
    # child emits freeform; parent call-step output_schema requires {"verdict"} → fail.
    return call_tool(
        "submit_step",
        {"session_id": child_sid, "step_id": "c1", "content": "freeform-not-object"},
    )


# --- T03 tests -------------------------------------------------------------


def test_child_guardrail_escalate_wraps_parent():
    parent_sid, child_sid = _spawn(_PARENT + "-guard")
    r = _trigger_guardrail(parent_sid, child_sid)
    assert r.get("code") == "session_escalated" and "called_workflow_error" in r


def test_child_cascade_error_wraps_parent():
    parent_sid, child_sid = _spawn(_PARENT + "-cascade")
    r = _trigger_cascade(parent_sid, child_sid)
    assert r["called_workflow_error"]["child_error"]["code"] == "skipped_predecessor_reference"


def test_child_retries_exhausted_wraps_parent():
    parent_sid, child_sid = _spawn(_PARENT + "-schema")
    r = _trigger_retries_exhausted(parent_sid, child_sid)
    assert r["called_workflow_error"]["child_error"]["retries_exhausted"] is True


def test_parent_output_schema_fail_wraps_called_workflow_error():
    parent_sid, child_sid = _spawn(_PARENT_OUTSCHEMA)
    r = _trigger_parent_schema_fail(parent_sid, child_sid)
    assert r["called_workflow_error"]["child_error"]["reason"] == "parent_output_schema_fail"


def test_called_workflow_error_wrapper_has_three_fields():
    parent_sid, child_sid = _spawn(_PARENT + "-guard")
    r = _trigger_guardrail(parent_sid, child_sid)
    assert set(r["called_workflow_error"].keys()) == {
        "child_session_id", "child_workflow_type", "child_error"
    }


def test_parent_escalation_record_uses_uniform_label():
    parent_sid, child_sid = _spawn(_PARENT + "-cascade")
    _trigger_cascade(parent_sid, child_sid)
    parent = state.get_session(parent_sid)
    assert parent["escalation"]["guardrail_id"] == "called_workflow_failed"


def test_child_retained_after_failure():
    parent_sid, child_sid = _spawn(_PARENT + "-schema")
    _trigger_retries_exhausted(parent_sid, child_sid)
    # Child is retained — state.get_session resolves (does not raise KeyError).
    child = state.get_session(child_sid)
    assert child["session_id"] == child_sid


def test_revise_retained_child_returns_parent_owned():
    parent_sid, child_sid = _spawn(_PARENT + "-guard")
    _trigger_guardrail(parent_sid, child_sid)
    r = call_tool("revise_step", {"session_id": child_sid, "step_id": "c1"})
    assert r.get("code") == "sub_workflow_parent_owned"


def test_delete_retained_child_returns_parent_owned():
    parent_sid, child_sid = _spawn(_PARENT + "-guard")
    _trigger_guardrail(parent_sid, child_sid)
    r = call_tool("delete_session", {"session_id": child_sid})
    assert r.get("code") == "sub_workflow_parent_owned"


def test_delete_child_allowed_when_not_parent_owned():
    parent_sid, child_sid = _spawn(_PARENT + "-guard")
    _trigger_guardrail(parent_sid, child_sid)
    state.set_called_session(parent_sid, None)
    r = call_tool("delete_session", {"session_id": child_sid})
    assert r.get("code") is None and r.get("session_id") == child_sid


def test_parent_owned_guard_envelope_includes_parent_sid_and_call_step():
    parent_sid, child_sid = _spawn(_PARENT + "-guard")
    _trigger_guardrail(parent_sid, child_sid)
    r = call_tool("revise_step", {"session_id": child_sid, "step_id": "c1"})
    assert r.get("parent_session_id") == parent_sid and r.get("call_step_id") == "p2"
