"""FastMCP stub server + pytest fixture.

A tiny FastMCP server exposing deterministic tools that cover the outcome
classes MCP client tests must handle (success, tool error, schema error,
slow response). TransportError cases are tested elsewhere by pointing a
client at an unreachable URL — no dedicated tool here.

The fixture `mcp_stub_server` starts the server in a background daemon
thread bound to 127.0.0.1 on an OS-assigned free port, waits for the
port to accept connections, and yields a `StubServerInfo` with `url`
and `port`. Session-scoped: FastMCP HTTP startup is non-trivial and the
stub is stateless, so one server per session is sufficient. Daemon
thread means teardown is automatic on process exit.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import NamedTuple

import pytest  # type: ignore[import-not-found]
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError


class StubServerInfo(NamedTuple):
    url: str
    port: int


def _build_stub() -> FastMCP:
    """Build a fresh FastMCP instance with the five deterministic tools."""
    mcp = FastMCP("stub")

    @mcp.tool
    def echo(value: str) -> str:
        """Return `value` unchanged."""
        return value

    @mcp.tool
    def fail(message: str) -> str:
        """Raise a ToolError with the given message (maps to isError envelope)."""
        raise ToolError(message)

    @mcp.tool
    def schema_required(count: int) -> str:
        """Accept an int. Clients passing a non-int trip server-side schema validation."""
        return f"count={count}"

    @mcp.tool
    def sleep(seconds: float) -> str:
        """Sleep `seconds` then return. Used by client-side timeout tests."""
        time.sleep(seconds)
        return f"slept={seconds}"

    return mcp


def _pick_free_port() -> int:
    """Ask the kernel for an unused port bound to 127.0.0.1, then release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> None:
    """Poll `host:port` until it accepts a TCP connection or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"stub FastMCP server did not open {host}:{port} within {timeout}s")


@pytest.fixture(scope="session")
def mcp_stub_server() -> StubServerInfo:  # type: ignore[misc]
    """Start the stub FastMCP server on a random free port; yield its URL+port.

    Session-scoped. The server runs in a daemon thread, so it dies when the
    pytest process exits — no explicit shutdown required.
    """
    port = _pick_free_port()
    mcp = _build_stub()

    def _serve() -> None:
        # FastMCP `run` is synchronous and creates its own event loop.
        # Running it in a daemon thread keeps it off the main thread and
        # ensures it dies with the process.
        mcp.run(transport="http", host="127.0.0.1", port=port, show_banner=False)

    thread = threading.Thread(target=_serve, name="mcp-stub-server", daemon=True)
    thread.start()

    _wait_for_port("127.0.0.1", port)

    return StubServerInfo(url=f"http://127.0.0.1:{port}/mcp/", port=port)
