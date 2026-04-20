"""Smoke tests for the FastMCP stub server fixture.

Validates the fixture end-to-end: tools/list responds, `echo` round-trips,
and `fail` produces an isError envelope. Teardown correctness is
implicit — the fixture uses a daemon thread, so pytest exiting cleanly
is the signal that nothing leaked.
"""

from __future__ import annotations

import asyncio

from fastmcp import Client

from tests.fixtures.mcp_stub import mcp_stub_server  # noqa: F401  (pytest fixture import)


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_tools_list_responds(mcp_stub_server) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    async def go() -> list[str]:
        async with Client(mcp_stub_server.url) as client:
            tools = await client.list_tools()
            return [t.name for t in tools]

    names = _run(go())
    assert {"echo", "fail", "schema_required", "sleep"}.issubset(set(names))


def test_echo_returns_value(mcp_stub_server) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    async def go() -> str:
        async with Client(mcp_stub_server.url) as client:
            result = await client.call_tool("echo", {"value": "hello"})
            return result.data  # type: ignore[no-any-return]

    assert _run(go()) == "hello"


def test_fail_produces_error_envelope(mcp_stub_server) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    async def go():  # type: ignore[no-untyped-def]
        async with Client(mcp_stub_server.url) as client:
            return await client.call_tool(
                "fail", {"message": "boom"}, raise_on_error=False
            )

    result = _run(go())
    assert result.is_error is True
    # The error message should propagate back in a text content block.
    text_blocks = [c for c in result.content if hasattr(c, "text")]
    assert any("boom" in c.text for c in text_blocks)
