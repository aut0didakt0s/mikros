"""Adversarial rate-limit suite + tool-surface classification regression.

Discovery-shaped tests over the assembled middleware + primitive + tool-
surface composition. Framed per `project_adversarial_suite_value.md`: goal
is surfacing surprises, not a green bar. Findings land as
``@pytest.mark.xfail(strict=True)`` here; the remediation PR flips them.

Scope rule (mirrors capability-token suite): these tests verify the
rate-limit model holds — token-bucket keyed on capability identity bounds
per-actor throughput. We assert which axis a request lands on and which
bucket denies it, not that "attacker attempts fail."

Seven sections:

  (a) Rate-limit correctness under hostile conditions
      Clock skew at MIDDLEWARE scope (T01 covers primitive scope),
      burst-cliff, SESSION_CAP-pressure + ip_session_create interaction.

  (b) Harvest resistance
      IP axis throttles a list_workflows tight-loop; per-session axis
      gates cross-session polling uniformly.

  (c) Transport bypass attempts
      stdio skips the per-IP axis (spy verification). X-Forwarded-For
      is future-work (T02 did not land proxy support). HTTP with no
      identity → denial/pass behavior matches T01 decision.

  (d) Memory/LRU behavior under rotating-IP attack
      100K unique IPs, one call each — IP store stays <= cap; idle
      sweep drops aged entries.

  (e) Dedupe correctness
      100 denies/1s/one identity → 1 WARN; 100 across 10 identities →
      10 WARNs; cache at cap → LRU eviction.

  (f) Session-id identity-space bypass surfaces
      Empty / whitespace / case / NFC-vs-NFD / pathological length /
      post-guard coverage. General property: limiter identity is a
      strict refinement of the workflow layer's identity. Findings
      xfail; remediation PR adds shared ``normalize_session_id``
      (fix-shape per T03-PLAN).

  (g) Tool-surface classification regression
      Machine-readable drift guard: the registered tool set must equal
      the classification table, and every ``session_creating`` tool
      must appear in the middleware's session-create allowlist.
"""

from __future__ import annotations

import asyncio
import logging
import tracemalloc
import unicodedata
from typing import Any, Callable, Iterator, Literal

import pytest  # type: ignore[import-not-found]

from megalos_server import state
from megalos_server.main import mcp
from megalos_server.middleware import (
    RateLimitMiddleware,
    _SESSION_CREATE_TOOL,
)
from megalos_server.ratelimit import (
    AXIS_IP,
    AXIS_IP_SESSION_CREATE,
    AXIS_SESSION,
    RateLimitConfig,
    RateLimiter,
    _DenyLogDedupe,
    _IpStore,
    _reset_deny_log_cache_for_test,
    emit_rate_limit_warn,
)
from tests.conftest import call_tool


# ---------------------------------------------------------------------------
# Fake-clock + limiter swap fixture (mirrors test_ratelimit_integration.py)
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
    """Swap the middleware's limiter with a test-owned one. Restores on teardown."""
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
    """Patch the middleware to treat in-process call_tool as HTTP from ``ip``."""
    import megalos_server.middleware as mw_mod

    monkeypatch.setattr(mw_mod, "_extract_ip", lambda ctx: ip)
    monkeypatch.setattr(mw_mod, "_detect_transport", lambda ctx: "http" if ip else "stdio")


# ===========================================================================
# (a) RATE-LIMIT CORRECTNESS UNDER HOSTILE CONDITIONS
# ===========================================================================
# Middleware-scope skew + burst-cliff + SESSION_CAP/ip_session_create interaction.
# T01's primitive-scope skew tests cover backward-jump clamping and zero-elapsed
# math; the tests here exercise the assembled middleware path: does a client
# honoring retry_after_ms recover cleanly, does exactly-at-burst allow and
# burst+1 deny, does ip_session_create continue to bound allocation under
# SESSION_CAP churn.


def test_retry_after_ms_honored_honestly_at_middleware_scope(swap_limiter):
    """Property: a client that sleeps for retry_after_ms and retries succeeds.

    Middleware-scope: differs from T01 primitive tests because it flows
    through the assembled on_call_tool path, including envelope
    construction and dedupe interaction. Verifies the retry_after_ms
    value the envelope advertises is an honest floor — client that
    honors it does not re-deny immediately."""
    clock = FakeClock()
    cfg = RateLimitConfig(session_rate=2.0, session_burst=2.0)
    swap_limiter(cfg, clock)
    state.clear_sessions()
    r = call_tool("start_workflow", {"workflow_type": "canonical", "context": "r"})
    sid = r["session_id"]
    # Drain the burst.
    assert call_tool("get_state", {"session_id": sid}).get("status") != "error"
    assert call_tool("get_state", {"session_id": sid}).get("status") != "error"
    # Next denies with retry_after_ms > 0.
    denied = call_tool("get_state", {"session_id": sid})
    assert denied["status"] == "error"
    assert denied["code"] == "rate_limited"
    retry_sec = denied["retry_after_ms"] / 1000.0
    # Honest honor: sleep exactly retry_after, then retry. Must succeed.
    clock.advance(retry_sec)
    ok = call_tool("get_state", {"session_id": sid})
    assert ok.get("status") != "error", ok


def test_burst_cliff_exact_then_denial_then_refill(swap_limiter):
    """Property: exactly burst calls allowed; burst+1 denies; after one
    refill period a single call succeeds again. The 'cliff' shape of
    token-bucket semantics — no partial denial between tokens."""
    clock = FakeClock()
    cfg = RateLimitConfig(session_rate=1.0, session_burst=5.0)
    swap_limiter(cfg, clock)
    state.clear_sessions()
    r = call_tool("start_workflow", {"workflow_type": "canonical", "context": "cliff"})
    sid = r["session_id"]
    for i in range(5):
        resp = call_tool("get_state", {"session_id": sid})
        assert resp.get("status") != "error", f"call {i} denied unexpectedly: {resp}"
    # 6th call: cliff.
    denied = call_tool("get_state", {"session_id": sid})
    assert denied["status"] == "error"
    assert denied["scope"] == AXIS_SESSION
    # One second passes at refill_rate=1/s -> one token regenerates.
    clock.advance(1.0)
    ok = call_tool("get_state", {"session_id": sid})
    assert ok.get("status") != "error"
    # Still only one token refilled; second call denies again.
    denied2 = call_tool("get_state", {"session_id": sid})
    assert denied2["status"] == "error"


def test_ip_session_create_bounds_allocation_under_session_cap_churn(
    swap_limiter, monkeypatch
):
    """Property: even when SESSION_CAP churn is high (attacker deletes to
    re-allocate), ip_session_create gates per-IP session-creation burst.

    Attacker loop: start_workflow, then delete_session, repeat. The
    ip_session_create axis charges on every start_workflow regardless of
    whether the resulting session is deleted — session creation work is
    the expensive operation bucket it gates."""
    # Very generous session/ip axes so ip_session_create is the isolated gate.
    clock = FakeClock()
    cfg = RateLimitConfig(
        session_rate=1000.0,
        session_burst=1000.0,
        ip_rate=1000.0,
        ip_burst=1000.0,
        ip_create_rate=1.0,
        ip_create_burst=10.0,
    )
    swap_limiter(cfg, clock)
    _patch_http_ip(monkeypatch, "10.7.0.1")
    state.clear_sessions()
    created = 0
    denied = 0
    for i in range(20):
        r = call_tool("start_workflow", {"workflow_type": "canonical", "context": f"c{i}"})
        if r.get("status") == "error" and r.get("code") == "rate_limited":
            denied += 1
            assert r["scope"] == AXIS_IP_SESSION_CREATE
        else:
            created += 1
            # Free the session row so we don't exhaust SESSION_CAP (tests
            # default SESSION_CAP=500; 20 fits but be defensive).
            call_tool("delete_session", {"session_id": r["session_id"]})
    # burst=10 on ip_session_create -> 10 allowed, 10 denied.
    assert created == 10, f"expected 10 creates under burst, got {created}"
    assert denied == 10


# ===========================================================================
# (b) HARVEST RESISTANCE
# ===========================================================================
# IP axis gates workflow-enumerating tools; per-session axis gates session-
# scoped polling uniformly across sessions.


def test_read_only_harvest_from_one_ip_hits_ip_axis(swap_limiter, monkeypatch):
    """Property: list_workflows (workflow-enumerating, no session_id) is
    gated by the IP axis. No session bucket consulted. After burst
    exhaustion the IP axis denies.

    This makes a read-only tight loop a cheap DoS target only up to the
    IP burst, not unbounded."""
    clock = FakeClock()
    # IP burst=5 so we see the cliff quickly.
    cfg = RateLimitConfig(
        session_rate=1000.0,
        session_burst=1000.0,
        ip_rate=0.1,
        ip_burst=5.0,
        ip_create_rate=1000.0,
        ip_create_burst=1000.0,
    )
    swap_limiter(cfg, clock)
    _patch_http_ip(monkeypatch, "10.7.0.2")
    state.clear_sessions()
    allowed = 0
    denied_scope = None
    for _ in range(10):
        r = call_tool("list_workflows", {})
        if r.get("status") == "error" and r.get("code") == "rate_limited":
            denied_scope = r["scope"]
            break
        allowed += 1
    assert allowed == 5, f"expected 5 allowed under IP burst, got {allowed}"
    assert denied_scope == AXIS_IP


def test_cross_session_gating_uniform(swap_limiter):
    """Property: per-session gating applies uniformly — the limiter does
    not special-case 'own vs other' session_ids. A client driving two
    sessions A + B exhausts A's bucket and B's bucket independently,
    each at exactly burst calls. Bypassing A's exhaustion by scraping
    B's fingerprint cannot give the attacker A's bucket.

    Workflow-layer invariant note: list_sessions currently returns all
    rows (no identity filtering today); the harvest property here is
    about bucket isolation, not visibility filtering. Identity-scoped
    list_sessions filtering is a separate M007 discussion if it lands."""
    clock = FakeClock()
    cfg = RateLimitConfig(session_rate=1.0, session_burst=3.0)
    swap_limiter(cfg, clock)
    state.clear_sessions()
    sid_a = call_tool("start_workflow", {"workflow_type": "canonical", "context": "A"})["session_id"]
    sid_b = call_tool("start_workflow", {"workflow_type": "canonical", "context": "B"})["session_id"]
    # Drain A: burst=3 -> 3 allowed, 4th denies.
    for _ in range(3):
        r = call_tool("get_state", {"session_id": sid_a})
        assert r.get("status") != "error"
    r = call_tool("get_state", {"session_id": sid_a})
    assert r["status"] == "error"
    assert r["scope"] == AXIS_SESSION
    # B untouched -> burst=3 available.
    for _ in range(3):
        r = call_tool("get_state", {"session_id": sid_b})
        assert r.get("status") != "error"


# ===========================================================================
# (c) TRANSPORT BYPASS ATTEMPTS
# ===========================================================================
# stdio skips per-IP axis entirely; HTTP with no IP skips per-IP but
# still gates session axis; X-Forwarded-For is future-work.


class _AxisSpy:
    """Records (axis, key) calls; all attempts allowed."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    def try_consume(self, axis: str, key: str, cost: int = 1) -> tuple[bool, float]:
        self.calls.append((axis, key, cost))
        return (True, 0.0)


def test_stdio_transport_skips_ip_axis(monkeypatch):
    """Property: under stdio transport the per-IP and per-IP-session-create
    axes are never consulted — there is no IP to gate on. Per-session
    axis still applies when session_id is present."""
    import megalos_server.middleware as mw_mod

    spy = _AxisSpy()
    mw = _find_rate_limit_middleware()
    original = mw._limiter
    mw._limiter = spy  # type: ignore[assignment]
    try:
        # Force stdio transport.
        monkeypatch.setattr(mw_mod, "_detect_transport", lambda ctx: "stdio")
        monkeypatch.setattr(mw_mod, "_extract_ip", lambda ctx: None)
        state.clear_sessions()
        r = call_tool("start_workflow", {"workflow_type": "canonical", "context": "s"})
        sid = r["session_id"]
        call_tool("get_state", {"session_id": sid})
    finally:
        mw._limiter = original
    axes_hit = {axis for axis, _, _ in spy.calls}
    assert AXIS_IP not in axes_hit, "stdio must not consult AXIS_IP"
    assert AXIS_IP_SESSION_CREATE not in axes_hit, (
        "stdio must not consult AXIS_IP_SESSION_CREATE"
    )
    # AXIS_SESSION may or may not be present depending on tool — here
    # get_state has a session_id, so it IS consulted.
    assert AXIS_SESSION in axes_hit


def test_http_missing_identity_skips_both_ip_and_session_axes(monkeypatch):
    """Property: HTTP transport but no resolvable IP (no client tuple) and
    no session_id on the tool (list_workflows) -> no axis is consulted.
    This matches T01's observation-only posture for identity-absent
    calls; the middleware passes through rather than fabricating a
    synthetic bucket key."""
    spy = _AxisSpy()
    mw = _find_rate_limit_middleware()
    original = mw._limiter
    mw._limiter = spy  # type: ignore[assignment]
    try:
        _patch_http_ip(monkeypatch, None)  # transport=stdio fallback
        state.clear_sessions()
        call_tool("list_workflows", {})
    finally:
        mw._limiter = original
    # No IP + no session_id -> no bucket keyed.
    assert spy.calls == [], f"expected no axis calls, got {spy.calls}"


def test_proxy_header_forwarded_for_deferred():
    """Future-work marker: HTTP proxy header support (X-Forwarded-For,
    Forwarded, X-Real-IP) did not land in T02. Today ``_extract_ip``
    reads ``request.client.host`` directly; a fronted deployment
    behind a trusted proxy would see all requests as originating from
    the proxy's IP, collapsing all IP-axis gating into one bucket.

    When T02 extends this (Phase G production deployment), add a test
    here asserting header trust-boundary enforcement (headers honored
    only when the immediate peer is on the configured trust list)."""
    from megalos_server.middleware import _extract_ip

    # Sanity: today's implementation does not consume forwarded headers.
    # If a later change reads X-Forwarded-For / X-Real-IP / the standard
    # Forwarded header, this assertion breaks and forces an update to the
    # proxy-trust boundary tests. We inspect the function body (not its
    # docstring) so comments/docstring mentions don't trip the check.
    import inspect

    body = inspect.getsource(_extract_ip)
    body_lower = body.lower()
    header_accessors = (
        'headers.get("x-forwarded-for")',
        "headers.get('x-forwarded-for')",
        'headers.get("forwarded")',
        "headers.get('forwarded')",
        'headers.get("x-real-ip")',
        "headers.get('x-real-ip')",
    )
    assert not any(h in body_lower for h in header_accessors), (
        "X-Forwarded-For / Forwarded / X-Real-IP support landed — add "
        "proxy-trust boundary tests."
    )


# ===========================================================================
# (d) MEMORY/LRU BEHAVIOR UNDER ROTATING-IP ATTACK
# ===========================================================================


# Memory regression gate for the 100K-IP stress. Peak memory measured at
# cap=10_000 is ~3.0 MB with <0.1% variance across 3 runs. The ceiling is
# set at 5 MB (≈1.67× measurement) — tight enough to catch a >70%
# regression from (e.g.) a LRU bug that leaks bucket refs, loose enough to
# absorb small runtime variance. If ``ip_store_cap`` changes, re-measure
# and re-pick the ceiling per docs/PERFORMANCE.md §Memory regression gate.
_IP_STORE_100K_PEAK_CEILING_MB = 5.0


def test_rotating_ip_attack_bounded_by_store_cap():
    """Property: 100K unique IPs, each making one call, fit inside a
    bounded store — the LRU evicts past the cap instead of growing
    without bound. Protects against rotating-IP memory attack.

    Memory regression gate: tracemalloc wraps the stress loop and
    asserts peak bytes under the ceiling derived in
    ``docs/PERFORMANCE.md``. A regression caused by (e.g.) a bucket
    leak, a forgotten LRU eviction, or a new per-bucket field that
    bloats the dict would push peak above the ceiling and fail loud."""
    clock = FakeClock()
    # Use the documented default cap (10k) so the assertion matches doc.
    cap = 10_000
    cfg = RateLimitConfig(
        ip_rate=1.0, ip_burst=5.0, ip_store_cap=cap, ip_idle_ttl_sec=1e9
    )
    limiter = RateLimiter(cfg, monotonic=clock)
    tracemalloc.start()
    for i in range(100_000):
        limiter.try_consume(AXIS_IP, f"10.0.{i // 256}.{i % 256}")
    _current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(limiter._ip_store.buckets) <= cap, (
        f"ip store grew past cap: {len(limiter._ip_store.buckets)} > {cap}"
    )
    peak_mb = peak_bytes / (1024 * 1024)
    assert peak_mb <= _IP_STORE_100K_PEAK_CEILING_MB, (
        f"100K-IP stress peak memory {peak_mb:.2f} MB exceeded ceiling "
        f"{_IP_STORE_100K_PEAK_CEILING_MB:.2f} MB — LRU/bucket-shape "
        "regression. Re-measure per docs/PERFORMANCE.md §Memory regression gate."
    )


def test_ip_store_idle_ttl_sweep_drops_aged_entries():
    """Property: idle sweep clears entries whose last_refill is older
    than idle_ttl_sec. Keeps memory bounded when attacker stops
    rotating but leaves 10K stale buckets behind."""
    clock = FakeClock()
    store = _IpStore(cap=5_000, idle_ttl_sec=60.0)
    for i in range(100):
        store.get_or_create(f"aged{i}", capacity=10.0, refill_rate=1.0, now=clock.now)
    clock.advance(120.0)  # beyond TTL.
    store.sweep(clock.now)
    assert len(store.buckets) == 0, "idle sweep must drop aged entries"


# ===========================================================================
# (e) DEDUPE CORRECTNESS
# ===========================================================================


def test_dedupe_100_denies_one_identity_one_warn(caplog):
    """Property: 100 denies in 1 second against one (scope, identity)
    pair emit exactly one WARN log line."""
    _reset_deny_log_cache_for_test()
    clock = FakeClock()
    with caplog.at_level(logging.WARNING, logger="megalos_server.ratelimit"):
        for _ in range(100):
            emit_rate_limit_warn(
                AXIS_SESSION, "fp_deadbeef", "session_fingerprint",
                1000.0, now=clock.now,
            )
            clock.advance(0.01)
    deny_records = [
        r for r in caplog.records if r.getMessage() == "rate_limit_exceeded"
    ]
    assert len(deny_records) == 1
    _reset_deny_log_cache_for_test()


def test_dedupe_100_denies_ten_identities_ten_warns(caplog):
    """Property: 100 denies across 10 distinct (scope, identity) pairs
    emit exactly 10 WARN lines — one per pair per 60s window."""
    _reset_deny_log_cache_for_test()
    clock = FakeClock()
    with caplog.at_level(logging.WARNING, logger="megalos_server.ratelimit"):
        for i in range(100):
            identity = f"fp{i % 10:02d}"
            emit_rate_limit_warn(
                AXIS_SESSION, identity, "session_fingerprint",
                1000.0, now=clock.now,
            )
            clock.advance(0.01)
    deny_records = [
        r for r in caplog.records if r.getMessage() == "rate_limit_exceeded"
    ]
    assert len(deny_records) == 10
    _reset_deny_log_cache_for_test()


def test_dedupe_cache_capped_under_rotating_identity_attack():
    """Property: rotating identities (each unique) trigger LRU eviction
    on the dedupe cache. Cache size never exceeds its cap, preventing
    unbounded growth under a rotating-identity flood."""
    cache = _DenyLogDedupe(window_sec=60.0, idle_ttl_sec=1e9, max_entries=100)
    for i in range(10_000):
        cache.should_emit(AXIS_SESSION, f"id{i}", now=float(i))
    assert len(cache._entries) <= 100, (
        f"dedupe cache grew past cap: {len(cache._entries)} > 100"
    )


# ===========================================================================
# (f) SESSION-ID IDENTITY-SPACE BYPASS SURFACES
# ===========================================================================
# General property: the limiter's notion of session identity must be a
# strict refinement of the workflow layer's identity — anything the
# workflow layer treats as the same session, the limiter must also treat
# as the same bucket.
#
# DISCOVERY NOTE: megalos_server/state.py does NOT currently define
# ``normalize_session_id`` or equivalent canonicalization. session_ids
# round-trip byte-exact through create/get/delete. The refinement
# property therefore holds *trivially* today (no two byte-distinct sids
# are ever treated as "same"). Tests below probe the *aspirational*
# property — the remediation PR adds a shared normalizer wired from
# both state.create_session/lookup AND middleware session_id extraction
# per T03-PLAN fix-shape guidance. Xfails capture those surfaces now so
# the remediation PR has an executable checklist.


def test_empty_session_id_skips_session_axis(monkeypatch):
    """Property: empty-string session_id is filtered at the middleware
    (``isinstance(raw_sid, str) and raw_sid`` rejects falsy). Session
    axis is NOT consulted on empty session_id. This is intentional —
    pydantic/_check_str reject the call downstream with a structured
    validation error.

    Adversarial surface: attacker cannot 'spend' someone else's session
    bucket by sending session_id=''. But also cannot 'save' their own
    session bucket by omitting — since list_workflows-style tools
    already bypass the session axis."""
    spy = _AxisSpy()
    mw = _find_rate_limit_middleware()
    original = mw._limiter
    mw._limiter = spy  # type: ignore[assignment]
    try:
        _patch_http_ip(monkeypatch, None)
        state.clear_sessions()
        call_tool("get_state", {"session_id": ""})
    finally:
        mw._limiter = original
    axes = {axis for axis, _, _ in spy.calls}
    assert AXIS_SESSION not in axes, (
        "empty session_id must not key the session axis (it is filtered)"
    )


def test_missing_session_id_skips_session_axis(monkeypatch):
    """Property: a tool call that omits session_id entirely (e.g. because
    pydantic will reject it anyway) does not create a synthetic 'None'
    bucket. Middleware keys the session axis only when a non-empty
    string is present."""
    spy = _AxisSpy()
    mw = _find_rate_limit_middleware()
    original = mw._limiter
    mw._limiter = spy  # type: ignore[assignment]
    try:
        _patch_http_ip(monkeypatch, None)
        state.clear_sessions()
        # Call with missing session_id; pydantic will reject, but the
        # middleware runs first.
        try:
            call_tool("get_state", {})
        except Exception:
            pass
    finally:
        mw._limiter = original
    axes_keys = [(axis, key) for axis, key, _ in spy.calls]
    assert all(axis != AXIS_SESSION for axis, _ in axes_keys), (
        f"missing session_id keyed session axis: {axes_keys}"
    )


# ---------------------------------------------------------------------------
# Minimal spy/stub plumbing for middleware-layer bypass tests.
#
# The primitive is byte-exact by contract (see
# test_try_consume_session_axis_requires_canonical_key in
# test_ratelimit_primitive.py). The LAYER where normalisation happens is
# the middleware. To prove the bypass is closed at that layer — and to
# make failures loud if the middleware ever stops normalising (the
# forcing-function property) — these tests exercise
# RateLimitMiddleware.on_call_tool directly with a spy limiter that
# records the key passed to try_consume.
# ---------------------------------------------------------------------------


class _SpyLimiter:
    """Records (axis, key, cost); allows every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    def try_consume(self, axis: str, key: str, cost: int = 1) -> tuple[bool, float]:
        self.calls.append((axis, key, cost))
        return (True, 0.0)


class _StubFastMCPContext:
    def __init__(self, transport: str = "stdio") -> None:
        self.transport = transport

    def set_state(self, key: str, value: Any) -> None:  # pragma: no cover - unused
        pass


class _StubMessage:
    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.name = name
        self.arguments = arguments


class _StubMiddlewareContext:
    def __init__(self, message: _StubMessage, transport: str = "stdio") -> None:
        self.message = message
        self.fastmcp_context = _StubFastMCPContext(transport=transport)


async def _noop_call_next(_ctx: Any) -> str:
    return "ok"


def _dispatch(mw: RateLimitMiddleware, sid: str, tool: str = "get_state") -> None:
    ctx = _StubMiddlewareContext(
        message=_StubMessage(name=tool, arguments={"session_id": sid}),
        transport="stdio",
    )
    asyncio.run(mw.on_call_tool(ctx, _noop_call_next))  # type: ignore[arg-type]


def _session_keys(spy: _SpyLimiter) -> list[str]:
    return [key for axis, key, _ in spy.calls if axis == AXIS_SESSION]


def test_case_variant_session_ids_collapse_to_one_bucket():
    """Property: two session_ids differing only in case produce the SAME
    bucket key after middleware session_id extraction.

    Exercised at the MIDDLEWARE layer (the normalisation site). The
    primitive is byte-exact by contract; this test fails loudly if the
    middleware ever stops calling ``normalize_session_id`` — the
    forcing-function property that keeps a single source of truth."""
    spy = _SpyLimiter()
    mw = RateLimitMiddleware(spy)  # type: ignore[arg-type]
    _dispatch(mw, "AbcDef123")
    _dispatch(mw, "abcdef123")
    keys = _session_keys(spy)
    assert len(keys) == 2
    assert keys[0] == keys[1], (
        "middleware must normalise session_id before consulting limiter — "
        "case variants produced distinct bucket keys"
    )


def test_unicode_nfc_nfd_variants_collapse_to_one_bucket():
    """Property: NFC and NFD encodings of the same glyph string produce
    the SAME bucket key after middleware session_id extraction. See
    module docstring on why the test lives at the middleware layer."""
    spy = _SpyLimiter()
    mw = RateLimitMiddleware(spy)  # type: ignore[arg-type]
    nfc = unicodedata.normalize("NFC", "caf\u00e9")
    nfd = unicodedata.normalize("NFD", "cafe\u0301")
    assert nfc != nfd, "test setup invariant: byte-distinct encodings"
    _dispatch(mw, nfc)
    _dispatch(mw, nfd)
    keys = _session_keys(spy)
    assert len(keys) == 2
    assert keys[0] == keys[1], (
        "middleware must NFC-fold session_id — NFC/NFD variants produced "
        "distinct bucket keys"
    )


def test_whitespace_variant_session_ids_collapse_to_one_bucket():
    """Property: session_ids differing only in leading/trailing whitespace
    produce the SAME bucket key after middleware session_id extraction."""
    spy = _SpyLimiter()
    mw = RateLimitMiddleware(spy)  # type: ignore[arg-type]
    _dispatch(mw, "abc123")
    _dispatch(mw, " abc123\t")
    keys = _session_keys(spy)
    assert len(keys) == 2
    assert keys[0] == keys[1], (
        "middleware must strip whitespace from session_id — variants "
        "produced distinct bucket keys"
    )


def test_pathological_length_session_id_does_not_crash():
    """Property: a multi-MB session_id does not crash the limiter. The
    LRU-bounded session dict (capped elsewhere by SESSION_CAP + session-
    deletion hooks) still accepts the call. Each bucket entry holds a
    reference to its key, so extreme sizes are a memory-pressure
    surface — but not a correctness bug."""
    clock = FakeClock()
    cfg = RateLimitConfig(session_rate=1.0, session_burst=1.0)
    limiter = RateLimiter(cfg, monotonic=clock)
    huge = "x" * (1 << 20)  # 1 MiB
    ok, _ = limiter.try_consume(AXIS_SESSION, huge)
    assert ok


def test_isinstance_str_guard_on_session_id_extraction():
    """Property: every session_id extraction path in the middleware
    guards on ``isinstance(raw_sid, str)`` before keying a bucket.
    This prevents dict/list/None-keyed buckets when the tool arg shape
    is malformed (before pydantic validation runs).

    Post-guard coverage grep: single extraction site today (on_call_tool),
    guarded at line-of-read. If future work adds another session_id
    extraction site, this test must be updated to cover it."""
    import megalos_server.middleware as mw_mod
    import inspect

    source = inspect.getsource(mw_mod)
    # Count raw-access patterns that would benefit from a guard. The
    # lookup shape ``arguments.get("session_id")`` is the only extraction
    # site currently. If a second site is added the guard must accompany it.
    extraction_sites = source.count('.get("session_id")')
    guard_patterns = source.count("isinstance(raw_sid, str)")
    assert extraction_sites == 1, (
        f"new session_id extraction site(s) added ({extraction_sites}); "
        "confirm isinstance(str) guard at each."
    )
    assert guard_patterns >= 1, "isinstance(raw_sid, str) guard missing"


def test_general_property_refinement_holds_today():
    """General property (f thesis): the limiter's identity equivalence
    is a strict refinement of the workflow layer's identity
    equivalence. Today neither layer normalizes — both treat
    byte-distinct session_ids as distinct. Refinement holds trivially.

    This test documents the baseline so the remediation PR's
    normalize_session_id addition is a non-regression: after fix, the
    workflow layer normalizes (e.g. NFC) and the limiter reads the same
    normalized key, keeping refinement intact."""
    # Workflow layer: create_session produces a byte-exact sid; get_session
    # is byte-exact lookup. No equivalence classes today.
    import secrets

    sid = secrets.token_urlsafe(32)
    clock = FakeClock()
    limiter = RateLimiter(RateLimitConfig(session_burst=1.0), monotonic=clock)
    ok1, _ = limiter.try_consume(AXIS_SESSION, sid)
    ok2, _ = limiter.try_consume(AXIS_SESSION, sid)
    # Same bytes -> same bucket.
    assert ok1 and ok2 is False
    # Different bytes -> different bucket (refinement is trivial today).
    other = secrets.token_urlsafe(32)
    ok3, _ = limiter.try_consume(AXIS_SESSION, other)
    assert ok3 is True


# ===========================================================================
# (g) TOOL-SURFACE CLASSIFICATION REGRESSION
# ===========================================================================
# Machine-readable drift guard. Enumerates tools registered on the FastMCP
# app at test time and asserts the set matches EXPECTED_CLASSIFICATION.
#
# DEVIATION FROM T03-PLAN NOTE: the plan's T02 audit listed 9 tools; the
# actual registered surface today is 12 (M004/M005 added enter_sub_workflow,
# push_flow, pop_flow — all session-scoped). EXPECTED_CLASSIFICATION
# reflects ground truth; future drift surfaces via the primary assertion.


Classification = Literal["session_creating", "session_scoped", "workflow_enumerating"]

EXPECTED_CLASSIFICATION: dict[str, Classification] = {
    # Session-creating: allocates a new session row + stack frame.
    "start_workflow": "session_creating",
    # Session-scoped: operates on an existing session_id.
    "submit_step": "session_scoped",
    "revise_step": "session_scoped",
    "get_state": "session_scoped",
    "get_guidelines": "session_scoped",
    "delete_session": "session_scoped",
    "generate_artifact": "session_scoped",
    "list_sessions": "session_scoped",
    # Stack-manipulating sub-workflow tools (M004/M005) — operate on
    # existing session identifiers, so classified session_scoped. Not in
    # the original T02 audit; captured here to reflect ground truth.
    "enter_sub_workflow": "session_scoped",
    "push_flow": "session_scoped",
    "pop_flow": "session_scoped",
    # Workflow-enumerating: no session_id; IP-axis gated only on HTTP.
    "list_workflows": "workflow_enumerating",
}


def _registered_tool_names() -> set[str]:
    """Enumerate tool names via the FastMCP introspection API."""
    tools = asyncio.run(mcp.list_tools())
    return {t.name for t in tools}


def test_tool_surface_matches_classification_table():
    """Primary assertion: the set of registered tools equals the set of
    keys in EXPECTED_CLASSIFICATION. Failure signals a new tool has been
    added (or removed) without classification — and without wiring the
    session-create allowlist if it's session-creating."""
    registered = _registered_tool_names()
    expected = set(EXPECTED_CLASSIFICATION.keys())
    diff = registered.symmetric_difference(expected)
    assert not diff, (
        f"unclassified tool surface: {diff} — classify in the test's "
        "EXPECTED_CLASSIFICATION map AND update _SESSION_CREATE_TOOL in "
        "middleware.py if the new tool is session-creating."
    )


def test_session_creating_tools_are_in_allowlist():
    """Secondary assertion: every tool classified ``session_creating``
    appears in the middleware's session-create allowlist
    (``_SESSION_CREATE_TOOL``). Prevents 'classified but not wired' drift.

    ``_SESSION_CREATE_TOOL`` is a single string today; if it later
    becomes a frozenset the check adapts (``in`` works for both)."""
    session_creating = {
        name for name, cls in EXPECTED_CLASSIFICATION.items()
        if cls == "session_creating"
    }
    allowlist: Any = _SESSION_CREATE_TOOL
    if isinstance(allowlist, str):
        allowlist_set = {allowlist}
    else:
        allowlist_set = set(allowlist)
    missing = session_creating - allowlist_set
    assert not missing, (
        f"session_creating tools missing from middleware allowlist: {missing}"
    )
