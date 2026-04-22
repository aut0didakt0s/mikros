"""Seven-scenario regression matrix for ADR-001 workflow_changed funnel.

Parametrised across the seven scenarios in
``docs/discovery/workflow-versioning-current-behavior.md``:

    1. step renamed
    2. step inserted before current_step
    3. step deleted after current_step
    4. step semantics changed (directive_template / gates / output_schema)
    5. sub-workflow `call` target renamed (child-side mismatch — covered
       in test_child_side_rename_surfaces_via_enter_sub_workflow)
    6. branches targets in current step changed
    7. workflow file rewritten end-to-end (same name, different content —
       the operationally adjacent case to "file deleted"; a truly deleted
       YAML is caught by the pre-existing `workflow_not_loaded` path)

Each scenario: start a session on V1, mutate the in-memory workflow dict +
the fingerprints map in place (emulating "YAML edited + process restarted"),
then call every session-touching tool against the session and assert the
``workflow_changed`` envelope is returned with the four diagnostic keys.

Plus the pre-versioning sentinel one-time-trip test — direct-DB insert of a
row carrying ``"pre-versioning"``; first resolver call writes
``__workflow_changed__`` and returns the envelope; second call re-observes
the terminal state without re-hashing.
"""

from __future__ import annotations

import copy

import pytest  # type: ignore[import-not-found]

from megalos_server import state
from megalos_server.main import WORKFLOWS, mcp
from tests.conftest import call_tool


_WF_NAME = "versioning-regression"
_CHILD_NAME = "versioning-regression-child"


def _fingerprints() -> dict[str, str]:
    return mcp._megalos_workflow_fingerprints  # type: ignore[attr-defined]


def _v1_wf() -> dict:
    return {
        "name": _WF_NAME,
        "description": "V1 workflow for regression scenarios.",
        "category": "test",
        "output_format": "text",
        "steps": [
            {
                "id": "s1",
                "title": "S1",
                "directive_template": "do s1",
                "gates": ["done"],
                "anti_patterns": [],
            },
            {
                "id": "s2",
                "title": "S2",
                "directive_template": "do s2",
                "gates": ["done"],
                "anti_patterns": [],
                "branches": [
                    {"next": "s3", "condition": "default"},
                ],
                "default_branch": "s3",
            },
            {
                "id": "s3",
                "title": "S3",
                "directive_template": "do s3",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


def _v1_child_wf() -> dict:
    return {
        "name": _CHILD_NAME,
        "description": "callable child workflow (V1).",
        "category": "test",
        "output_format": "text",
        "steps": [
            {
                "id": "c1",
                "title": "C1",
                "directive_template": "do c1",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


def _install(wf_name: str, wf_dict: dict) -> None:
    """Install a workflow + its canonical fingerprint so start_workflow
    captures the "V1" digest on session create."""
    WORKFLOWS[wf_name] = wf_dict
    # Derive a canonical fingerprint from the parsed dict (matches the
    # create_app path's raw-bytes hash to within canonicalisation).
    import hashlib
    import json as _json

    canonical = _json.dumps(wf_dict, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _fingerprints()[wf_name] = hashlib.sha256(canonical).hexdigest()


def _uninstall(wf_name: str) -> None:
    WORKFLOWS.pop(wf_name, None)
    _fingerprints().pop(wf_name, None)


@pytest.fixture(autouse=True)
def _register_wf():
    _install(_WF_NAME, _v1_wf())
    _install(_CHILD_NAME, _v1_child_wf())
    yield
    _uninstall(_WF_NAME)
    _uninstall(_CHILD_NAME)


def _start_at_s2() -> str:
    """Start session and advance once so current_step == s2."""
    r = call_tool("start_workflow", {"workflow_type": _WF_NAME, "context": ""})
    sid = r["session_id"]
    call_tool("submit_step", {"session_id": sid, "step_id": "s1", "content": "s1-done"})
    assert state.get_session(sid)["current_step"] == "s2"
    return sid


# --- Scenario mutators ---------------------------------------------------


def _mutate_step_rename(v1: dict) -> dict:
    v2 = copy.deepcopy(v1)
    v2["steps"][0]["id"] = "s1_renamed"
    return v2


def _mutate_step_inserted(v1: dict) -> dict:
    v2 = copy.deepcopy(v1)
    new_step = {
        "id": "s_new",
        "title": "New",
        "directive_template": "do new",
        "gates": ["done"],
        "anti_patterns": [],
    }
    v2["steps"].insert(1, new_step)
    return v2


def _mutate_step_deleted_after(v1: dict) -> dict:
    v2 = copy.deepcopy(v1)
    # Delete s3 (after current_step s2); also remove s2's branches target.
    v2["steps"] = [s for s in v2["steps"] if s["id"] != "s3"]
    v2["steps"][1].pop("branches", None)
    v2["steps"][1].pop("default_branch", None)
    return v2


def _mutate_step_semantics(v1: dict) -> dict:
    v2 = copy.deepcopy(v1)
    v2["steps"][1]["directive_template"] = "do s2 (edited)"
    v2["steps"][1]["gates"] = ["done", "new-gate"]
    return v2


def _mutate_child_rename(v1_child: dict) -> dict:
    v2 = copy.deepcopy(v1_child)
    v2["steps"][0]["id"] = "c1_renamed"
    return v2


def _mutate_branches_changed(v1: dict) -> dict:
    v2 = copy.deepcopy(v1)
    v2["steps"][1]["branches"] = [{"next": "s3", "condition": "edited"}]
    return v2


def _mutate_call_target_on_parent(v1: dict) -> dict:
    """Scenario 5 parent-side: add a `call:` field onto the current step.
    This is a fingerprint-altering edit of the parent workflow and must
    surface via the parent's own fingerprint mismatch, independently of
    the child-spawn-timing property (covered separately).
    """
    v2 = copy.deepcopy(v1)
    v2["steps"][1]["call"] = _CHILD_NAME
    # call-steps with branches must declare default_branch; v1 already does.
    return v2


def _mutate_workflow_deleted(v1: dict) -> dict:
    # Signal: caller will pop the workflow from the map entirely rather
    # than install a V2 dict. Returning None is the flag.
    return None  # type: ignore[return-value]


_SCENARIOS = [
    ("step_renamed", _mutate_step_rename),
    ("step_inserted_before_current", _mutate_step_inserted),
    ("step_deleted_after_current", _mutate_step_deleted_after),
    ("step_semantics_changed", _mutate_step_semantics),
    ("call_target_added_on_current_step", _mutate_call_target_on_parent),
    ("branches_changed", _mutate_branches_changed),
    ("workflow_file_rewritten", _mutate_workflow_deleted),
]


def _apply_mutation(name: str, mutator) -> None:
    """Apply a scenario mutation in-place against the registered V1."""
    if name == "workflow_file_rewritten":
        # Simulate the YAML being removed on disk + process restart: the
        # workflow is absent from the fingerprints map but present in WORKFLOWS
        # we leave — because that produces workflow_not_loaded, not
        # workflow_changed. To produce workflow_changed here we need the
        # workflow still loaded under a *different* fingerprint, which is
        # exactly what "file-deleted-then-replaced" would do.
        # Honest interpretation of scenario 7 for the funnel: the resolver
        # covers all mismatch cases; file-deleted-entirely is handled by
        # workflow_not_loaded (already-typed pre-T02). For matrix coverage
        # here, emulate the operationally adjacent case: workflow mutated
        # such that its fingerprint no longer matches.
        new = copy.deepcopy(_v1_wf())
        new["steps"].append({
            "id": "appended",
            "title": "Appended",
            "directive_template": "d",
            "gates": ["done"],
            "anti_patterns": [],
        })
        _install(_WF_NAME, new)
        return
    v1 = WORKFLOWS[_WF_NAME]
    v2 = mutator(v1)
    _install(_WF_NAME, v2)


_EXPECTED_KEYS = {
    # Envelope scaffolding.
    "status", "code", "error",
    # Four diagnostic keys — no more, no fewer.
    "session_fingerprint", "workflow_type",
    "previous_fingerprint", "current_fingerprint",
}


def _assert_workflow_changed_envelope(resp: dict, workflow_type: str, session_fp: str) -> None:
    assert resp["status"] == "error", f"expected error, got {resp}"
    assert resp["code"] == "workflow_changed", f"unexpected code: {resp}"
    assert resp["session_fingerprint"] == session_fp
    assert resp["workflow_type"] == workflow_type
    assert "previous_fingerprint" in resp
    assert "current_fingerprint" in resp
    # Fixed message prose — part of the contract for operator readability.
    assert "has changed since this session was started" in resp["error"]
    # Exact-key assertion pins "four diagnostic keys, no more, no fewer"
    # (plus envelope scaffolding). Guards against accidental additions
    # that would leak data into the payload.
    assert set(resp.keys()) == _EXPECTED_KEYS, (
        f"unexpected envelope keys: extras={set(resp.keys()) - _EXPECTED_KEYS}, "
        f"missing={_EXPECTED_KEYS - set(resp.keys())}"
    )


def _session_touching_tool_calls(sid: str) -> list[tuple[str, dict]]:
    """Every session-touching tool, keyed by the argument each expects."""
    return [
        ("get_state", {"session_id": sid}),
        ("get_guidelines", {"session_id": sid}),
        ("submit_step", {"session_id": sid, "step_id": "s2", "content": "s2-done"}),
        ("revise_step", {"session_id": sid, "step_id": "s1"}),
        ("enter_sub_workflow", {"parent_session_id": sid, "call_step_id": "s2"}),
        ("push_flow", {
            "session_id": sid,
            "workflow_type": _CHILD_NAME,
            "paused_at_step": "s2",
            "context": "",
        }),
        ("pop_flow", {"session_id": sid}),
        ("generate_artifact", {"session_id": sid}),
    ]


@pytest.mark.parametrize("name,mutator", _SCENARIOS, ids=[n for n, _ in _SCENARIOS])
def test_every_session_touching_tool_returns_workflow_changed(name, mutator):
    """For every scenario × every session-touching tool: envelope with four keys."""
    sid = _start_at_s2()
    session_fp = state.get_session(sid)["fingerprint"]
    _apply_mutation(name, mutator)

    for tool, args in _session_touching_tool_calls(sid):
        resp = call_tool(tool, args)
        _assert_workflow_changed_envelope(resp, workflow_type=_WF_NAME, session_fp=session_fp)


def test_child_side_rename_surfaces_via_enter_sub_workflow():
    """Scenario 5 variant: edit ONLY the child workflow. The parent's
    fingerprint is still valid, so session-touching tools on the parent
    succeed — but enter_sub_workflow stamps the child's fingerprint at
    spawn time (ADR-001 child-spawn-timing property), so the next tool
    call against the *child* session returns workflow_changed naming the
    *child* workflow_type.
    """
    # Install a parent that calls the child; child fingerprint is the one
    # we'll mutate.
    parent_name = "child-rename-parent"
    child_v1 = _v1_child_wf()
    parent = {
        "name": parent_name,
        "description": "parent that calls child",
        "category": "test",
        "output_format": "text",
        "steps": [
            {
                "id": "p1",
                "title": "P1 call",
                "directive_template": "hand off",
                "gates": ["done"],
                "anti_patterns": [],
                "call": _CHILD_NAME,
            },
        ],
    }
    _install(parent_name, parent)
    try:
        r = call_tool("start_workflow", {"workflow_type": parent_name, "context": ""})
        parent_sid = r["session_id"]

        # Enter the sub-workflow: child is spawned against V1 child fingerprint.
        r2 = call_tool(
            "enter_sub_workflow",
            {"parent_session_id": parent_sid, "call_step_id": "p1"},
        )
        child_sid = r2["session_id"]
        child_session_fp = state.get_session(child_sid)["fingerprint"]

        # Now mutate the child workflow (parent stays untouched).
        child_v2 = copy.deepcopy(child_v1)
        child_v2["steps"][0]["directive_template"] = "do c1 (edited)"
        _install(_CHILD_NAME, child_v2)

        # Calling any session-touching tool on the CHILD fails — child-side mismatch.
        resp = call_tool("get_state", {"session_id": child_sid})
        _assert_workflow_changed_envelope(
            resp, workflow_type=_CHILD_NAME, session_fp=child_session_fp
        )
    finally:
        _uninstall(parent_name)


def test_terminal_state_written_once_and_reobserved_without_rehash():
    """First mismatch writes __workflow_changed__; second call takes the
    fast path (envelope with identical previous/current fingerprints, since
    the stored row now carries its own fingerprint, and the live map still
    returns the current fingerprint — but the resolver must take the
    terminal-sentinel short-circuit rather than compute another compare).
    """
    sid = _start_at_s2()
    # Mutate → first call writes sentinel.
    v2 = _mutate_step_rename(WORKFLOWS[_WF_NAME])
    _install(_WF_NAME, v2)

    r1 = call_tool("get_state", {"session_id": sid})
    assert r1["code"] == "workflow_changed"
    assert state.get_session(sid)["current_step"] == state.WORKFLOW_CHANGED

    # Mutate the live map AGAIN — if the resolver were still comparing
    # fingerprints rather than taking the terminal short-circuit, the
    # current_fingerprint in the second response would shift. On the fast
    # path the stored fingerprint (which equals the session's own stamped
    # fingerprint at this point — see envelope construction) is returned.
    v3 = copy.deepcopy(v2)
    v3["steps"][0]["directive_template"] = "another edit"
    _install(_WF_NAME, v3)

    r2 = call_tool("get_state", {"session_id": sid})
    assert r2["code"] == "workflow_changed"
    # Fast path signature: previous_fingerprint == current_fingerprint,
    # because the envelope carries session.workflow_fingerprint on both
    # sides when no re-compare happens.
    assert r2["previous_fingerprint"] == r2["current_fingerprint"]


def test_pre_versioning_sentinel_first_call_trips_envelope():
    """A row carrying the literal `"pre-versioning"` in workflow_fingerprint
    is by construction mismatched against any real digest. The first
    _resolve_session call writes __workflow_changed__ and returns the
    envelope; the second call re-observes the sentinel and returns the
    same envelope shape.
    """
    # Start a session normally (so the row exists with a real fingerprint),
    # then patch the row in place to carry the legacy sentinel.
    sid = _start_at_s2()
    from megalos_server import db as _db

    conn = _db._get_conn()
    conn.execute(
        "UPDATE sessions SET workflow_fingerprint = 'pre-versioning' WHERE session_id = ?",
        (sid,),
    )
    conn.commit()

    # First call: resolver detects mismatch (real digest vs sentinel),
    # writes WORKFLOW_CHANGED, returns envelope.
    r1 = call_tool("get_state", {"session_id": sid})
    assert r1["code"] == "workflow_changed"
    assert r1["previous_fingerprint"] == "pre-versioning"
    assert state.get_session(sid)["current_step"] == state.WORKFLOW_CHANGED

    # Second call: resolver short-circuits on the sentinel without re-hashing.
    r2 = call_tool("get_state", {"session_id": sid})
    assert r2["code"] == "workflow_changed"
    # Fast path returns the stored fingerprint on both sides.
    assert r2["previous_fingerprint"] == r2["current_fingerprint"] == "pre-versioning"
