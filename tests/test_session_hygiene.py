"""Tests for session cap enforcement and TTL expiry."""

from datetime import datetime, timedelta, timezone

from conftest import call_tool
from megalos_server import state


def _any_workflow_type():
    """Return the first available workflow type name."""
    result = call_tool("list_workflows", {})
    return result["workflows"][0]["name"]


def setup_function():
    state.clear_sessions()


# --- Session cap ---

def test_cap_rejects_sixth_session():
    wf = _any_workflow_type()
    for _ in range(5):
        r = call_tool("start_workflow", {"workflow_type": wf, "context": "test"})
        assert "session_id" in r

    r = call_tool("start_workflow", {"workflow_type": wf, "context": "test"})
    assert "error" in r
    assert "5" in r["error"]
    assert "active_sessions" in r
    assert len(r["active_sessions"]) == 5


def test_cap_allows_after_delete():
    wf = _any_workflow_type()
    sids = []
    for _ in range(5):
        r = call_tool("start_workflow", {"workflow_type": wf, "context": "test"})
        sids.append(r["session_id"])

    call_tool("delete_session", {"session_id": sids[0]})
    r = call_tool("start_workflow", {"workflow_type": wf, "context": "test"})
    assert "session_id" in r


def test_cap_allows_after_complete():
    wf = _any_workflow_type()
    sids = []
    for _ in range(5):
        r = call_tool("start_workflow", {"workflow_type": wf, "context": "test"})
        sids.append(r["session_id"])

    # Complete first session so it no longer counts as active
    state.update_session(sids[0], current_step=state.COMPLETE)

    r = call_tool("start_workflow", {"workflow_type": wf, "context": "test"})
    assert "session_id" in r


# --- TTL expiry ---

def test_expire_deletes_old_sessions():
    sid = state.create_session("test_wf", current_step="step1")
    # Backdate updated_at by 25 hours
    old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    state._set_updated_at_for_test(sid, old_time)

    expired = state.expire_sessions(ttl_hours=24)
    assert sid in expired
    assert state.list_sessions() == []


def test_expire_keeps_fresh_sessions():
    state.create_session("test_wf", current_step="step1")
    expired = state.expire_sessions(ttl_hours=24)
    assert expired == []
    assert len(state.list_sessions()) == 1


def test_expire_cleans_completed_sessions_too():
    sid = state.create_session("test_wf", current_step=state.COMPLETE)
    old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    state._set_updated_at_for_test(sid, old_time)

    expired = state.expire_sessions(ttl_hours=24)
    assert sid in expired


# --- count_active ---

def test_count_active_excludes_completed():
    state.create_session("wf", current_step="step1")
    state.create_session("wf", current_step=state.COMPLETE)
    assert state.count_active() == 1
