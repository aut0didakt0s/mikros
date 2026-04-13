"""End-to-end tests for mikros blog workflow."""

from tests.conftest import call_tool

STEPS = ["angle", "audience", "outline", "draft", "revise", "polish"]


class TestBlogHappyPath:
    def test_happy_path(self):
        r = call_tool("start_workflow", {"workflow_type": "blog", "context": "write about remote work"})
        assert "session_id" in r
        assert r["current_step"]["id"] == "angle"
        assert "Do NOT" in r["directive"]
        sid = r["session_id"]

        for i, step_id in enumerate(STEPS):
            r = call_tool("submit_step", {"session_id": sid, "step_id": step_id, "content": f"blog-{step_id}"})
            assert r["submitted"]["id"] == step_id
            assert r["progress"] == f"step {i + 1} of 6 complete"

            if i < len(STEPS) - 1:
                assert r["next_step"]["id"] == STEPS[i + 1]
                assert "directive" in r
                assert "gates" in r
            else:
                assert r["status"] == "workflow_complete"

        art = call_tool("generate_artifact", {"session_id": sid})
        assert art["output_format"] == "text"
        assert isinstance(art["artifact"], str)
        for step_id in STEPS:
            assert f"blog-{step_id}" in art["artifact"]


class TestBlogDirectiveContent:
    def test_directives_have_do_not(self):
        r = call_tool("start_workflow", {"workflow_type": "blog", "context": "test"})
        assert "PLACEHOLDER" not in r["directive"]
        assert "Do NOT" in r["directive"]
        sid = r["session_id"]

        for step_id in STEPS:
            sub = call_tool("submit_step", {"session_id": sid, "step_id": step_id, "content": "x"})
            if "directive" in sub:
                assert "PLACEHOLDER" not in sub["directive"]
                assert "Do NOT" in sub["directive"]
