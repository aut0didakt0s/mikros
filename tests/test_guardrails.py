"""Tests for workflow guardrails with escalation."""

import os
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import state
from server.main import WORKFLOWS
from server.schema import validate_workflow
from tests.conftest import call_tool


def _base_steps():
    return [
        {"id": "s1", "title": "Step 1", "directive_template": "Do step 1",
         "gates": ["done"], "anti_patterns": []},
        {"id": "s2", "title": "Step 2", "directive_template": "Do step 2",
         "gates": ["done"], "anti_patterns": []},
        {"id": "safe", "title": "Safe Step", "directive_template": "Safe zone",
         "gates": ["done"], "anti_patterns": []},
    ]


def _wf_with_guardrails(guardrails, extra_steps=None):
    steps = extra_steps or _base_steps()
    return {
        "name": "guardrail-test",
        "description": "test guardrails",
        "category": "testing",
        "output_format": "text",
        "steps": steps,
        "guardrails": guardrails,
    }


def _load_temp_workflow(wf_dict):
    """Write workflow to temp file, load via WORKFLOWS, return cleanup func."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(wf_dict, f)
    f.close()
    # Validate first
    errors, _ = validate_workflow(f.name)
    assert not errors, f"Workflow validation failed: {errors}"
    WORKFLOWS["guardrail-test"] = wf_dict
    return f.name


def _setup(wf_dict, context="test context"):
    state.clear_sessions()
    WORKFLOWS.pop("guardrail-test", None)
    _load_temp_workflow(wf_dict)
    result = call_tool("start_workflow", {"workflow_type": "guardrail-test", "context": context})
    return result["session_id"]


# --- keyword_match triggers force_branch ---

def test_keyword_match_force_branch():
    wf = _wf_with_guardrails([{
        "id": "profanity-guard",
        "trigger": {"type": "keyword_match", "patterns": ["bad\\s+word", "offensive"]},
        "action": "force_branch",
        "target_step": "safe",
        "message": "Content flagged, redirecting to safe step.",
    }])
    sid = _setup(wf)
    result = call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "this has a BAD  WORD in it"})
    # Should be force-branched to "safe" instead of normal "s2"
    assert "error" not in result
    assert result["next_step"]["id"] == "safe"


def test_keyword_match_no_match_proceeds_normally():
    wf = _wf_with_guardrails([{
        "id": "profanity-guard",
        "trigger": {"type": "keyword_match", "patterns": ["bad\\s+word"]},
        "action": "force_branch",
        "target_step": "safe",
        "message": "Flagged.",
    }])
    sid = _setup(wf)
    result = call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "perfectly fine content"})
    assert result["next_step"]["id"] == "s2"


# --- step_revisit triggers escalate ---

def test_step_revisit_triggers_escalate():
    steps = [
        {"id": "s1", "title": "Step 1", "directive_template": "Do it",
         "gates": ["done"], "anti_patterns": [],
         "branches": [{"next": "s1", "condition": "loop"}, {"next": "s2", "condition": "done"}],
         "default_branch": "s1"},
        {"id": "s2", "title": "Step 2", "directive_template": "Finish",
         "gates": ["done"], "anti_patterns": []},
    ]
    wf = _wf_with_guardrails([{
        "id": "loop-guard",
        "trigger": {"type": "step_revisit", "max_visits": 3},
        "action": "escalate",
        "message": "Too many revisits, escalating.",
    }], extra_steps=steps)
    sid = _setup(wf)
    # s1 gets visit 1 at start. Submit s1 -> branches back to s1 (visit 2).
    call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "attempt 1", "branch": "s1"})
    # Now at s1 with visit count 2. Submit again -> s1 (visit 3).
    call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "attempt 2", "branch": "s1"})
    # Now at s1 with visit count 3 >= max_visits 3 -> escalate
    result = call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "attempt 3", "branch": "s1"})
    assert "error" in result
    assert "escalat" in result["error"].lower()
    assert result["guardrail_id"] == "loop-guard"


# --- escalated session rejects submit_step ---

def test_escalated_session_rejects_submit():
    steps = [
        {"id": "s1", "title": "Step 1", "directive_template": "Do it",
         "gates": ["done"], "anti_patterns": [],
         "branches": [{"next": "s1", "condition": "loop"}, {"next": "s2", "condition": "done"}],
         "default_branch": "s1"},
        {"id": "s2", "title": "Step 2", "directive_template": "Finish",
         "gates": ["done"], "anti_patterns": []},
    ]
    wf = _wf_with_guardrails([{
        "id": "loop-guard",
        "trigger": {"type": "step_revisit", "max_visits": 2},
        "action": "escalate",
        "message": "Escalated.",
    }], extra_steps=steps)
    sid = _setup(wf)
    # Visit 1 at start. Submit -> visit 2 >= 2 -> escalate on next submit
    call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "once", "branch": "s1"})
    # Now visit count is 2, submit triggers escalate
    result = call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "twice", "branch": "s1"})
    assert "error" in result
    # Further submissions should be rejected
    result2 = call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "blocked"})
    assert "error" in result2
    assert "escalat" in result2["error"].lower()
    assert "escalation" in result2


# --- warn allows continuation ---

def test_warn_allows_continuation():
    wf = _wf_with_guardrails([{
        "id": "length-warn",
        "trigger": {"type": "output_length", "max_chars": 10},
        "action": "warn",
        "message": "Content is very long.",
    }])
    sid = _setup(wf)
    result = call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "a" * 20})
    assert "error" not in result
    assert result.get("guardrail_warning") == "Content is very long."
    assert result["next_step"]["id"] == "s2"


def test_warn_no_trigger_no_warning():
    wf = _wf_with_guardrails([{
        "id": "length-warn",
        "trigger": {"type": "output_length", "max_chars": 100},
        "action": "warn",
        "message": "Content is very long.",
    }])
    sid = _setup(wf)
    result = call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "short"})
    assert "guardrail_warning" not in result


# --- no guardrails backward compat ---

def test_no_guardrails_backward_compat():
    """Workflow without guardrails field works as before."""
    wf = {
        "name": "guardrail-test",
        "description": "no guardrails",
        "category": "testing",
        "output_format": "text",
        "steps": _base_steps(),
    }
    sid = _setup(wf)
    result = call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "hello"})
    assert "error" not in result
    assert result["next_step"]["id"] == "s2"


# --- load-time validation of guardrail target_step references ---

def test_validate_guardrail_bad_target_step():
    wf = _wf_with_guardrails([{
        "id": "bad-ref",
        "trigger": {"type": "keyword_match", "patterns": ["x"]},
        "action": "force_branch",
        "target_step": "nonexistent",
        "message": "bad",
    }])
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(wf, f)
    f.close()
    errors, _ = validate_workflow(f.name)
    assert any("nonexistent" in e for e in errors)
    os.unlink(f.name)


def test_validate_guardrail_missing_target_step_for_force_branch():
    wf = _wf_with_guardrails([{
        "id": "no-target",
        "trigger": {"type": "keyword_match", "patterns": ["x"]},
        "action": "force_branch",
        "message": "missing target",
    }])
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(wf, f)
    f.close()
    errors, _ = validate_workflow(f.name)
    assert any("target_step" in e for e in errors)
    os.unlink(f.name)


def test_validate_guardrail_invalid_trigger_type():
    wf = _wf_with_guardrails([{
        "id": "bad-type",
        "trigger": {"type": "magic"},
        "action": "warn",
        "message": "bad trigger",
    }])
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(wf, f)
    f.close()
    errors, _ = validate_workflow(f.name)
    assert any("trigger type" in e for e in errors)
    os.unlink(f.name)


# --- output_length trigger ---

def test_output_length_triggers_force_branch():
    wf = _wf_with_guardrails([{
        "id": "too-long",
        "trigger": {"type": "output_length", "max_chars": 5},
        "action": "force_branch",
        "target_step": "safe",
        "message": "Too long!",
    }])
    sid = _setup(wf)
    result = call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "abcdef"})
    assert result["next_step"]["id"] == "safe"


def test_output_length_under_limit_passes():
    wf = _wf_with_guardrails([{
        "id": "too-long",
        "trigger": {"type": "output_length", "max_chars": 100},
        "action": "force_branch",
        "target_step": "safe",
        "message": "Too long!",
    }])
    sid = _setup(wf)
    result = call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "short"})
    assert result["next_step"]["id"] == "s2"


# --- step_count trigger ---

def test_step_count_triggers_warn():
    wf = _wf_with_guardrails([{
        "id": "too-many-steps",
        "trigger": {"type": "step_count", "max": 2},
        "action": "warn",
        "message": "Many steps submitted.",
    }])
    sid = _setup(wf)
    # step_data has 0 entries -> submit s1 stores it (now 1) but check happens
    # after store so step_data has 1 entry, < 2 -> no trigger
    result1 = call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "first"})
    assert "guardrail_warning" not in result1
    # Now step_data has 1 entry, submit s2 stores it (now 2) >= 2 -> trigger
    result2 = call_tool("submit_step", {"session_id": sid, "step_id": "s2", "content": "second"})
    assert result2.get("guardrail_warning") == "Many steps submitted."


# --- first matching guardrail wins ---

def test_first_guardrail_wins():
    wf = _wf_with_guardrails([
        {
            "id": "first",
            "trigger": {"type": "output_length", "max_chars": 3},
            "action": "warn",
            "message": "First guardrail.",
        },
        {
            "id": "second",
            "trigger": {"type": "output_length", "max_chars": 3},
            "action": "force_branch",
            "target_step": "safe",
            "message": "Second guardrail.",
        },
    ])
    sid = _setup(wf)
    result = call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "abcdef"})
    # First one is warn, so should get warning and proceed to s2 (not force_branch to safe)
    assert result.get("guardrail_warning") == "First guardrail."
    assert result["next_step"]["id"] == "s2"


def teardown_module(_module):
    state.clear_sessions()
    WORKFLOWS.pop("guardrail-test", None)
