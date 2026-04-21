"""Fake-clock tests for the RateLimiter primitive + bounded IP store.

Verifies the sync consume() contract, clock-skew defense, key-axis
isolation, and LRU/TTL behavior of the bounded IP store. No log
assertions — primitive emits no logs in T01.
"""

import pytest  # type: ignore[import-not-found]

from megalos_server.ratelimit import (
    AXIS_IP,
    AXIS_IP_SESSION_CREATE,
    AXIS_SESSION,
    RateLimitConfig,
    RateLimiter,
    TokenBucket,
    _IpStore,
)


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


# ---------------------------------------------------------------------------
# TokenBucket fundamentals
# ---------------------------------------------------------------------------


def test_burst_exhaustion_returns_retry_after_positive():
    clock = FakeClock()
    # Very small bucket to make exhaustion trivial.
    cfg = RateLimitConfig(session_rate=1.0, session_burst=3.0)
    limiter = RateLimiter(cfg, monotonic=clock)
    for _ in range(3):
        allowed, retry = limiter.try_consume(AXIS_SESSION, "s1")
        assert allowed is True
        assert retry == 0.0
    allowed, retry_ms = limiter.try_consume(AXIS_SESSION, "s1")
    assert allowed is False
    assert retry_ms > 0
    # One token refills in 1 second @ 1tok/s -> 1000ms.
    assert abs(retry_ms - 1000.0) < 1e-6


def test_steady_state_refill_matches_rate():
    clock = FakeClock()
    # rate 2 tokens/sec, burst 5 tokens. Consume once per 0.5s for 10s
    # -> 20 attempts; initial burst absorbs 5, refill adds 2*10=20 tokens
    # total: 25 allowed out of 20? No — only 20 attempts. All allowed.
    #
    # Better: exhaust burst, then consume 1/sec at exactly the rate.
    cfg = RateLimitConfig(session_rate=2.0, session_burst=5.0)
    limiter = RateLimiter(cfg, monotonic=clock)
    # Drain the burst.
    for _ in range(5):
        assert limiter.try_consume(AXIS_SESSION, "s1")[0]
    # Steady state: 2 tokens/sec -> one allowed every 0.5s.
    allowed_count = 0
    for _ in range(10):
        clock.advance(0.5)
        allowed, _ = limiter.try_consume(AXIS_SESSION, "s1")
        if allowed:
            allowed_count += 1
    assert allowed_count == 10  # one per 0.5s for 5s = 10 tokens earned.


def test_steady_state_floor_at_sub_rate():
    # Consume faster than rate -> allowed count tracks rate*M (plus burst
    # absorption if not pre-drained). Use an absolute-time clock advance
    # so floating-point accumulation drift doesn't bite the assertion.
    clock = FakeClock()
    cfg = RateLimitConfig(session_rate=2.0, session_burst=3.0)
    limiter = RateLimiter(cfg, monotonic=clock)
    # Drain burst first to isolate the refill contribution.
    for _ in range(3):
        assert limiter.try_consume(AXIS_SESSION, "s1")[0]
    # Hammer 1000 requests over 5s => 5*2 = 10 tokens earned.
    allowed = 0
    for i in range(1, 1001):
        clock.now = i * 0.005  # absolute, not accumulated
        ok, _ = limiter.try_consume(AXIS_SESSION, "s1")
        if ok:
            allowed += 1
    assert allowed == 10


def test_clock_skew_same_value_twice_no_double_spend():
    clock = FakeClock()
    cfg = RateLimitConfig(session_rate=10.0, session_burst=2.0)
    limiter = RateLimiter(cfg, monotonic=clock)
    ok1, _ = limiter.try_consume(AXIS_SESSION, "s1")
    ok2, _ = limiter.try_consume(AXIS_SESSION, "s1")
    # Both should succeed (burst 2).
    assert ok1 and ok2
    # No clock advance; third should fail — no token creation.
    ok3, retry = limiter.try_consume(AXIS_SESSION, "s1")
    assert ok3 is False
    assert retry > 0


def test_clock_skew_backward_jump_clamped():
    clock = FakeClock(start=100.0)
    cfg = RateLimitConfig(session_rate=10.0, session_burst=1.0)
    limiter = RateLimiter(cfg, monotonic=clock)
    # Drain.
    assert limiter.try_consume(AXIS_SESSION, "s1")[0]
    # Jump backward by 50s — would naively add 500 tokens if unclamped.
    # Clamp must prevent any refill AND not rewind last_refill (otherwise
    # a subsequent tiny forward advance from the rewound value would
    # "credit" the skew as legitimate elapsed time).
    clock.now = 50.0
    ok, retry = limiter.try_consume(AXIS_SESSION, "s1")
    assert ok is False
    assert retry > 0
    # Advance past the original anchor: last_refill stayed at ~100, so
    # earning tokens requires the clock to exceed 100 again. At rate 10,
    # one token is earned 0.1s past the anchor; use 0.5s of margin to
    # sidestep float-subtraction drift on small deltas.
    clock.now = 100.5
    ok2, _ = limiter.try_consume(AXIS_SESSION, "s1")
    assert ok2 is True


# ---------------------------------------------------------------------------
# Key / axis isolation
# ---------------------------------------------------------------------------


def test_separate_keys_same_axis_isolated():
    clock = FakeClock()
    cfg = RateLimitConfig(session_rate=1.0, session_burst=1.0)
    limiter = RateLimiter(cfg, monotonic=clock)
    assert limiter.try_consume(AXIS_SESSION, "a")[0]
    # "a" exhausted, "b" untouched.
    assert limiter.try_consume(AXIS_SESSION, "a")[0] is False
    assert limiter.try_consume(AXIS_SESSION, "b")[0]


def test_cross_axis_keys_isolated():
    clock = FakeClock()
    cfg = RateLimitConfig(
        session_rate=1.0,
        session_burst=1.0,
        ip_rate=1.0,
        ip_burst=1.0,
        ip_create_rate=1.0,
        ip_create_burst=1.0,
    )
    limiter = RateLimiter(cfg, monotonic=clock)
    # Same key "x" across all three axes — each bucket is independent.
    assert limiter.try_consume(AXIS_SESSION, "x")[0]
    assert limiter.try_consume(AXIS_IP, "x")[0]
    assert limiter.try_consume(AXIS_IP_SESSION_CREATE, "x")[0]
    # Each is now drained independently.
    assert limiter.try_consume(AXIS_SESSION, "x")[0] is False
    assert limiter.try_consume(AXIS_IP, "x")[0] is False
    assert limiter.try_consume(AXIS_IP_SESSION_CREATE, "x")[0] is False


# ---------------------------------------------------------------------------
# Bounded IP store
# ---------------------------------------------------------------------------


def test_ip_store_lru_eviction_on_cap():
    clock = FakeClock()
    cfg = RateLimitConfig(
        ip_rate=1.0, ip_burst=10.0, ip_store_cap=3, ip_idle_ttl_sec=1e9
    )
    limiter = RateLimiter(cfg, monotonic=clock)
    for ip in ("a", "b", "c"):
        limiter.try_consume(AXIS_IP, ip)
    assert set(limiter._ip_store.buckets.keys()) == {"a", "b", "c"}
    # Insert cap+1 — oldest ("a") should be evicted.
    limiter.try_consume(AXIS_IP, "d")
    assert "a" not in limiter._ip_store.buckets
    assert set(limiter._ip_store.buckets.keys()) == {"b", "c", "d"}


def test_ip_store_lru_promotion_prevents_eviction():
    clock = FakeClock()
    cfg = RateLimitConfig(
        ip_rate=1.0, ip_burst=10.0, ip_store_cap=3, ip_idle_ttl_sec=1e9
    )
    limiter = RateLimiter(cfg, monotonic=clock)
    for ip in ("a", "b", "c"):
        limiter.try_consume(AXIS_IP, ip)
    # Touch "a" — moves it to MRU end.
    limiter.try_consume(AXIS_IP, "a")
    # Insert new — "b" should now be LRU victim, not "a".
    limiter.try_consume(AXIS_IP, "d")
    assert "a" in limiter._ip_store.buckets
    assert "b" not in limiter._ip_store.buckets


def test_ip_store_idle_ttl_sweep_drops_expired():
    clock = FakeClock()
    store = _IpStore(cap=100, idle_ttl_sec=60.0)
    # Populate a few buckets.
    for ip in ("old1", "old2", "fresh"):
        store.get_or_create(ip, capacity=10.0, refill_rate=1.0, now=clock.now)
    # Advance time past TTL for the old ones.
    clock.advance(120.0)
    # Touch "fresh" so its last_refill advances.
    fresh_bucket = store.get_or_create(
        "fresh", capacity=10.0, refill_rate=1.0, now=clock.now
    )
    fresh_bucket.last_refill = clock.now
    # Public sweep invocation.
    store.sweep(clock.now)
    assert "old1" not in store.buckets
    assert "old2" not in store.buckets
    assert "fresh" in store.buckets


def test_ip_store_ttl_sweep_fires_on_nth_access():
    # Exercise the on-access sweep path (every _SWEEP_EVERY-th access).
    clock = FakeClock()
    store = _IpStore(cap=1000, idle_ttl_sec=10.0)
    # Populate one stale bucket.
    store.get_or_create("stale", capacity=5.0, refill_rate=1.0, now=clock.now)
    clock.advance(30.0)
    # Trigger enough accesses to cross the sweep threshold (_SWEEP_EVERY=64).
    # Use unique keys so LRU doesn't touch "stale".
    for i in range(80):
        store.get_or_create(f"fresh{i}", capacity=5.0, refill_rate=1.0, now=clock.now)
    assert "stale" not in store.buckets


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_unknown_axis_raises():
    limiter = RateLimiter()
    with pytest.raises(ValueError):
        limiter.try_consume("bogus", "k")


def test_config_from_env_rejects_bad_input():
    with pytest.raises(ValueError):
        RateLimitConfig.from_env({"MEGALOS_RATELIMIT_SESSION_RATE": "not-a-number"})
    with pytest.raises(ValueError):
        RateLimitConfig.from_env({"MEGALOS_RATELIMIT_IP_BURST": "-1"})
    with pytest.raises(ValueError):
        RateLimitConfig.from_env({"MEGALOS_RATELIMIT_IP_STORE_CAP": "-5"})


def test_config_from_env_uses_defaults_when_unset():
    cfg = RateLimitConfig.from_env({})
    assert cfg.session_rate == 2.0
    assert cfg.ip_burst == 200.0
    assert cfg.ip_store_cap == 10_000


def test_drop_session_clears_bucket():
    clock = FakeClock()
    limiter = RateLimiter(RateLimitConfig(session_burst=1.0, session_rate=0.1), monotonic=clock)
    assert limiter.try_consume(AXIS_SESSION, "s1")[0]
    # Bucket exists; drop it.
    limiter.drop_session("s1")
    # Fresh creation => full burst again.
    ok, _ = limiter.try_consume(AXIS_SESSION, "s1")
    assert ok is True


def test_token_bucket_is_plain_dataclass():
    # Shape guard — keep TokenBucket boring.
    b = TokenBucket(capacity=5.0, refill_rate=1.0, tokens=5.0, last_refill=0.0)
    assert b.capacity == 5.0
    assert b.tokens == 5.0


def test_try_consume_session_axis_requires_canonical_key():
    """Contract: ``try_consume`` does NOT self-normalise session-axis keys.

    The limiter is byte-exact on keys. Callers (state.py entry points,
    middleware session_id extraction) are the single source of truth for
    canonicalisation, performed once at the layer boundary via
    ``session_canon.normalize_session_id``. Internal normalisation here
    would silently mask caller-layer drift — neutralising the adversarial
    bypass tests as a forcing function the moment a future caller forgets
    to normalise.

    This test documents the contract twofold:

    (1) Non-canonical variants key DISTINCT buckets. ``"ABC"`` and
        ``"abc"`` are bypass-equivalent under ``normalize_session_id``,
        but the limiter treats them as separate buckets — proving the
        absence of self-normalisation. Callers that pass non-canonical
        keys will suffer a real divergence, not a silent correction.

    (2) Canonical keys round-trip (idempotence). For any canonical input
        ``k`` (where ``k == normalize_session_id(k)``), the limiter's
        bucket for ``k`` is identical to its bucket for
        ``normalize_session_id(k)`` — the contract-satisfying path.
    """
    from megalos_server.session_canon import normalize_session_id

    clock = FakeClock()
    limiter = RateLimiter(
        RateLimitConfig(session_burst=2.0, session_rate=0.0),
        monotonic=clock,
    )

    non_canon = "ABC"
    canon = normalize_session_id(non_canon)
    assert non_canon != canon, (
        "test sentinel: expected ABC to differ from its canonical form"
    )

    # (1) Distinct buckets under non-canonical input.
    limiter.try_consume(AXIS_SESSION, non_canon)  # "ABC" bucket: 2 -> 1
    limiter.try_consume(AXIS_SESSION, non_canon)  # "ABC" bucket: 1 -> 0
    allowed_after_burst, _ = limiter.try_consume(AXIS_SESSION, non_canon)
    assert allowed_after_burst is False, "ABC bucket should be empty after burst"
    # "abc" (canonical form) has its OWN full bucket — no silent correction.
    allowed_canon, _ = limiter.try_consume(AXIS_SESSION, canon)
    assert allowed_canon is True, (
        "contract violated: limiter silently normalised non-canonical key"
    )

    # (2) Canonical-key idempotence: calling with canonical input twice
    # keys the same bucket both times.
    assert normalize_session_id(canon) == canon  # canon is a fixed point
    limiter.try_consume(AXIS_SESSION, canon)  # "abc" bucket: 1 -> 0
    allowed_canon_exhausted, _ = limiter.try_consume(AXIS_SESSION, canon)
    assert allowed_canon_exhausted is False, (
        "canonical bucket should be exhausted — proves repeat-call keys same bucket"
    )
