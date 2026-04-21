"""Token-bucket rate limiter primitive + bounded IP bucket store + deny WARN log.

Sync-consume constraint (verbatim):

    No await between bucket read and write. Pure async-outer + sync-inner
    block atomic at asyncio event-loop level, no lock needed. If state
    moves out-of-process, revisit.

The primitive ships in T01. T02 adds the WARN-log emission hook
(``emit_rate_limit_warn``) with (scope, identity, 60s window) dedupe via
a bounded in-memory LRU, so a sustained attack produces one log line per
minute per (scope, identity), not one per request.

Three axes — session, ip, ip_session_create — each with its own bucket
store. The per-session store is an unbounded dict keyed by session_id
(naturally bounded elsewhere by SESSION_CAP + session-deletion hooks
registered later). The per-IP stores use a bounded LRU (OrderedDict) with
configurable size cap and idle TTL so a rotating-IP attack can't consume
unbounded memory.

Clock-skew handling: monotonic() is *supposed* to be monotonic, but we
defensively clamp backward jumps and same-value reads to zero elapsed —
no negative refill, no accidental token creation, no double-spend.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

from .session_canon import normalize_session_id

_log = logging.getLogger("megalos_server.ratelimit")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Defaults baked in here; overridable via MEGALOS_RATELIMIT_* env vars.
_DEFAULT_SESSION_RATE = 2.0
_DEFAULT_SESSION_BURST = 30.0
_DEFAULT_IP_RATE = 60.0
_DEFAULT_IP_BURST = 200.0
_DEFAULT_IP_CREATE_RATE = 1.0
_DEFAULT_IP_CREATE_BURST = 10.0
_DEFAULT_IP_STORE_CAP = 10_000
_DEFAULT_IP_IDLE_TTL_SEC = 600.0


@dataclass(frozen=True)
class RateLimitConfig:
    """Rate-limiter configuration. Loaded from env vars via ``from_env``.

    Per-axis (rate, burst) pairs: ``rate`` is tokens-per-second refill
    rate; ``burst`` is bucket capacity (max tokens, initial tokens).
    """

    session_rate: float = _DEFAULT_SESSION_RATE
    session_burst: float = _DEFAULT_SESSION_BURST
    ip_rate: float = _DEFAULT_IP_RATE
    ip_burst: float = _DEFAULT_IP_BURST
    ip_create_rate: float = _DEFAULT_IP_CREATE_RATE
    ip_create_burst: float = _DEFAULT_IP_CREATE_BURST
    ip_store_cap: int = _DEFAULT_IP_STORE_CAP
    ip_idle_ttl_sec: float = _DEFAULT_IP_IDLE_TTL_SEC

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "RateLimitConfig":
        """Build a config from MEGALOS_RATELIMIT_* env vars. ValueError on
        non-numeric or negative values."""
        src = env if env is not None else os.environ
        return cls(
            session_rate=_pos_float(src, "MEGALOS_RATELIMIT_SESSION_RATE", _DEFAULT_SESSION_RATE),
            session_burst=_pos_float(src, "MEGALOS_RATELIMIT_SESSION_BURST", _DEFAULT_SESSION_BURST),
            ip_rate=_pos_float(src, "MEGALOS_RATELIMIT_IP_RATE", _DEFAULT_IP_RATE),
            ip_burst=_pos_float(src, "MEGALOS_RATELIMIT_IP_BURST", _DEFAULT_IP_BURST),
            ip_create_rate=_pos_float(src, "MEGALOS_RATELIMIT_IP_CREATE_RATE", _DEFAULT_IP_CREATE_RATE),
            ip_create_burst=_pos_float(src, "MEGALOS_RATELIMIT_IP_CREATE_BURST", _DEFAULT_IP_CREATE_BURST),
            ip_store_cap=_pos_int(src, "MEGALOS_RATELIMIT_IP_STORE_CAP", _DEFAULT_IP_STORE_CAP),
            ip_idle_ttl_sec=_pos_float(src, "MEGALOS_RATELIMIT_IP_IDLE_TTL_SEC", _DEFAULT_IP_IDLE_TTL_SEC),
        )


def _pos_float(src: dict | os._Environ, key: str, default: float) -> float:
    raw = src.get(key)
    if raw is None or raw == "":
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be numeric, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{key} must be non-negative, got {value}")
    return value


def _pos_int(src: dict | os._Environ, key: str, default: int) -> int:
    raw = src.get(key)
    if raw is None or raw == "":
        return int(default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be integer, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{key} must be non-negative, got {value}")
    return value


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


@dataclass
class TokenBucket:
    """Token bucket: capacity, refill_rate (tokens/sec), current tokens, last refill time."""

    capacity: float
    refill_rate: float
    tokens: float
    last_refill: float


# ---------------------------------------------------------------------------
# Bounded IP store
# ---------------------------------------------------------------------------


# Sweep interval: run expiration sweep once every N accesses. Keeps sweep
# amortized O(1) per access without a background task.
_SWEEP_EVERY = 64


@dataclass
class _IpStore:
    """Bounded LRU store for per-IP buckets. OrderedDict gives O(1)
    move_to_end (promotion) + popitem(last=False) (LRU evict)."""

    cap: int
    idle_ttl_sec: float
    buckets: OrderedDict[str, TokenBucket] = field(default_factory=OrderedDict)
    _access_count: int = 0

    def get_or_create(
        self, key: str, capacity: float, refill_rate: float, now: float
    ) -> TokenBucket:
        self._access_count += 1
        if self._access_count % _SWEEP_EVERY == 0:
            self._sweep_expired(now)
        bucket = self.buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(
                capacity=capacity,
                refill_rate=refill_rate,
                tokens=capacity,
                last_refill=now,
            )
            self.buckets[key] = bucket
            self._evict_if_over_cap()
        else:
            # LRU promotion on access.
            self.buckets.move_to_end(key)
        return bucket

    def _evict_if_over_cap(self) -> None:
        while len(self.buckets) > self.cap:
            self.buckets.popitem(last=False)

    def _sweep_expired(self, now: float) -> None:
        if self.idle_ttl_sec <= 0:
            return
        cutoff = now - self.idle_ttl_sec
        expired_keys = [k for k, b in self.buckets.items() if b.last_refill < cutoff]
        for k in expired_keys:
            del self.buckets[k]

    def sweep(self, now: float) -> None:
        """Public sweep hook (used by tests)."""
        self._sweep_expired(now)


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


AXIS_SESSION = "session"
AXIS_IP = "ip"
AXIS_IP_SESSION_CREATE = "ip_session_create"

_AXES = (AXIS_SESSION, AXIS_IP, AXIS_IP_SESSION_CREATE)


class RateLimiter:
    """Three-axis token-bucket rate limiter.

    Per-session store is an unbounded plain dict (bounded elsewhere by
    SESSION_CAP). Per-IP stores are bounded LRUs (see ``_IpStore``).

    ``try_consume`` is synchronous by design — see module docstring.
    """

    def __init__(
        self,
        config: RateLimitConfig | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.config = config or RateLimitConfig.from_env()
        self._monotonic = monotonic
        self._session_buckets: dict[str, TokenBucket] = {}
        self._ip_store = _IpStore(
            cap=self.config.ip_store_cap,
            idle_ttl_sec=self.config.ip_idle_ttl_sec,
        )
        self._ip_create_store = _IpStore(
            cap=self.config.ip_store_cap,
            idle_ttl_sec=self.config.ip_idle_ttl_sec,
        )

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def try_consume(self, axis: str, key: str, cost: int = 1) -> tuple[bool, float]:
        """Try to consume ``cost`` tokens from the bucket at ``(axis, key)``.

        Returns ``(allowed, retry_after_ms)``. On allow, retry_after_ms is 0.
        On deny, retry_after_ms is the milliseconds until enough tokens
        refill to cover the request.

        On the session axis, ``key`` is folded through the shared
        ``normalize_session_id`` canonicaliser before bucket lookup so case /
        Unicode NFC-NFD / whitespace variants of the same logical session
        key the SAME bucket. The middleware already normalises at its own
        layer; this call is belt-and-suspenders so any code path reaching
        the limiter (adversarial tests, direct callers, future wiring) can
        NOT silently re-open the bypass class T03 surfaced.
        """
        if axis not in _AXES:
            raise ValueError(f"unknown axis: {axis}")
        if axis == AXIS_SESSION:
            key = normalize_session_id(key)
        now = self._monotonic()
        bucket = self._get_bucket(axis, key, now)
        # Sync-inner block: no await between read and write.
        self._refill(bucket, now)
        if bucket.tokens >= cost:
            bucket.tokens -= cost
            return (True, 0.0)
        deficit = cost - bucket.tokens
        if bucket.refill_rate <= 0:
            # Pathological config — bucket cannot refill. Report a
            # large retry_after so caller retries far in the future.
            return (False, float("inf"))
        retry_after_sec = deficit / bucket.refill_rate
        return (False, retry_after_sec * 1000.0)

    def drop_session(self, session_id: str) -> None:
        """Drop the session-axis bucket for ``session_id`` (called by
        state.delete_session hook once wired; harmless if absent)."""
        self._session_buckets.pop(session_id, None)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _get_bucket(self, axis: str, key: str, now: float) -> TokenBucket:
        if axis == AXIS_SESSION:
            b = self._session_buckets.get(key)
            if b is None:
                b = TokenBucket(
                    capacity=self.config.session_burst,
                    refill_rate=self.config.session_rate,
                    tokens=self.config.session_burst,
                    last_refill=now,
                )
                self._session_buckets[key] = b
            return b
        if axis == AXIS_IP:
            return self._ip_store.get_or_create(
                key, self.config.ip_burst, self.config.ip_rate, now
            )
        # AXIS_IP_SESSION_CREATE
        return self._ip_create_store.get_or_create(
            key, self.config.ip_create_burst, self.config.ip_create_rate, now
        )

    def _refill(self, bucket: TokenBucket, now: float) -> None:
        elapsed = now - bucket.last_refill
        # Clock-skew clamp: backward jump or same-value -> zero elapsed.
        if elapsed < 0:
            elapsed = 0.0
        added = elapsed * bucket.refill_rate
        if added > 0:
            bucket.tokens = min(bucket.capacity, bucket.tokens + added)
        # Always advance last_refill forward (but never backward) so a
        # later forward tick sees the right delta. If elapsed is 0 (same
        # value twice) last_refill is already correct.
        if now > bucket.last_refill:
            bucket.last_refill = now


# ---------------------------------------------------------------------------
# Deny WARN log emission with dedupe
# ---------------------------------------------------------------------------


# Dedupe window: one log line per (scope, identity) per WINDOW seconds.
# 60s matches the plan — sustained attack produces one line per minute per
# (scope, identity) rather than one per request.
_DEDUPE_WINDOW_SEC = 60.0

# Idle-TTL: sweep stale entries out of the dedupe cache. Naturally smaller
# than the per-IP bucket store — entries here carry a single timestamp,
# not a bucket.
_DEDUPE_IDLE_TTL_SEC = 300.0

# Hard cap on the dedupe cache. Bounds memory under a rotating-identity
# attack. 1000 entries * ~40 bytes key + 8 byte timestamp ~= 50kB.
_DEDUPE_MAX_ENTRIES = 1000

# Sweep stale entries once every N inserts. Matches the _IpStore cadence —
# amortized O(1) without a background task.
_DEDUPE_SWEEP_EVERY = 64


class _DenyLogDedupe:
    """Bounded LRU dedupe cache for deny WARN log emission.

    Keyed by ``(scope, identity)``; value is the monotonic timestamp of the
    last emitted log line for that pair. ``should_emit(now)`` returns True
    iff the last-emit timestamp is absent or older than ``WINDOW`` seconds,
    and records ``now`` as the new emit timestamp.
    """

    def __init__(
        self,
        window_sec: float = _DEDUPE_WINDOW_SEC,
        idle_ttl_sec: float = _DEDUPE_IDLE_TTL_SEC,
        max_entries: int = _DEDUPE_MAX_ENTRIES,
    ):
        self._window = window_sec
        self._idle_ttl = idle_ttl_sec
        self._max = max_entries
        self._entries: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._access_count = 0

    def should_emit(self, scope: str, identity: str, now: float) -> bool:
        self._access_count += 1
        if self._access_count % _DEDUPE_SWEEP_EVERY == 0:
            self._sweep_expired(now)
        key = (scope, identity)
        last = self._entries.get(key)
        if last is not None and (now - last) < self._window:
            # Still inside the dedupe window — suppress. Promote the key so
            # it isn't evicted while the window is active.
            self._entries.move_to_end(key)
            return False
        self._entries[key] = now
        self._entries.move_to_end(key)
        self._evict_if_over_cap()
        return True

    def _evict_if_over_cap(self) -> None:
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)

    def _sweep_expired(self, now: float) -> None:
        if self._idle_ttl <= 0:
            return
        cutoff = now - self._idle_ttl
        expired = [k for k, ts in self._entries.items() if ts < cutoff]
        for k in expired:
            del self._entries[k]


# Module-level dedupe singleton. Process-memory only; resets on redeploy —
# intentional, matches bucket-store semantics (see docs/rate-limits.md).
_deny_log_cache = _DenyLogDedupe()


def hash_ip(ip: str) -> str:
    """Fingerprint a raw remote IP for log emission.

    Rationale: raw IP in log lines is PII-adjacent in some jurisdictions
    and potentially hands an attacker a free correlation ID. Hash with the
    same shape the session_fingerprint uses — sha256 truncated to 12 hex
    chars. Collisions at 48 bits are large enough for incident-response
    granularity.
    """
    return hashlib.sha256(ip.encode()).hexdigest()[:12]


def emit_rate_limit_warn(
    scope: str,
    identity: str,
    identity_kind: str,
    retry_after_ms: float,
    now: float | None = None,
) -> bool:
    """Emit a structured WARN log line for a rate-limit denial (deduped).

    Fields: ``event=rate_limit_exceeded``, ``scope``, ``retry_after_ms``
    plus the caller-chosen ``identity_kind`` (``session_fingerprint`` or
    ``ip_fingerprint``) carrying ``identity``. No raw session_id or raw
    IP passes through this function — callers must pre-hash.

    Dedupe by ``(scope, identity, 60s)``. Returns True iff a log line was
    actually emitted; False when the emission was suppressed by dedupe.
    """
    tick = now if now is not None else time.monotonic()
    if not _deny_log_cache.should_emit(scope, identity, tick):
        return False
    _log.warning(
        "rate_limit_exceeded",
        extra={
            "event": "rate_limit_exceeded",
            "scope": scope,
            identity_kind: identity,
            "retry_after_ms": retry_after_ms,
        },
    )
    return True


def _reset_deny_log_cache_for_test() -> None:
    """Test-only hook: drop the dedupe cache so integration tests that
    share a process don't bleed dedupe state across test functions."""
    global _deny_log_cache
    _deny_log_cache = _DenyLogDedupe()


__all__ = [
    "AXIS_IP",
    "AXIS_IP_SESSION_CREATE",
    "AXIS_SESSION",
    "RateLimitConfig",
    "RateLimiter",
    "TokenBucket",
    "emit_rate_limit_warn",
    "hash_ip",
]
