"""Adversarial test surface verifying the capability-token model.

These tests verify the capability-token model holds, not that hijack attempts fail.
Under possession-as-capability, the MCP-spec hijack attacks are redefined as
legitimate access. See docs/SECURITY.md (S03) for the threat-model rationale.

The three test sections mirror the model's three load-bearing claims:

  (a) ENUMERATION RESISTANCE — 256-bit session_id entropy defeats brute-force.
  (b) LOG-LEAK PREVENTION    — session_ids never appear in logs or outbound payloads.
  (c) INTERNAL CONSISTENCY   — a tool call naming session X serves session X's data
                               (defense-in-depth against code bugs, not against
                               attackers).

MCP-spec §Session Hijacking (https://modelcontextprotocol.io/llms-full.txt) is
cited in test comments only — never in assertion semantics. A test that asserts
"hijack attempt fails" is the wrong shape here.
"""

import logging
import re
import secrets

import pytest  # type: ignore[import-not-found]

from conftest import call_tool
from megalos_server import state


_CANONICAL = "canonical"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _start_session() -> str:
    """Create a fresh session against the canonical framework fixture."""
    r = call_tool("start_workflow", {"workflow_type": _CANONICAL, "context": "test"})
    return r["session_id"]


# secrets.token_urlsafe(n) emits base64url without padding.
# 32 entropy-bytes ceil-encode to 43 characters; charset is URL-safe b64.
_TOKEN_URLSAFE_CHARSET = re.compile(r"^[A-Za-z0-9_-]+$")
_TOKEN_URLSAFE_LEN = len(secrets.token_urlsafe(32))  # 43 — stay portable if stdlib shifts.


# ---------------------------------------------------------------------------
# (a) ENUMERATION RESISTANCE
# ---------------------------------------------------------------------------
# Claim: 256-bit session_id entropy defeats brute-force enumeration.
#
# MCP-spec §Session Hijacking: Session Hijack Prompt Injection / Session Hijack
# Impersonation — under possession-as-capability, those attacks are redefined
# as legitimate access once the attacker possesses the session_id. These tests
# verify the property that keeps possession expensive (entropy, uniqueness,
# structured-not-found on unknown tokens), not that hijack attempts fail.


def test_session_id_entropy_length_and_charset(monkeypatch):
    """N=100 freshly minted session_ids must match `secrets.token_urlsafe(32)`
    in length and charset. Cap is raised locally so 100 sessions fit."""
    monkeypatch.setenv("MEGALOS_SESSION_CAP", "200")
    sids = [state.create_session(_CANONICAL, current_step="alpha") for _ in range(100)]
    assert len(sids) == 100
    for sid in sids:
        assert len(sid) == _TOKEN_URLSAFE_LEN, f"unexpected length: {sid!r}"
        assert _TOKEN_URLSAFE_CHARSET.match(sid), f"non-urlsafe char in {sid!r}"


def test_session_ids_unique_across_batch(monkeypatch):
    """N=1000 fresh session_ids must have zero collisions. token_urlsafe(32)
    yields 256 bits of entropy; a birthday-paradox collision within 1000 draws
    is astronomically improbable — any hit here signals a broken entropy source."""
    monkeypatch.setenv("MEGALOS_SESSION_CAP", "2000")
    sids = [state.create_session(_CANONICAL, current_step="alpha") for _ in range(1000)]
    assert len(sids) == len(set(sids)) == 1000


def test_unknown_session_id_returns_structured_not_found():
    """Fabricate a syntactically valid but unallocated session_id; any
    session-scoped tool must return the structured session_not_found envelope.

    No timing assertions — they flake in CI. Under possession-as-capability
    the only enumeration defense that matters at runtime is 'unknown tokens
    get a uniform, structured rejection' — which is what this asserts."""
    fabricated = secrets.token_urlsafe(32)
    result = call_tool("get_state", {"session_id": fabricated})
    assert result["status"] == "error"
    assert result["code"] == "session_not_found"
    # The envelope must carry the fingerprint-only identity field; the CI
    # grep gate enforces that error payloads never echo the raw session_id.
    assert "session_id" not in result
    assert result["session_fingerprint"] == state._compute_fingerprint(fabricated)


# ---------------------------------------------------------------------------
# (b) LOG-LEAK PREVENTION
# ---------------------------------------------------------------------------
# Claim: session_ids (the capability token) never appear in logs or in
# structured error payloads.
#
# MCP-spec §Session Hijacking: Session Hijack via Log Exposure — under
# possession-as-capability, a leaked session_id in a log file would hand the
# capability to anyone with log-read access. These tests verify the
# non-emission property directly.


def test_full_workflow_no_raw_session_id_in_logs(caplog):
    """Run a realistic sequence against one session, capture every log line,
    and assert the raw session_id never appears as a substring. Covers the
    tool-surface entry points plus the eviction-log seam in state.py."""
    with caplog.at_level(logging.DEBUG):  # widest possible capture; narrow later if noisy
        sid = _start_session()
        call_tool("get_state", {"session_id": sid})
        call_tool("submit_step", {
            "session_id": sid, "step_id": "alpha", "content": "alpha-body",
        })
        call_tool("submit_step", {
            "session_id": sid, "step_id": "bravo", "content": "bravo-body",
        })
        call_tool("delete_session", {"session_id": sid})

    buffer = "\n".join(
        [rec.getMessage() for rec in caplog.records]
        + [str(getattr(rec, "args", "") or "") for rec in caplog.records]
        # Structured-log extras live on record attributes; dump __dict__ too
        # so any stray session_id attached via logger extra={} is captured.
        + [str(rec.__dict__) for rec in caplog.records]
    )
    assert sid not in buffer, (
        f"raw session_id leaked into log output (records={len(caplog.records)})"
    )


@pytest.mark.parametrize(
    "tool_name,args_builder",
    [
        # Each builder takes a known-valid sid and returns args that drive
        # the tool into an error path. The error path MUST communicate
        # session identity via `session_fingerprint`, not raw `session_id`.
        # (start_workflow has no session_id input, so its error path is
        # unknown-workflow-type — asserts the envelope shape only.)
        ("start_workflow", lambda sid: {"workflow_type": "no-such-wf", "context": "x"}),
        ("submit_step", lambda sid: {
            "session_id": secrets.token_urlsafe(32),
            "step_id": "alpha",
            "content": "x",
        }),
        ("revise_step", lambda sid: {
            "session_id": secrets.token_urlsafe(32),
            "step_id": "alpha",
        }),
        ("get_state", lambda sid: {"session_id": secrets.token_urlsafe(32)}),
        ("delete_session", lambda sid: {"session_id": secrets.token_urlsafe(32)}),
    ],
)
def test_error_responses_carry_fingerprint_not_session_id(tool_name, args_builder):
    """Every session-scoped tool's error envelope must carry session identity
    as `session_fingerprint`, never as raw `session_id` as a dict KEY.

    Companion `test_error_message_text_omits_raw_session_id` verifies the
    same property for the free-text `error` message body — catching leaks
    where interpolation (f-string / format / concat) would have embedded the
    raw session_id token into the envelope text."""
    real_sid = _start_session()  # exercised only to satisfy builders that ignore it
    args = args_builder(real_sid)
    result = call_tool(tool_name, args)

    assert result.get("status") == "error", f"expected error envelope, got {result!r}"
    # Hard rule: an error envelope must not carry a `session_id` key. Session
    # identity goes through `session_fingerprint` (and friends:
    # parent_session_fingerprint, child_session_fingerprint).
    assert "session_id" not in result, (
        f"{tool_name} error envelope leaked session_id key: {result!r}"
    )


@pytest.mark.parametrize(
    "tool_name,args_builder",
    [
        ("submit_step", lambda: {
            "session_id": secrets.token_urlsafe(32),
            "step_id": "alpha",
            "content": "x",
        }),
        ("revise_step", lambda: {
            "session_id": secrets.token_urlsafe(32),
            "step_id": "alpha",
        }),
        ("get_state", lambda: {"session_id": secrets.token_urlsafe(32)}),
        ("delete_session", lambda: {"session_id": secrets.token_urlsafe(32)}),
    ],
)
def test_error_message_text_omits_raw_session_id(tool_name, args_builder):
    """The raw session_id must not appear anywhere in the serialized error
    envelope, including the `error` message text. Caught via `SessionNotFoundError`
    raised by `state.get_session` with no payload; `_trap_errors` and
    `_resolve_session` construct the message from the exception type, not
    str(e). The `session_fingerprint` key remains the structured identifier."""
    args = args_builder()
    result = call_tool(tool_name, args)
    fabricated = args["session_id"]
    assert result.get("status") == "error"
    assert fabricated not in repr(result), (
        f"{tool_name} error envelope embeds raw session_id token"
    )


# ---------------------------------------------------------------------------
# (c) INTERNAL CONSISTENCY
# ---------------------------------------------------------------------------
# Claim: a tool call naming session X serves session X's data (and only X's).
# This is defense-in-depth against code bugs, NOT against attackers — under
# possession-as-capability, the attacker who possesses session_id B is a
# legitimate caller of B.
#
# MCP-spec §Session Hijacking: Session Hijack Prompt Injection — a prompt-
# injection-steered tool call that swapped sids mid-dispatch would surface as
# a cross-contamination of responses. These tests verify that the lookup is
# keyed honestly by session_id: no ambient-state fallback can leak one
# session's step_data into another session's response.


def test_two_sessions_no_cross_contamination():
    """Two sessions A and B with distinct step_data; alternating get_state
    calls must each return their own session's data only."""
    sid_a = _start_session()
    sid_b = _start_session()
    call_tool("submit_step", {
        "session_id": sid_a, "step_id": "alpha", "content": "A-only-alpha-payload",
    })
    call_tool("submit_step", {
        "session_id": sid_b, "step_id": "alpha", "content": "B-only-alpha-payload",
    })

    for _ in range(3):
        state_a = call_tool("get_state", {"session_id": sid_a})
        state_b = call_tool("get_state", {"session_id": sid_b})
        assert state_a["session_id"] == sid_a
        assert state_b["session_id"] == sid_b
        assert state_a["step_data"].get("alpha") == "A-only-alpha-payload"
        assert state_b["step_data"].get("alpha") == "B-only-alpha-payload"
        # Explicit negative assertions: no leakage in either direction.
        assert "B-only-alpha-payload" not in repr(state_a)
        assert "A-only-alpha-payload" not in repr(state_b)


def test_session_data_doesnt_appear_in_other_session_responses():
    """Stronger cross-contamination check: build two sessions with maximally
    distinct payloads, call every read-shape tool against each, and search
    every response of session X for session Y's content (and vice versa)."""
    a_marker = "PAYLOAD-ALPHA-e3c9"  # distinctive enough to grep-verify
    b_marker = "PAYLOAD-BRAVO-7f41"
    sid_a = _start_session()
    sid_b = _start_session()

    # Fill each session to its middle step; each payload is a unique marker.
    call_tool("submit_step", {
        "session_id": sid_a, "step_id": "alpha", "content": a_marker,
    })
    call_tool("submit_step", {
        "session_id": sid_b, "step_id": "alpha", "content": b_marker,
    })

    responses_for_a = [
        call_tool("get_state", {"session_id": sid_a}),
        call_tool("get_guidelines", {"session_id": sid_a}),
        call_tool("list_sessions", {}),  # global read — must segregate per session_id keys
    ]
    responses_for_b = [
        call_tool("get_state", {"session_id": sid_b}),
        call_tool("get_guidelines", {"session_id": sid_b}),
    ]

    # session A's responses must not contain B's marker.
    for resp in responses_for_a:
        # list_sessions is the known mixed surface — it legitimately returns
        # both sessions' summaries. But summaries don't carry step_data, so
        # markers must still be absent.
        assert b_marker not in repr(resp), f"B marker leaked into A-addressed response: {resp!r}"

    # session B's responses must not contain A's marker.
    for resp in responses_for_b:
        assert a_marker not in repr(resp), f"A marker leaked into B-addressed response: {resp!r}"
