"""Tests for revise_step tool — invalidate-forward semantics."""

from server import state
from tests.conftest import call_tool

CODING_STEPS = ["discuss", "plan", "execute", "review", "iterate", "deliver"]
ESSAY_STEPS = ["explore", "commit", "structure", "draft", "revise", "polish"]


def _fresh_session(workflow="coding", steps_to_complete=None):
    """Start workflow and optionally submit steps. Returns session_id."""
    state.clear_sessions()
    r = call_tool("start_workflow", {"workflow_type": workflow, "context": "test"})
    sid = r["session_id"]
    for step_id in (steps_to_complete or []):
        call_tool("submit_step", {"session_id": sid, "step_id": step_id, "content": f"content-{step_id}"})
    return sid


class TestReviseStep:
    def test_revise_resets_current_step(self):
        sid = _fresh_session(steps_to_complete=CODING_STEPS[:3])  # discuss, plan, execute done
        r = call_tool("revise_step", {"session_id": sid, "step_id": "plan"})
        assert r["revised_step"]["id"] == "plan"
        # current_step should now be "plan"
        s = call_tool("get_state", {"session_id": sid})
        assert s["current_step"]["id"] == "plan"

    def test_revise_deletes_forward_step_data(self):
        sid = _fresh_session(steps_to_complete=CODING_STEPS[:4])  # discuss..review done
        r = call_tool("revise_step", {"session_id": sid, "step_id": "plan"})
        assert "execute" in r["invalidated_steps"]
        assert "review" in r["invalidated_steps"]
        s = call_tool("get_state", {"session_id": sid})
        assert "execute" not in s["step_data"]
        assert "review" not in s["step_data"]
        # discuss and plan data preserved
        assert "discuss" in s["step_data"]
        assert "plan" in s["step_data"]

    def test_revise_returns_previous_content(self):
        sid = _fresh_session(steps_to_complete=CODING_STEPS[:2])
        r = call_tool("revise_step", {"session_id": sid, "step_id": "discuss"})
        assert r["previous_content"] == "content-discuss"

    def test_revise_uncompleted_step_errors(self):
        sid = _fresh_session(steps_to_complete=CODING_STEPS[:2])  # discuss, plan done
        r = call_tool("revise_step", {"session_id": sid, "step_id": "execute"})
        assert "error" in r
        assert "not been completed" in r["error"]

    def test_revise_nonexistent_step_errors(self):
        sid = _fresh_session(steps_to_complete=CODING_STEPS[:1])
        r = call_tool("revise_step", {"session_id": sid, "step_id": "bogus"})
        assert "error" in r
        assert "not found" in r["error"]

    def test_revise_completed_workflow_uncompletes(self):
        sid = _fresh_session(steps_to_complete=CODING_STEPS)  # all done
        # Workflow should be complete
        s = call_tool("get_state", {"session_id": sid})
        assert s["current_step"] is None  # __complete__ means no current step found
        # Revise early step
        r = call_tool("revise_step", {"session_id": sid, "step_id": "discuss"})
        assert r["revised_step"]["id"] == "discuss"
        s = call_tool("get_state", {"session_id": sid})
        assert s["current_step"]["id"] == "discuss"
        # All forward data gone
        assert "plan" not in s["step_data"]
        assert "deliver" not in s["step_data"]

    def test_revise_essay_workflow(self):
        """Cross-workflow: essay type uses same semantics."""
        sid = _fresh_session(workflow="essay", steps_to_complete=ESSAY_STEPS[:4])
        r = call_tool("revise_step", {"session_id": sid, "step_id": "commit"})
        assert r["revised_step"]["id"] == "commit"
        assert "structure" in r["invalidated_steps"]
        assert "draft" in r["invalidated_steps"]
        s = call_tool("get_state", {"session_id": sid})
        assert s["current_step"]["id"] == "commit"
        assert "explore" in s["step_data"]

    def test_revise_last_completed_step_no_invalidation(self):
        """Revising the most recently completed step invalidates nothing extra."""
        sid = _fresh_session(steps_to_complete=CODING_STEPS[:3])  # current is review
        r = call_tool("revise_step", {"session_id": sid, "step_id": "execute"})
        assert r["invalidated_steps"] == ["review", "iterate", "deliver"]
        s = call_tool("get_state", {"session_id": sid})
        assert s["current_step"]["id"] == "execute"
