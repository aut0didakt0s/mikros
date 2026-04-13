"""End-to-end tests for research synthesis workflow."""

from tests.conftest import call_tool

STEPS = ["frame", "gather", "evaluate", "synthesize", "structure", "refine"]


class TestFullWorkflow:
    """Happy path: start -> submit all 6 steps -> generate text artifact."""

    def test_happy_path(self):
        r = call_tool("start_workflow", {"workflow_type": "research", "context": "impact of sleep on memory"})
        assert "session_id" in r
        assert r["current_step"]["id"] == "frame"
        assert "Do NOT" in r["directive"]
        sid = r["session_id"]

        for i, step_id in enumerate(STEPS):
            r = call_tool("submit_step", {"session_id": sid, "step_id": step_id, "content": f"content-{step_id}"})
            assert r["submitted"]["id"] == step_id
            assert r["progress"] == f"step {i + 1} of 6 complete"

            if i < len(STEPS) - 1:
                assert r["next_step"]["id"] == STEPS[i + 1]
                assert "directive" in r
                assert "gates" in r
            else:
                assert r["status"] == "workflow_complete"

        st = call_tool("get_state", {"session_id": sid})
        assert len(st["step_data"]) == 6

        art = call_tool("generate_artifact", {"session_id": sid})
        assert art["output_format"] == "text"
        assert isinstance(art["artifact"], str)


class TestDirectiveContent:
    """Verify research.yaml directives are real, not placeholders."""

    def test_no_placeholders(self):
        r = call_tool("start_workflow", {"workflow_type": "research", "context": "test"})
        assert "PLACEHOLDER" not in r["directive"]
        sid = r["session_id"]
        for step_id in STEPS:
            sub = call_tool("submit_step", {"session_id": sid, "step_id": step_id, "content": "x"})
            if "directive" in sub:
                assert "PLACEHOLDER" not in sub["directive"]
                assert "Do NOT" in sub["directive"]
