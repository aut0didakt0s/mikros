"""Deployable FastMCP stub server.

Four deterministic tools (`echo`, `fail`, `schema_required`, `sleep`) that
cover the outcome classes the megalos MCP client handles. The same tool
definitions back the pytest `mcp_stub_server` fixture and the Horizon-
deployed stub, so nightly live-smoke CI exercises byte-identical behavior.
"""
