"""Tool definitions for the deployable FastMCP stub.

Single source of truth for the four stub tools. Both `mcp_stub.main` (the
Horizon entrypoint) and the pytest fixture in `tests/fixtures/mcp_stub/`
attach these via `register_tools(mcp)` so test-suite stub and deployed
stub remain byte-identical.
"""

from __future__ import annotations

import time

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError


def echo(value: str) -> str:
    """Return `value` unchanged."""
    return value


def fail(message: str) -> str:
    """Raise a ToolError with the given message (maps to isError envelope)."""
    raise ToolError(message)


def schema_required(count: int) -> str:
    """Accept an int. Clients passing a non-int trip server-side schema validation."""
    return f"count={count}"


def sleep(seconds: float) -> str:
    """Sleep `seconds` then return. Used by client-side timeout tests."""
    time.sleep(seconds)
    return f"slept={seconds}"


def register_tools(mcp: FastMCP) -> None:
    """Attach the four stub tools to an existing FastMCP instance.

    Used by both `mcp_stub.main` (deployed server) and the pytest fixture
    (in-process server) so the two exercise identical tool definitions.
    """
    mcp.tool(echo)
    mcp.tool(fail)
    mcp.tool(schema_required)
    mcp.tool(sleep)
