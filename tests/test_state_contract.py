"""Contract tests for megalos_server.state.

Named invariant — 'detached snapshot': get_session returns a freshly-constructed
dict each call; in-place mutations do not persist. Persist via update_session
(or the dedicated RMW helpers like increment_retry / set_escalation / etc).
"""

import pytest  # type: ignore[import-not-found]

from megalos_server import state


def test_get_session_returns_detached_snapshot():
    sid = state.create_session("wf", current_step="first")
    s1 = state.get_session(sid)

    # Mutate returned dict and its nested dicts in place.
    s1["step_data"]["first"] = "in-place-payload"
    s1["retry_counts"]["first"] = 99
    s1["current_step"] = "mutated-step"

    # Re-fetch — original values must be preserved.
    s2 = state.get_session(sid)
    assert s2["step_data"] == {}
    assert s2["retry_counts"] == {}
    assert s2["current_step"] == "first"
    # And s1 vs s2 must not share nested references.
    assert s1["step_data"] is not s2["step_data"]


def test_update_session_persists_step_data_and_current_step():
    sid = state.create_session("wf", current_step="first")
    state.update_session(sid, step_data={"first": "ok"}, current_step="second")
    s = state.get_session(sid)
    assert s["step_data"] == {"first": "ok"}
    assert s["current_step"] == "second"


def test_missing_session_raises_session_not_found_error():
    from megalos_server.errors import SessionNotFoundError

    with pytest.raises(SessionNotFoundError):
        state.get_session("nope")
    with pytest.raises(SessionNotFoundError):
        state.update_session("nope", current_step="x")


def test_set_updated_at_for_test_backdates_only():
    sid = state.create_session("wf", current_step="first")
    state._set_updated_at_for_test(sid, "1999-01-01T00:00:00+00:00")
    s = state.get_session(sid)
    assert s["updated_at"] == "1999-01-01T00:00:00+00:00"
    assert s["current_step"] == "first"
