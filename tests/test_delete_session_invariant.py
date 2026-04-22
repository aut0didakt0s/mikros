"""ADR-001 invariant: delete_session is the sole recovery path for
workflow_changed sessions and MUST NOT route through _resolve_session.

A future refactor that routed delete_session through the funnel would
make a mutated session undeletable — the only recovery would be manual
DB surgery. This test pins the invariant with a scripted attack:

    1. Create a session.
    2. Mutate the workflow in-memory (emulate YAML-edit-plus-restart).
    3. Confirm any session-touching tool returns workflow_changed.
    4. Call delete_session — MUST succeed, MUST NOT emit the envelope.

If the invariant regresses, step (4) will return the workflow_changed
envelope rather than the delete confirmation.
"""

from __future__ import annotations

import copy
import hashlib
import json

from megalos_server import state
from megalos_server.main import WORKFLOWS, mcp
from tests.conftest import call_tool


_WF = "delete-invariant"


def _fingerprints() -> dict[str, str]:
    return mcp._megalos_workflow_fingerprints  # type: ignore[attr-defined]


def _install(name: str, wf: dict) -> None:
    WORKFLOWS[name] = wf
    canonical = json.dumps(wf, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _fingerprints()[name] = hashlib.sha256(canonical).hexdigest()


def _wf_v1() -> dict:
    return {
        "name": _WF,
        "description": "workflow for delete invariant test",
        "category": "test",
        "output_format": "text",
        "steps": [
            {
                "id": "only",
                "title": "Only",
                "directive_template": "do",
                "gates": ["done"],
                "anti_patterns": [],
            },
        ],
    }


def test_delete_session_bypasses_resolver_on_workflow_changed_session():
    _install(_WF, _wf_v1())
    try:
        r = call_tool("start_workflow", {"workflow_type": _WF, "context": ""})
        sid = r["session_id"]

        # Mutate: workflow edited under the live session.
        v2 = copy.deepcopy(_wf_v1())
        v2["steps"][0]["directive_template"] = "edited"
        _install(_WF, v2)

        # First, confirm a session-touching tool trips the funnel.
        resp = call_tool("get_state", {"session_id": sid})
        assert resp["code"] == "workflow_changed", (
            f"expected funnel to trip, got {resp}"
        )
        # Session now carries the terminal sentinel.
        assert state.get_session(sid)["current_step"] == state.WORKFLOW_CHANGED

        # Invariant: delete_session succeeds against the terminal session.
        # It must NOT return the workflow_changed envelope.
        delete_resp = call_tool("delete_session", {"session_id": sid})
        assert delete_resp.get("status") != "error", (
            f"delete_session emitted an error — invariant broken: {delete_resp}"
        )
        assert delete_resp.get("code") != "workflow_changed", (
            "delete_session routed through _resolve_session — ADR-001 invariant broken"
        )
        # Confirms the session is gone.
        assert delete_resp["session_id"] == sid
        assert delete_resp["current_step"] == state.WORKFLOW_CHANGED
    finally:
        WORKFLOWS.pop(_WF, None)
        _fingerprints().pop(_WF, None)


def test_delete_session_works_on_workflow_deleted_entirely():
    """Companion invariant: when the workflow YAML is gone entirely
    (workflow_not_loaded path, pre-existing), delete_session is still
    the only way out and must succeed — it already bypasses the
    resolver, so no resolver-path check is consulted.
    """
    _install(_WF, _wf_v1())
    r = call_tool("start_workflow", {"workflow_type": _WF, "context": ""})
    sid = r["session_id"]
    # Remove the workflow entirely — workflow_not_loaded would fire on any
    # session-touching tool. But delete_session bypasses the resolver and
    # completes.
    WORKFLOWS.pop(_WF, None)
    _fingerprints().pop(_WF, None)

    delete_resp = call_tool("delete_session", {"session_id": sid})
    assert delete_resp.get("status") != "error", delete_resp
    assert delete_resp["session_id"] == sid
