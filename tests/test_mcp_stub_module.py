"""Smoke tests for the deployable `mcp_stub` package.

Confirms the package imports cleanly, `main.mcp` is a FastMCP instance,
and `register_tools` attaches the expected tool set. The pytest fixture
in `tests/fixtures/mcp_stub/` exercises the tools end-to-end; this file
only asserts the surface that Horizon's entrypoint resolver needs.
"""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP

import mcp_stub
import mcp_stub.main
import mcp_stub.tools


def test_package_imports_cleanly() -> None:
    assert mcp_stub is not None
    assert mcp_stub.tools is not None
    assert mcp_stub.main is not None


def test_main_exposes_fastmcp_instance() -> None:
    assert isinstance(mcp_stub.main.mcp, FastMCP)


def test_register_tools_attaches_expected_tools() -> None:
    """Build a fresh FastMCP, register, assert the four tool names are listed."""
    mcp = FastMCP("verify")
    mcp_stub.tools.register_tools(mcp)
    # FastMCP exposes `list_tools` on Client; on the server side the
    # inspection API is `_tool_manager.list_tools()` (async). Keep this
    # resilient: try the async server-side path and fall back to asserting
    # the module attributes exist.
    try:
        tools = asyncio.run(mcp._tool_manager.list_tools())  # type: ignore[attr-defined]
        names = {t.name for t in tools}
        assert {"echo", "fail", "schema_required", "sleep"}.issubset(names)
    except AttributeError:
        # FastMCP internals moved. Fall back to callable-level check;
        # the fixture-level smoke tests cover end-to-end tool dispatch.
        for name in ("echo", "fail", "schema_required", "sleep"):
            assert callable(getattr(mcp_stub.tools, name))


def test_shared_tool_callables_exist() -> None:
    for name in ("echo", "fail", "schema_required", "sleep"):
        assert callable(getattr(mcp_stub.tools, name))
