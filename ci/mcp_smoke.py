"""Nightly advisory live-smoke for the deployed MCP stub.

Exercises the full HTTP -> FastMCP -> tools/call -> envelope path against
the stub URL pinned via the `MCP_STUB_URL` environment variable. Success
is an `Ok` outcome whose value round-trips the `echo` argument. Failure
is non-blocking: the GitHub Actions workflow that invokes this script is
NOT listed as a required check; a red run indicates live-path drift
between the client and the deployed stub, not a release blocker.

Not a pytest test. Lives outside `tests/` and does not import pytest.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict

from megalos_server import mcp_client
from megalos_server.mcp_registry import AuthConfig, Registry, ServerConfig


_TOKEN_ENV = "MCP_STUB_TOKEN_UNUSED"


def _load_registry() -> Registry:
    url = os.environ.get("MCP_STUB_URL")
    if not url:
        sys.stderr.write(
            "error: MCP_STUB_URL env var is required. "
            "Set the GitHub secret MCP_STUB_URL to the deployed Horizon URL, "
            "e.g. https://<stub>.fastmcp.app/mcp\n"
        )
        sys.exit(2)
    # The stub deploys with auth off, but the registry schema requires an
    # auth block and the client unconditionally resolves token_env before
    # each call. Supply a dummy Bearer token so the call dispatches; the
    # server ignores the header when auth is off.
    os.environ.setdefault(_TOKEN_ENV, "unused")
    cfg = ServerConfig(
        name="stub",
        url=url,
        transport="http",
        auth=AuthConfig(type="bearer", token_env=_TOKEN_ENV),
        timeout_default=10.0,
    )
    return Registry(servers={"stub": cfg})


def main() -> int:
    registry = _load_registry()
    outcome = mcp_client.call("stub", "echo", {"value": "ci-smoke"}, registry)
    envelope = {"kind": outcome.kind, **asdict(outcome)}
    print(json.dumps(envelope, default=str, indent=2))
    if getattr(outcome, "kind", None) != "ok":
        print(f"RESULT: FAIL ({outcome.kind})")
        return 1
    if getattr(outcome, "value", None) != "ci-smoke":
        print(f"RESULT: FAIL (ok but value mismatch: {outcome.value!r})")
        return 1
    print("RESULT: PASS (ok)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
