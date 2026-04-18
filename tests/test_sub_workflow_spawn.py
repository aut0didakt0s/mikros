"""Tests for M004/S02/T01: child session spawn via enter_sub_workflow.

Covers:
- child session creation with bidirectional link
- pre-condition rejects (missing parent, wrong step, step-has-no-call, child-in-flight)
- call_context_from extraction
- migration idempotency via PRAGMA table_info
"""

import os
import sqlite3

import pytest

from megalos_server import db, state
from megalos_server.main import WORKFLOWS
from tests.conftest import call_tool


_PARENT = "spawn-parent"
_CHILD = "spawn-child"


def _parent_wf() -> dict:
    return {
        "name": _PARENT,
        "description": "parent with call step",
        "category": "test",
        "output_format": "text",
        "steps": [
            {
                "id": "p1",
                "title": "Parent step 1",
                "directive_template": "do p1",
                "gates": ["done"],
                "anti_patterns": [],
            },
            {
                "id": "p2",
                "title": "Parent call step",
                "directive_template": "hand off",
                "gates": ["done"],
                "anti_patterns": [],
                "call": _CHILD,
            },
            {
                "id": "p3",
                "title": "Parent step 3",
                "directive_template": "do p3",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


def _parent_wf_with_ccf() -> dict:
    wf = _parent_wf()
    wf["steps"][1]["call_context_from"] = "step_data.p1.topic"
    return wf


def _child_wf() -> dict:
    return {
        "name": _CHILD,
        "description": "child workflow",
        "category": "test",
        "output_format": "text",
        "steps": [
            {
                "id": "c1",
                "title": "Child step 1",
                "directive_template": "child work",
                "gates": ["done"],
                "anti_patterns": [],
            },
            {
                "id": "c2",
                "title": "Child step 2",
                "directive_template": "child finish",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


@pytest.fixture(autouse=True)
def _register_wfs():
    WORKFLOWS[_PARENT] = _parent_wf()
    WORKFLOWS[_CHILD] = _child_wf()
    yield
    WORKFLOWS.pop(_PARENT, None)
    WORKFLOWS.pop(_CHILD, None)


def _start_parent(context: str = "") -> str:
    r = call_tool("start_workflow", {"workflow_type": _PARENT, "context": context})
    return r["session_id"]


def _advance_parent_to_call_step(parent_sid: str, p1_content: str = "first-content") -> None:
    r = call_tool("submit_step", {"session_id": parent_sid, "step_id": "p1", "content": p1_content})
    assert "error" not in r, r


# --- T01 tests --------------------------------------------------------------


def test_spawn_creates_child_session():
    parent_sid = _start_parent()
    _advance_parent_to_call_step(parent_sid)
    r = call_tool("enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p2"})
    assert r.get("session_id") and r["session_id"] != parent_sid


def test_spawn_sets_bidirectional_link():
    parent_sid = _start_parent()
    _advance_parent_to_call_step(parent_sid)
    r = call_tool("enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p2"})
    child_sid = r["session_id"]
    parent = state.get_session(parent_sid)
    child = state.get_session(child_sid)
    assert parent["called_session"] == child_sid and child["parent_session_id"] == parent_sid


def test_spawn_rejects_when_parent_session_missing():
    r = call_tool("enter_sub_workflow", {"parent_session_id": "no-such-sid", "call_step_id": "p2"})
    assert r["code"] == "session_not_found"


def test_spawn_rejects_when_parent_wrong_step():
    parent_sid = _start_parent()
    # parent is at p1; call with call_step_id=p2
    r = call_tool("enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p2"})
    assert r["code"] == "out_of_order_submission"


def test_spawn_rejects_when_step_has_no_call_field():
    parent_sid = _start_parent()
    # parent is at p1, which has no `call`
    r = call_tool("enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p1"})
    assert r["code"] == "invalid_argument"


def test_spawn_rejects_when_child_already_in_flight():
    parent_sid = _start_parent()
    _advance_parent_to_call_step(parent_sid)
    first = call_tool("enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p2"})
    first_child_sid = first["session_id"]
    second = call_tool("enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p2"})
    assert second["code"] == "sub_workflow_pending" and second["child_session_id"] == first_child_sid


def test_call_context_from_extracts_subtree():
    WORKFLOWS[_PARENT] = _parent_wf_with_ccf()
    parent_sid = _start_parent()
    # p1 content must be JSON with a "topic" field so step_data.p1.topic resolves
    _advance_parent_to_call_step(parent_sid, p1_content='{"topic": "renewable energy"}')
    r = call_tool("enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p2"})
    assert r["context"] == "renewable energy"


def test_call_context_from_absent_empty_child_context():
    # _parent_wf() has no call_context_from on p2
    parent_sid = _start_parent()
    _advance_parent_to_call_step(parent_sid)
    r = call_tool("enter_sub_workflow", {"parent_session_id": parent_sid, "call_step_id": "p2"})
    assert r["context"] == ""


def test_migration_idempotent_on_existing_db(tmp_path, monkeypatch):
    # Use a dedicated tmp DB path separate from the autouse conftest DB.
    db_path = str(tmp_path / "mig_idempotent.db")
    monkeypatch.setenv("MEGALOS_DB_PATH", db_path)
    db._reset_for_test()
    db._get_conn()  # first init
    db._reset_for_test()
    conn = db._get_conn()  # second init — migration must not raise or duplicate
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    assert {"called_session", "parent_session_id"}.issubset(cols)
