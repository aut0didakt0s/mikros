"""Error vocabulary, size caps, and response helper for megalos MCP server."""

import os

SESSION_NOT_FOUND = "session_not_found"
INVALID_ARGUMENT = "invalid_argument"
OVERSIZE_PAYLOAD = "oversize_payload"
SCHEMA_VIOLATION = "schema_violation"
SESSION_CAP_EXCEEDED = "session_cap_exceeded"
WORKFLOW_NOT_LOADED = "workflow_not_loaded"
UNKNOWN_ARTIFACT = "unknown_artifact"
OUT_OF_ORDER_SUBMISSION = "out_of_order_submission"
SESSION_ESCALATED = "session_escalated"
WORKFLOW_COMPLETE = "workflow_complete"
CONCURRENT_WRITE_CONFLICT = "concurrent_write_conflict"  # reserved for S02
SKIPPED_PREDECESSOR_REFERENCE = "skipped_predecessor_reference"
CALL_WITH_COLLECT = "call_with_collect"
CALL_WITH_INTERMEDIATE_ARTIFACTS = "call_with_intermediate_artifacts"
CALL_CONTEXT_FROM_WITHOUT_CALL = "call_context_from_without_call"
CALL_INVALID_CONTEXT_REF = "call_invalid_context_ref"
CALL_BRANCHES_WITHOUT_DEFAULT = "call_branches_without_default"
UNKNOWN_CALL_TARGET = "unknown_call_target"
CALL_CYCLE_DETECTED = "call_cycle_detected"
SUB_WORKFLOW_RUNTIME_NOT_IMPLEMENTED = "sub_workflow_runtime_not_implemented"
SUB_WORKFLOW_PARENT_OWNED = "sub_workflow_parent_owned"
SUB_WORKFLOW_PENDING = "sub_workflow_pending"
SESSION_STACK_FULL = "session_stack_full"
CALL_STEP_REQUIRES_ENTER_SUB_WORKFLOW = "call_step_requires_enter_sub_workflow"

CONTENT_MAX = 262_144
ARTIFACT_MAX = 1_048_576
YAML_MAX = 512_000

SESSION_CAP = int(os.environ.get("MEGALOS_SESSION_CAP", "500"))


def get_session_cap() -> int:
    """Re-read MEGALOS_SESSION_CAP on each call. Tests use monkeypatch.setenv."""
    return int(os.environ.get("MEGALOS_SESSION_CAP", "500"))


def error_response(code: str, error: str, **fields: object) -> dict:
    """Return {"status": "error", "code": code, "error": error, **fields}."""
    return {"status": "error", "code": code, "error": error, **fields}
