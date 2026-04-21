"""Tests for the Identity value-object access-check seam.

The seam is structurally a no-op today — every request + session carries
ANONYMOUS_IDENTITY. The tests nail down the shape the Phase G bearer-auth
path will depend on: Identity is importable, owner_identity is attached at
every session-dict materialization path, the access-check short-circuits
on mismatch with a structured cross_session_access_denied envelope, and
the regression canary exercises the mismatch branch end-to-end via the
contextvar that CallerIdentityMiddleware populates."""

from conftest import call_tool
from megalos_server import state
from megalos_server.identity import ANONYMOUS_IDENTITY, Identity
from megalos_server.identity_ctx import caller_identity_var
from megalos_server.main import WORKFLOWS
from megalos_server.tools import _resolve_session


def setup_function():
    state.clear_sessions()


def _any_workflow_type() -> str:
    result = call_tool("list_workflows", {})
    return result["workflows"][0]["name"]


def test_identity_type_shape():
    assert ANONYMOUS_IDENTITY == {"kind": "anonymous"}
    # Type alias must be importable — exercising the symbol keeps the
    # import surface covered and catches accidental removal.
    anon: Identity = {"kind": "anonymous"}
    assert anon["kind"] == "anonymous"


def test_owner_identity_on_session_create():
    wf = _any_workflow_type()
    r = call_tool("start_workflow", {"workflow_type": wf, "context": "test"})
    sid = r["session_id"]
    loaded = state.get_session(sid)
    assert loaded["owner_identity"] == ANONYMOUS_IDENTITY


def test_owner_identity_on_session_load():
    sid = state.create_session("canonical", current_step="step1")
    # Round-trip through the DB: get_session goes via _row_to_session, which
    # is the derivation path that must attach owner_identity on every load.
    hydrated = state.get_session(sid)
    assert hydrated["owner_identity"] == ANONYMOUS_IDENTITY
    # list_sessions goes via a separate code path (not _row_to_session) —
    # assert parity so tools iterating summaries see the same shape.
    summaries = state.list_sessions()
    match = [s for s in summaries if s["session_id"] == sid]
    assert len(match) == 1
    assert match[0]["owner_identity"] == ANONYMOUS_IDENTITY


def test_access_check_passes_anonymous():
    wf = _any_workflow_type()
    r = call_tool("start_workflow", {"workflow_type": wf, "context": "test"})
    sid = r["session_id"]
    # Happy path — anonymous caller_identity (default) vs anonymous
    # owner_identity. get_state must not return an error envelope.
    result = call_tool("get_state", {"session_id": sid})
    assert "error" not in result
    assert result["session_id"] == sid


def test_cross_session_access_denied_envelope_shape():
    wf = _any_workflow_type()
    r = call_tool("start_workflow", {"workflow_type": wf, "context": "test"})
    sid = r["session_id"]
    fingerprint = state._compute_fingerprint(sid)

    # Direct-invoke the access-check at _resolve_session: the full call_tool
    # dispatch re-enters CallerIdentityMiddleware, which today always forces
    # the contextvar back to ANONYMOUS_IDENTITY and would mask the mismatch.
    # Phase G will emit a non-anonymous identity here; this test simulates
    # that future state at the narrowest legitimate surface — the single
    # planting site of the access-check.
    fake_bearer: Identity = {"kind": "bearer", "subject": "shape-test"}
    token = caller_identity_var.set(fake_bearer)
    try:
        resolved, err = _resolve_session(sid, WORKFLOWS)
    finally:
        caller_identity_var.reset(token)

    assert resolved is None
    assert err is not None
    # The envelope must carry cross_session_access_denied + session_fingerprint,
    # never the raw session_id. Fingerprint-only payload is the T02 contract
    # that the CI grep gate enforces.
    assert err["status"] == "error"
    assert err["code"] == "cross_session_access_denied"
    assert err["session_fingerprint"] == fingerprint
    assert "session_id" not in err
    assert isinstance(err["error"], str)
    assert fingerprint in err["error"]


def test_seam_regression_canary():
    """seam shape regression canary — if this test breaks, the bearer-auth path will break."""
    wf = _any_workflow_type()
    r = call_tool("start_workflow", {"workflow_type": wf, "context": "test"})
    sid = r["session_id"]
    fingerprint = state._compute_fingerprint(sid)

    # Construct a non-anonymous identity of the shape Phase G will emit.
    # The session owner remains ANONYMOUS_IDENTITY (attached at hydrate time
    # by _row_to_session). The access-check must reject the mismatch.
    fake_bearer: Identity = {"kind": "bearer", "subject": "canary-test"}
    token = caller_identity_var.set(fake_bearer)
    try:
        resolved, err = _resolve_session(sid, WORKFLOWS)
    finally:
        caller_identity_var.reset(token)

    # Seam shape: structured envelope with the exact error code and a
    # fingerprint-only identity field. The same assertions form the Phase G
    # acceptance shape — if they drift here, bearer auth breaks on landing.
    assert resolved is None
    assert err is not None
    assert err["status"] == "error"
    assert err["code"] == "cross_session_access_denied"
    assert err["session_fingerprint"] == fingerprint
    assert "session_id" not in err
