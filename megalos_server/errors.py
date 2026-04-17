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
