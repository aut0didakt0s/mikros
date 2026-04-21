"""Unit tests for session_id entropy + fingerprint derivation.

Covers the two primitives underpinning the capability-token model:
  (1) session_ids are 256-bit-entropy tokens from secrets.token_urlsafe(32);
  (2) fingerprint = sha256(session_id)[:12] is a pure, deterministic derivation,
      safe to expose in logs.

No logging, Identity, or access-check coverage here — those live in their own
suites. Pure primitive verification only.
"""

from __future__ import annotations

import hashlib
import re

from megalos_server import db, state
from megalos_server.state import _compute_fingerprint


# secrets.token_urlsafe(n) returns base64url-encoded bytes with '=' padding
# stripped. For n=32 the result is always 43 characters drawn from
# [A-Za-z0-9_-].
_TOKEN_URLSAFE_32_LEN = 43
_TOKEN_URLSAFE_CHARSET = re.compile(r"^[A-Za-z0-9_-]+$")


def _reset_db() -> None:
    db._reset_for_test()
    db.init_schema()
    state.clear_sessions()


def test_session_id_has_token_urlsafe_32_length_and_charset() -> None:
    """Every new session_id must be exactly token_urlsafe(32) output shape."""
    _reset_db()
    sid = state.create_session("test_workflow", current_step="s1")
    assert len(sid) == _TOKEN_URLSAFE_32_LEN, (
        f"session_id length {len(sid)} != expected {_TOKEN_URLSAFE_32_LEN}"
    )
    assert _TOKEN_URLSAFE_CHARSET.match(sid), (
        f"session_id {sid!r} contains characters outside base64url alphabet"
    )


def test_session_ids_are_unique_across_many_creates() -> None:
    """256-bit entropy ⇒ collision probability is negligible; assert uniqueness
    across a reasonable sample. A collision here would mean the RNG is broken
    or the length cap was wrong."""
    _reset_db()
    sids = {state.create_session("test_workflow", current_step="s1") for _ in range(200)}
    assert len(sids) == 200, "session_id collisions across 200 creates"


def test_compute_fingerprint_is_deterministic() -> None:
    """Same session_id in ⇒ same fingerprint out, every time."""
    sid = "example-session-id-literal"
    fp1 = _compute_fingerprint(sid)
    fp2 = _compute_fingerprint(sid)
    assert fp1 == fp2
    # Pin the exact value so accidental truncation changes (e.g. [:10]) fail loud.
    expected = hashlib.sha256(sid.encode()).hexdigest()[:12]
    assert fp1 == expected
    assert len(fp1) == 12
    assert re.match(r"^[0-9a-f]{12}$", fp1)


def test_compute_fingerprint_differs_across_distinct_session_ids() -> None:
    """Injectivity-in-practice: N distinct session_ids produce N distinct
    fingerprints. 48-bit output ⇒ birthday-bound collision at ~2^24 ≈ 16M;
    200 inputs is nowhere near that."""
    _reset_db()
    sids = [state.create_session("test_workflow", current_step="s1") for _ in range(200)]
    fps = {_compute_fingerprint(sid) for sid in sids}
    assert len(fps) == len(sids), "fingerprint collision across 200 distinct session_ids"


def test_session_dict_carries_fingerprint_on_hydrate() -> None:
    """get_session must attach fingerprint to the hydrated dict, and it must
    match _compute_fingerprint(session_id) — no drift between the two."""
    _reset_db()
    sid = state.create_session("test_workflow", current_step="s1")
    sess = state.get_session(sid)
    assert sess["fingerprint"] == _compute_fingerprint(sid)
    assert sess["session_id"] == sid


def test_list_sessions_entries_carry_fingerprint() -> None:
    """list_sessions is a hydrate path too; every entry must carry fingerprint."""
    _reset_db()
    sid_a = state.create_session("test_workflow", current_step="s1")
    sid_b = state.create_session("test_workflow", current_step="s1")
    entries = {e["session_id"]: e for e in state.list_sessions()}
    assert entries[sid_a]["fingerprint"] == _compute_fingerprint(sid_a)
    assert entries[sid_b]["fingerprint"] == _compute_fingerprint(sid_b)


def test_delete_session_returned_dict_carries_fingerprint() -> None:
    """delete_session returns the hydrated dict; fingerprint must be attached."""
    _reset_db()
    sid = state.create_session("test_workflow", current_step="s1")
    deleted = state.delete_session(sid)
    assert deleted["fingerprint"] == _compute_fingerprint(sid)
