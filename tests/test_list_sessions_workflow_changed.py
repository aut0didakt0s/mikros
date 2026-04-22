"""`list_sessions` third status vocabulary: ``workflow_changed``.

Per ADR-001, rows in the ``__workflow_changed__`` terminal state surface
with ``status: "workflow_changed"``, alongside the existing ``active`` /
``completed`` values. The branch already existed in ``state.list_sessions``
in T01 but was dormant — no write path produced the sentinel. T02's
activation of the funnel is what first exercises it end-to-end.
"""

from __future__ import annotations

import copy
import hashlib
import json

from megalos_server import state
from megalos_server.main import WORKFLOWS, mcp
from tests.conftest import call_tool


_WF = "list-sessions-workflow-changed"


def _fingerprints() -> dict[str, str]:
    return mcp._megalos_workflow_fingerprints  # type: ignore[attr-defined]


def _install(name: str, wf: dict) -> None:
    WORKFLOWS[name] = wf
    canonical = json.dumps(wf, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _fingerprints()[name] = hashlib.sha256(canonical).hexdigest()


def _wf() -> dict:
    return {
        "name": _WF,
        "description": "workflow",
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


def test_list_sessions_reports_workflow_changed_status_for_terminal_rows():
    _install(_WF, _wf())
    try:
        # Two sessions: one active, one about to be terminal.
        r_active = call_tool("start_workflow", {"workflow_type": _WF, "context": ""})
        active_sid = r_active["session_id"]
        r_target = call_tool("start_workflow", {"workflow_type": _WF, "context": ""})
        target_sid = r_target["session_id"]

        # Mutate workflow.
        v2 = copy.deepcopy(_wf())
        v2["steps"][0]["directive_template"] = "edited"
        _install(_WF, v2)

        # Active session is still active (no resolver call happened against it).
        # Target session: trip the funnel.
        call_tool("get_state", {"session_id": target_sid})
        assert state.get_session(target_sid)["current_step"] == state.WORKFLOW_CHANGED

        sessions = call_tool("list_sessions", {})["sessions"]
        by_id = {s["session_id"]: s for s in sessions}

        assert by_id[target_sid]["status"] == "workflow_changed"
        # Active remains "active" — list_sessions does not re-check per ADR-001.
        # But: the OTHER session's resolver hasn't fired, so the row's
        # current_step is still the first step's id (unchanged), status active.
        assert by_id[active_sid]["status"] == "active"

        # Status vocabulary is the full set {active, completed, workflow_changed}.
        status_vocab = {s["status"] for s in sessions}
        assert status_vocab <= {"active", "completed", "workflow_changed"}
        assert "workflow_changed" in status_vocab
    finally:
        WORKFLOWS.pop(_WF, None)
        _fingerprints().pop(_WF, None)
