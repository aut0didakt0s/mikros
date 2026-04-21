"""End-to-end rate-limit integration tests.

Exercises the full stack via the ``call_tool`` harness against the
running FastMCP app. Verifies:

- Normal polling under per-session sustained rate succeeds indefinitely.
- Burst-then-pause: N rapid calls succeed, the N+1st denies with a
  positive ``retry_after_ms``, and calls succeed again after the pause.
- Scope is populated correctly on each axis's deny path.
- Session-create gating fires ahead of other axes on ``start_workflow``.
- Cross-session: denying on session A does not affect session B.
- Deny WARN log is deduped: a sustained burst produces one line per
  (scope, identity, 60s window).

Fake-clock injection: we swap the ``RateLimitMiddleware`` instance's
``_limiter`` for a fresh ``RateLimiter`` using a test-owned monotonic
callable. Restores the original limiter on teardown so the mutation
does not leak to later tests.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterator

import pytest  # type: ignore[import-not-found]

from megalos_server import state
from megalos_server.main import mcp
from megalos_server.middleware import RateLimitMiddleware
from megalos_server.ratelimit import (
    AXIS_IP,
    AXIS_IP_SESSION_CREATE,
    AXIS_SESSION,
    RateLimitConfig,
    RateLimiter,
    _reset_deny_log_cache_for_test,
)
from tests.conftest import call_tool


# ---------------------------------------------------------------------------
# Fake-clock + limiter swap fixture
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


def _find_rate_limit_middleware() -> RateLimitMiddleware:
    for m in mcp.middleware:  # type: ignore[attr-defined]
        if isinstance(m, RateLimitMiddleware):
            return m
    raise RuntimeError("RateLimitMiddleware not registered on mcp app")


@pytest.fixture
def swap_limiter() -> Iterator[Callable[[RateLimitConfig, Callable[[], float]], RateLimiter]]:
    """Swap the middleware's limiter with one built from a test-supplied
    config + monotonic. Restores the original limiter on teardown.
    Resets the deny-log dedupe cache so dedupe state does not leak across
    tests."""
    _reset_deny_log_cache_for_test()
    mw = _find_rate_limit_middleware()
    original = mw._limiter
    new_limiter_ref: dict[str, Any] = {}

    def _install(config: RateLimitConfig, clock: Callable[[], float]) -> RateLimiter:
        limiter = RateLimiter(config, monotonic=clock)
        mw._limiter = limiter
        new_limiter_ref["limiter"] = limiter
        return limiter

    try:
        yield _install
    finally:
        mw._limiter = original
        _reset_deny_log_cache_for_test()


def _patch_http_ip(monkeypatch: pytest.MonkeyPatch, ip: str | None) -> None:
    """Patch transport + IP extraction so the middleware treats the
    in-process call_tool dispatch as HTTP from ``ip``."""
    import megalos_server.middleware as mw_mod

    monkeypatch.setattr(mw_mod, "_extract_ip", lambda ctx: ip)
    monkeypatch.setattr(mw_mod, "_detect_transport", lambda ctx: "http" if ip else "stdio")


# ---------------------------------------------------------------------------
# Normal polling — stays under per-session sustained rate
# ---------------------------------------------------------------------------


def test_steady_polling_under_session_rate_succeeds(swap_limiter):
    clock = FakeClock()
    # Defaults: session rate 2/s, burst 30. 1 call per second for 60s.
    cfg = RateLimitConfig(session_rate=2.0, session_burst=30.0)
    swap_limiter(cfg, clock)
    state.clear_sessions()
    r = call_tool("start_workflow", {"workflow_type": "canonical", "context": "poll"})
    sid = r["session_id"]
    for _ in range(60):
        clock.advance(1.0)
        r = call_tool("get_state", {"session_id": sid})
        assert r.get("status") != "error", r
        assert r["session_id"] == sid


# ---------------------------------------------------------------------------
# Burst then pause: Nth denies, N+1 after refill succeeds again
# ---------------------------------------------------------------------------


def test_session_burst_then_pause_then_success(swap_limiter):
    clock = FakeClock()
    cfg = RateLimitConfig(session_rate=2.0, session_burst=30.0)
    swap_limiter(cfg, clock)
    state.clear_sessions()
    r = call_tool("start_workflow", {"workflow_type": "canonical", "context": "burst"})
    sid = r["session_id"]
    # start_workflow has no session_id arg -> session axis NOT consulted,
    # so the session bucket is untouched after creation. Fire 30 rapid
    # get_state calls on sid: burst=30 -> all succeed.
    for i in range(30):
        r = call_tool("get_state", {"session_id": sid})
        assert r.get("status") != "error", f"call {i} unexpectedly denied: {r}"
    # 31st call exhausts the bucket -> denied with retry_after_ms > 0.
    r = call_tool("get_state", {"session_id": sid})
    assert r["status"] == "error"
    assert r["code"] == "rate_limited"
    assert r["scope"] == AXIS_SESSION
    assert r["retry_after_ms"] > 0
    # Pause long enough to refill at least one token (rate 2/s -> 0.5s).
    clock.advance(1.0)
    r = call_tool("get_state", {"session_id": sid})
    assert r.get("status") != "error"


# ---------------------------------------------------------------------------
# Scope populates correctly on each axis
# ---------------------------------------------------------------------------


def test_deny_scope_session(swap_limiter):
    clock = FakeClock()
    cfg = RateLimitConfig(session_rate=1.0, session_burst=1.0)
    swap_limiter(cfg, clock)
    state.clear_sessions()
    r = call_tool("start_workflow", {"workflow_type": "canonical", "context": "x"})
    sid = r["session_id"]
    # burst=1: first get_state consumes the only token, second denies.
    r = call_tool("get_state", {"session_id": sid})
    assert r.get("status") != "error"
    r = call_tool("get_state", {"session_id": sid})
    assert r["status"] == "error"
    assert r["code"] == "rate_limited"
    assert r["scope"] == AXIS_SESSION
    # session_fingerprint present, raw session_id absent.
    assert "session_fingerprint" in r
    assert r["session_fingerprint"] != sid
    assert "session_id" not in r


def test_deny_scope_ip(swap_limiter, monkeypatch):
    clock = FakeClock()
    # Session budget huge; IP budget tiny so IP axis trips first.
    cfg = RateLimitConfig(
        session_rate=1000.0,
        session_burst=1000.0,
        ip_rate=1.0,
        ip_burst=1.0,
        ip_create_rate=1000.0,
        ip_create_burst=1000.0,
    )
    swap_limiter(cfg, clock)
    _patch_http_ip(monkeypatch, "10.0.0.1")
    state.clear_sessions()
    r = call_tool("start_workflow", {"workflow_type": "canonical", "context": "x"})
    assert r.get("status") != "error", r
    sid = r["session_id"]
    # IP bucket drained by start_workflow -> next get_state denies on IP.
    r = call_tool("get_state", {"session_id": sid})
    assert r["status"] == "error"
    assert r["code"] == "rate_limited"
    assert r["scope"] == AXIS_IP
    assert r["retry_after_ms"] > 0
    # IP-axis envelope MUST NOT expose IP / fingerprint in response.
    assert "ip" not in r
    assert "ip_fingerprint" not in r
    assert "session_fingerprint" not in r


def test_deny_scope_ip_session_create(swap_limiter, monkeypatch):
    clock = FakeClock()
    # ip_session_create burst = 10, so 10 start_workflow calls from the
    # same IP in one second succeed and the 11th denies. Session cap is 5
    # active rows, so we delete each session after creation to keep the
    # session-cap side gate quiet and isolate the ip_session_create axis.
    cfg = RateLimitConfig(
        session_rate=1000.0,
        session_burst=1000.0,
        ip_rate=1000.0,
        ip_burst=1000.0,
        ip_create_rate=1.0,
        ip_create_burst=10.0,
    )
    swap_limiter(cfg, clock)
    _patch_http_ip(monkeypatch, "10.0.0.2")
    state.clear_sessions()
    rate_limited_denies = 0
    rate_limited_first: dict[str, Any] | None = None
    for i in range(11):
        r = call_tool("start_workflow", {"workflow_type": "canonical", "context": f"c{i}"})
        if r.get("status") == "error" and r.get("code") == "rate_limited":
            rate_limited_denies += 1
            if rate_limited_first is None:
                rate_limited_first = r
        elif r.get("session_id"):
            # Free the session row so we don't hit the 5-session cap.
            call_tool("delete_session", {"session_id": r["session_id"]})
    # 10 succeed (burst), 11th denies on ip_session_create.
    assert rate_limited_denies == 1
    assert rate_limited_first is not None
    assert rate_limited_first["code"] == "rate_limited"
    assert rate_limited_first["scope"] == AXIS_IP_SESSION_CREATE
    assert rate_limited_first["retry_after_ms"] > 0


# ---------------------------------------------------------------------------
# Cross-session isolation (per-session axis)
# ---------------------------------------------------------------------------


def test_cross_session_isolation(swap_limiter):
    clock = FakeClock()
    cfg = RateLimitConfig(session_rate=1.0, session_burst=1.0)
    swap_limiter(cfg, clock)
    state.clear_sessions()
    r_a = call_tool("start_workflow", {"workflow_type": "canonical", "context": "A"})
    sid_a = r_a["session_id"]
    r_b = call_tool("start_workflow", {"workflow_type": "canonical", "context": "B"})
    sid_b = r_b["session_id"]
    # Drain A's bucket: burst=1 so the first get_state consumes, next denies.
    r = call_tool("get_state", {"session_id": sid_a})
    assert r.get("status") != "error"
    r = call_tool("get_state", {"session_id": sid_a})
    assert r["status"] == "error"
    assert r["scope"] == AXIS_SESSION
    # B's bucket is independent — burst=1 still available.
    r = call_tool("get_state", {"session_id": sid_b})
    assert r.get("status") != "error"
    assert r["session_id"] == sid_b


# ---------------------------------------------------------------------------
# WARN log dedupe — one line per (scope, identity, 60s window)
# ---------------------------------------------------------------------------


def test_warn_log_deduped_under_sustained_deny(swap_limiter, caplog):
    clock = FakeClock()
    cfg = RateLimitConfig(session_rate=0.001, session_burst=1.0)
    swap_limiter(cfg, clock)
    state.clear_sessions()
    r = call_tool("start_workflow", {"workflow_type": "canonical", "context": "flood"})
    sid = r["session_id"]

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="megalos_server.ratelimit"):
        # 100 denies over a simulated 10 seconds; dedupe window is 60s so
        # exactly ONE WARN line should emit for this (scope, identity).
        for i in range(100):
            clock.advance(0.1)
            r = call_tool("get_state", {"session_id": sid})
            # Most of these deny; sparse refill may allow a handful, but
            # every deny call path funnels through the same dedupe key.

    deny_records = [
        rec for rec in caplog.records if rec.getMessage() == "rate_limit_exceeded"
    ]
    assert len(deny_records) == 1, (
        f"expected exactly one deduped WARN line, got {len(deny_records)}"
    )
    rec = deny_records[0]
    # Structured fields carried via logging.extra -> attribute access.
    assert rec.levelno == logging.WARNING
    assert getattr(rec, "event", None) == "rate_limit_exceeded"
    assert getattr(rec, "scope", None) == AXIS_SESSION
    # Session-axis log identity is session_fingerprint, never raw session_id.
    fp = getattr(rec, "session_fingerprint", None)
    assert fp is not None
    assert fp != sid
    # retry_after_ms present and positive.
    assert getattr(rec, "retry_after_ms", 0) > 0
