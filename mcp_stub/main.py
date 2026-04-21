"""Horizon entrypoint for the MCP stub server.

Exposes `mcp` at module level so `fastmcp inspect mcp_stub/main.py:mcp`
and the Horizon web UI (entrypoint = `mcp_stub/main.py:mcp`) both
resolve the same FastMCP instance. Deploy with auth off — the stub is
intentionally unauthenticated so nightly smoke CI can call it without
a secret token.
"""

from __future__ import annotations

from fastmcp import FastMCP

from mcp_stub.tools import register_tools

mcp = FastMCP("mcp-stub")
register_tools(mcp)


if __name__ == "__main__":
    # FastMCP reads FASTMCP_HOST / FASTMCP_PORT from env automatically.
    mcp.run(transport="streamable-http")  # type: ignore[arg-type]
