"""End-to-end tests for mikros essay workflow."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Remove stale DB before importing (import triggers workflow load)
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "server", "mikros_sessions.db")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

from server.main import mcp  # noqa: E402

STEPS = ["explore", "commit", "structure", "draft", "revise", "polish"]


def _call(tool_name, args):
    """Sync wrapper around mcp.call_tool — returns the structured dict."""
    result = asyncio.run(mcp.call_tool(tool_name, args))
    return result.structured_content


class TestEssayHappyPath:
    """Happy path: start -> submit all 6 steps -> generate text artifact."""

    def test_happy_path(self):
        r = _call("start_workflow", {"workflow_type": "essay", "context": "write about solitude"})
        assert "session_id" in r
        assert r["current_step"]["id"] == "explore"
        assert "Do NOT" in r["directive"]
        sid = r["session_id"]

        for i, step_id in enumerate(STEPS):
            r = _call("submit_step", {"session_id": sid, "step_id": step_id, "content": f"essay-{step_id}"})
            assert r["submitted"]["id"] == step_id
            assert r["progress"] == f"step {i + 1} of 6 complete"

            if i < len(STEPS) - 1:
                assert r["next_step"]["id"] == STEPS[i + 1]
                assert "directive" in r
                assert "gates" in r
            else:
                assert r["status"] == "workflow_complete"

        # Generate artifact — must be text, not structured_code
        art = _call("generate_artifact", {"session_id": sid})
        assert art["output_format"] == "text"
        assert isinstance(art["artifact"], str)
        for step_id in STEPS:
            assert f"essay-{step_id}" in art["artifact"]


class TestEssayOutOfOrder:
    """Out-of-order submission rejected."""

    def test_out_of_order_rejected(self):
        r = _call("start_workflow", {"workflow_type": "essay", "context": "test"})
        sid = r["session_id"]
        # Try submitting 'commit' when 'explore' is current
        r = _call("submit_step", {"session_id": sid, "step_id": "commit", "content": "nope"})
        assert "error" in r
        assert "Out-of-order" in r["error"]
        assert r["expected_step"] == "explore"


class TestEssayDirectiveContent:
    """Verify essay.yaml directives have real content and domain-specific Do NOT rules."""

    def test_directives_have_do_not(self):
        r = _call("start_workflow", {"workflow_type": "essay", "context": "test"})
        assert "PLACEHOLDER" not in r["directive"]
        assert "Do NOT" in r["directive"]
        sid = r["session_id"]

        for step_id in STEPS:
            sub = _call("submit_step", {"session_id": sid, "step_id": step_id, "content": "x"})
            if "directive" in sub:
                assert "PLACEHOLDER" not in sub["directive"]
                assert "Do NOT" in sub["directive"]


class TestEssayTextArtifact:
    """Verify generate_artifact returns concatenated text, not structured_code."""

    def test_text_format(self):
        r = _call("start_workflow", {"workflow_type": "essay", "context": "test"})
        sid = r["session_id"]
        for step_id in STEPS:
            _call("submit_step", {"session_id": sid, "step_id": step_id, "content": f"part-{step_id}"})

        art = _call("generate_artifact", {"session_id": sid})
        assert art["output_format"] == "text"
        assert isinstance(art["artifact"], str)
        # Text format joins with double newline
        assert "part-explore" in art["artifact"]
        assert "part-polish" in art["artifact"]

    def test_auto_format_resolves_to_text(self):
        r = _call("start_workflow", {"workflow_type": "essay", "context": "test"})
        sid = r["session_id"]
        for step_id in STEPS:
            _call("submit_step", {"session_id": sid, "step_id": step_id, "content": "x"})

        # output_format="auto" should resolve to "text" for essay workflow
        art = _call("generate_artifact", {"session_id": sid, "output_format": "auto"})
        assert art["output_format"] == "text"
        assert isinstance(art["artifact"], str)
