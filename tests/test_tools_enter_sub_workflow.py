"""Tool registration + error-envelope smoke for enter_sub_workflow (M004/S01/T03 + S02/T01)."""

import asyncio

from megalos_server.main import mcp
from tests.conftest import call_tool


def test_enter_sub_workflow_is_registered():
    tools = asyncio.run(mcp.list_tools())
    names = [t.name for t in tools]
    assert "enter_sub_workflow" in names


def test_enter_sub_workflow_rejects_bogus_parent():
    r = call_tool("enter_sub_workflow", {"parent_session_id": "nope", "call_step_id": "any"})
    assert r["code"] == "session_not_found"
