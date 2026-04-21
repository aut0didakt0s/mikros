"""Transport-routing tests for RateLimitMiddleware.

We construct a stub MiddlewareContext and a spy RateLimiter to verify
axis routing without spinning up a full FastMCP app. The goal is to
exercise the on_call_tool decision tree: stdio skips IP, HTTP consults
session + IP, session-create (start_workflow) on HTTP additionally
consults ip_session_create, missing session_id falls back to IP-only.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest  # type: ignore[import-not-found]

from megalos_server.middleware import RateLimitMiddleware, _SESSION_CREATE_TOOL
from megalos_server.ratelimit import (
    AXIS_IP,
    AXIS_IP_SESSION_CREATE,
    AXIS_SESSION,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class SpyLimiter:
    """Records axis calls; all attempts allowed unless ``deny`` set."""

    def __init__(self, deny: set[str] | None = None):
        self.calls: list[tuple[str, str, int]] = []
        self._deny = deny or set()

    def try_consume(self, axis: str, key: str, cost: int = 1) -> tuple[bool, float]:
        self.calls.append((axis, key, cost))
        if axis in self._deny:
            return (False, 1234.5)
        return (True, 0.0)


@dataclass
class StubMessage:
    name: str
    arguments: dict[str, Any]


class StubFastMCPContext:
    def __init__(self, transport: str | None):
        self.transport = transport
        self._state: dict[str, Any] = {}

    def set_state(self, key: str, value: Any) -> None:
        self._state[key] = value


@dataclass
class StubMiddlewareContext:
    message: StubMessage
    fastmcp_context: StubFastMCPContext | None


async def _noop_call_next(_ctx: Any) -> str:
    return "ok"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mw(limiter: Any) -> RateLimitMiddleware:
    return RateLimitMiddleware(limiter)


def _ctx(
    tool: str,
    arguments: dict[str, Any] | None = None,
    transport: str | None = "streamable-http",
) -> StubMiddlewareContext:
    return StubMiddlewareContext(
        message=StubMessage(name=tool, arguments=arguments or {}),
        fastmcp_context=StubFastMCPContext(transport=transport) if transport else None,
    )


def _patch_ip(monkeypatch: pytest.MonkeyPatch, ip: str | None) -> None:
    """Patch _extract_ip to return a deterministic IP (or None)."""
    import megalos_server.middleware as mw_mod

    monkeypatch.setattr(mw_mod, "_extract_ip", lambda ctx: ip)


def _run(coro) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_stdio_skips_ip_axis(monkeypatch):
    spy = SpyLimiter()
    mw = _mw(spy)
    ctx = _ctx("get_state", {"session_id": "s1"}, transport="stdio")
    _patch_ip(monkeypatch, "1.2.3.4")  # should be ignored on stdio
    result = _run(mw.on_call_tool(ctx, _noop_call_next))
    assert result == "ok"
    axes = [c[0] for c in spy.calls]
    assert AXIS_IP not in axes
    assert AXIS_IP_SESSION_CREATE not in axes
    assert AXIS_SESSION in axes


def test_http_consults_session_and_ip(monkeypatch):
    spy = SpyLimiter()
    mw = _mw(spy)
    ctx = _ctx("get_state", {"session_id": "s1"}, transport="streamable-http")
    _patch_ip(monkeypatch, "1.2.3.4")
    _run(mw.on_call_tool(ctx, _noop_call_next))
    axes = [c[0] for c in spy.calls]
    assert AXIS_SESSION in axes
    assert AXIS_IP in axes
    # Not a session-create tool -> no ip_session_create.
    assert AXIS_IP_SESSION_CREATE not in axes


def test_http_start_workflow_adds_ip_session_create(monkeypatch):
    spy = SpyLimiter()
    mw = _mw(spy)
    # start_workflow doesn't take a session_id; args carry workflow_type + context.
    ctx = _ctx(
        _SESSION_CREATE_TOOL,
        {"workflow_type": "canonical", "context": "hello"},
        transport="streamable-http",
    )
    _patch_ip(monkeypatch, "1.2.3.4")
    _run(mw.on_call_tool(ctx, _noop_call_next))
    axes = [c[0] for c in spy.calls]
    # ip_session_create must be FIRST (per plan ordering).
    assert axes[0] == AXIS_IP_SESSION_CREATE
    assert AXIS_IP in axes
    # No session_id on start_workflow args -> session axis skipped.
    assert AXIS_SESSION not in axes


def test_missing_session_id_http_falls_back_to_ip_only(monkeypatch):
    # list_workflows takes no session_id; HTTP transport => IP-axis only.
    spy = SpyLimiter()
    mw = _mw(spy)
    ctx = _ctx("list_workflows", {}, transport="streamable-http")
    _patch_ip(monkeypatch, "9.9.9.9")
    _run(mw.on_call_tool(ctx, _noop_call_next))
    axes = [c[0] for c in spy.calls]
    assert AXIS_IP in axes
    assert AXIS_SESSION not in axes
    assert AXIS_IP_SESSION_CREATE not in axes


def test_missing_session_id_stdio_skips_everything(monkeypatch):
    # Stdio + no session_id => nothing to gate on (pass-through).
    spy = SpyLimiter()
    mw = _mw(spy)
    ctx = _ctx("list_workflows", {}, transport="stdio")
    _patch_ip(monkeypatch, None)
    result = _run(mw.on_call_tool(ctx, _noop_call_next))
    assert result == "ok"
    assert spy.calls == []


def test_http_without_extractable_ip_skips_ip_axis(monkeypatch):
    # HTTP but no client.host -> IP extraction returns None; skip IP axes.
    spy = SpyLimiter()
    mw = _mw(spy)
    ctx = _ctx("get_state", {"session_id": "s1"}, transport="streamable-http")
    _patch_ip(monkeypatch, None)
    _run(mw.on_call_tool(ctx, _noop_call_next))
    axes = [c[0] for c in spy.calls]
    assert axes == [AXIS_SESSION]


def test_deny_attaches_metadata_but_still_calls_next(monkeypatch):
    # T01 contract: denial is observation-only. call_next still fires;
    # deny metadata is attached to fastmcp_context state for T02.
    spy = SpyLimiter(deny={AXIS_SESSION})
    mw = _mw(spy)
    ctx = _ctx("get_state", {"session_id": "s1"}, transport="stdio")
    _patch_ip(monkeypatch, None)
    result = _run(mw.on_call_tool(ctx, _noop_call_next))
    assert result == "ok"
    assert ctx.fastmcp_context is not None
    metadata = ctx.fastmcp_context._state.get("rate_limit_denied")
    assert metadata == {"scope": AXIS_SESSION, "retry_after_ms": 1234.5}


def test_unknown_transport_treated_as_non_http(monkeypatch):
    # A transport string the middleware doesn't recognize shouldn't crash;
    # it's bucketed as "unknown" -> no IP gating, session axis still applies.
    spy = SpyLimiter()
    mw = _mw(spy)
    ctx = _ctx("get_state", {"session_id": "s1"}, transport="ws-experimental")
    _patch_ip(monkeypatch, "1.2.3.4")
    _run(mw.on_call_tool(ctx, _noop_call_next))
    axes = [c[0] for c in spy.calls]
    assert AXIS_SESSION in axes
    assert AXIS_IP not in axes


def test_null_fastmcp_context_passes_through(monkeypatch):
    # context.fastmcp_context is None in some in-process test paths.
    # Middleware must not crash; transport defaults to unknown, no IP,
    # session gated only if session_id present.
    spy = SpyLimiter()
    mw = _mw(spy)
    ctx = StubMiddlewareContext(
        message=StubMessage(name="get_state", arguments={"session_id": "s1"}),
        fastmcp_context=None,
    )
    _patch_ip(monkeypatch, None)
    result = _run(mw.on_call_tool(ctx, _noop_call_next))
    assert result == "ok"
    axes = [c[0] for c in spy.calls]
    assert axes == [AXIS_SESSION]
