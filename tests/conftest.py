"""Shared test fixtures for mikros MCP tests."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.main import mcp  # noqa: E402


def call_tool(tool_name, args):
    """Sync wrapper around mcp.call_tool — returns the structured dict."""
    result = asyncio.run(mcp.call_tool(tool_name, args))
    return result.structured_content
