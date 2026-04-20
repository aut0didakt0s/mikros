"""FastMCP stub server fixture for MCP client tests.

Importing this module does NOT start the server. The server starts only
when the `mcp_stub_server` pytest fixture is resolved.
"""

from tests.fixtures.mcp_stub.server import StubServerInfo, mcp_stub_server

__all__ = ["StubServerInfo", "mcp_stub_server"]
