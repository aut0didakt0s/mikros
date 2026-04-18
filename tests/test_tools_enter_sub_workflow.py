"""Placeholder tool registration for sub-workflow invocation (M004/S01/T03)."""

import asyncio

from megalos_server.main import mcp
from tests.conftest import call_tool


def test_enter_sub_workflow_is_registered():
    tools = asyncio.run(mcp.list_tools())
    names = [t.name for t in tools]
    assert "enter_sub_workflow" in names


def test_enter_sub_workflow_returns_placeholder():
    r = call_tool("enter_sub_workflow", {"parent_session_id": "any", "call_step_id": "any"})
    assert r["code"] == "sub_workflow_runtime_not_implemented"
