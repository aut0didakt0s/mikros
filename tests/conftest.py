"""Shared test fixtures for mikros MCP tests."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Remove stale DB before importing (import triggers workflow load)
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "server", "mikros_sessions.db")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

from server.main import mcp  # noqa: E402


def call_tool(tool_name, args):
    """Sync wrapper around mcp.call_tool — returns the structured dict."""
    result = asyncio.run(mcp.call_tool(tool_name, args))
    return result.structured_content
