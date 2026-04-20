"""Runtime executor for ``action: mcp_tool_call`` steps.

Workflow steps whose ``action`` is ``mcp_tool_call`` have no LLM turn; this
module resolves their ``args`` against session step_data, invokes the
sync MCP client, and returns a wrapped envelope suitable for writing into
step_data.

Two public helpers:

- ``resolve_args`` — recursive walker. String leaves that are a whole-match
  ``${step_data.<path>}`` are substituted via the existing ``_resolve_ref``
  resolver (which returns ``_REF_ABSENT`` for missing paths and raises
  ``_SkippedPredecessor`` on cascade). Non-ref strings and other scalars are
  pass-through. Returns a value tree with `_REF_ABSENT` sentinels preserved
  so the executor can fail the step cleanly rather than sending a sentinel
  to the MCP tool.

- ``execute_mcp_tool_call_step`` — single-step executor. Resolves args,
  checks for unresolved refs, picks an effective timeout, calls
  ``mcp_client.call``, and maps the ``CallOutcome`` to an envelope dict of
  the shape ``{"ok": true, "value": ...}`` or
  ``{"ok": false, "error": {"message": ...}}``. Cascade propagation
  (``_SkippedPredecessor``) is the caller's responsibility.

Divergence from silent-None semantics for unresolved refs: a `_REF_ABSENT`
leaf after argument resolution writes an executor-level error envelope
(``unresolved arg ref: ${step_data.<path>}``) rather than passing the
sentinel to the MCP tool. A sentinel value sent to a remote tool produces
a cryptic server-side schema-validation failure that is harder for
workflow authors to diagnose than the local error. This is the one place
we deliberately differ from the LLM-path ref-missing semantics; elsewhere,
`_REF_ABSENT` propagates (e.g. preconditions evaluate false).
"""

from __future__ import annotations

import logging
from typing import Any

from megalos_server import mcp_client
from megalos_server.mcp_client import (
    CallOutcome,
    Ok,
    ProtocolError,
    SchemaValidationError,
    TimeoutError as McpTimeoutError,
    ToolExecutionError,
    TransportError,
)
from megalos_server.mcp_registry import Registry

_log = logging.getLogger("megalos_server.workflow")

# Default effective timeout when neither step-level nor registry-level is set.
# Mirrors mcp_client._DEFAULT_TIMEOUT_S but duplicated rather than imported so
# this module does not reach into mcp_client's private names.
_DEFAULT_TIMEOUT_S: float = 30.0


def resolve_args(
    raw: Any,
    step_data: dict,
    skipped_set: set[str],
    referencing_step_id: str,
) -> Any:
    """Recursively resolve ``${step_data.<path>}`` refs in an args tree.

    String leaves that exactly match a ref-path are replaced with the
    resolved value (possibly ``_REF_ABSENT``). All other leaves are
    pass-through. Raises ``_SkippedPredecessor`` (from tools) if any
    ref points at a skipped predecessor.

    Import of ``_resolve_ref`` is function-local to avoid a circular import
    (tools → mcp_executor, mcp_executor → tools would be cyclic at module
    load time).
    """
    from megalos_server.tools import _resolve_ref  # local import: see docstring

    if isinstance(raw, dict):
        return {
            k: resolve_args(v, step_data, skipped_set, referencing_step_id)
            for k, v in raw.items()
        }
    if isinstance(raw, list):
        return [
            resolve_args(item, step_data, skipped_set, referencing_step_id)
            for item in raw
        ]
    if isinstance(raw, str):
        # Whole-string match for ${step_data.<path>} — T01 schema forbids
        # mixed interpolation, so a string either starts with ${ and ends
        # with } (ref) or is a literal.
        if raw.startswith("${") and raw.endswith("}") and "step_data." in raw:
            inner = raw[2:-1]
            return _resolve_ref(inner, step_data, skipped_set, referencing_step_id)
        return raw
    # int/float/bool/None: literal pass-through.
    return raw


def find_absent_ref_path(value: Any, path: str = "") -> str | None:
    """Return the dotted path to the first ``_REF_ABSENT`` leaf, or None.

    Used by the executor to produce an ``unresolved arg ref`` error message
    naming the offending path.
    """
    from megalos_server.tools import _REF_ABSENT  # local import: avoid cycle

    if value is _REF_ABSENT:
        return path or "<root>"
    if isinstance(value, dict):
        for k, v in value.items():
            sub = find_absent_ref_path(v, f"{path}.{k}" if path else k)
            if sub is not None:
                return sub
        return None
    if isinstance(value, list):
        for i, item in enumerate(value):
            sub = find_absent_ref_path(item, f"{path}[{i}]")
            if sub is not None:
                return sub
        return None
    return None


def _map_outcome_to_envelope(outcome: CallOutcome) -> dict:
    """Map a ``CallOutcome`` variant to the workflow-facing envelope dict."""
    if isinstance(outcome, Ok):
        return {"ok": True, "value": outcome.value}
    if isinstance(outcome, ToolExecutionError):
        return {"ok": False, "error": {"message": outcome.message}}
    if isinstance(outcome, TransportError):
        return {"ok": False, "error": {"message": f"transport error: {outcome.detail}"}}
    if isinstance(outcome, ProtocolError):
        return {"ok": False, "error": {"message": f"protocol error: {outcome.detail}"}}
    if isinstance(outcome, SchemaValidationError):
        return {"ok": False, "error": {"message": f"schema error: {outcome.detail}"}}
    if isinstance(outcome, McpTimeoutError):
        return {"ok": False, "error": {"message": "timeout"}}
    # Should be unreachable — the union is closed. Defensive fallback.
    return {"ok": False, "error": {"message": f"unknown outcome: {type(outcome).__name__}"}}


def _effective_timeout(step: dict, registry: Registry) -> float:
    """Resolve timeout precedence: step > registry default > module default."""
    step_timeout = step.get("timeout")
    if isinstance(step_timeout, (int, float)) and not isinstance(step_timeout, bool):
        return float(step_timeout)
    cfg = registry.get(step["server"])
    if cfg.timeout_default is not None:
        return float(cfg.timeout_default)
    return _DEFAULT_TIMEOUT_S


def execute_mcp_tool_call_step(
    step: dict,
    step_data: dict,
    skipped_set: set[str],
    registry: Registry | None,
    workflow_name: str,
) -> dict:
    """Execute one ``mcp_tool_call`` step and return its envelope.

    Resolves args, checks for unresolved refs (writes an error envelope
    instead of dispatching the call), picks an effective timeout, invokes
    ``mcp_client.call``, and maps the outcome.

    May raise ``_SkippedPredecessor`` (from ``resolve_args``) for cascade.
    The caller is responsible for catching that and treating the step as
    skipped.

    Raises ``RuntimeError`` if ``registry`` is None — that situation should
    have been caught at workflow load time by T01's schema cross-check;
    this is a belt-and-braces invariant check.
    """
    if registry is None:
        raise RuntimeError(
            "registry required for mcp_tool_call execution — should have been "
            "caught at load time"
        )

    resolved = resolve_args(step["args"], step_data, skipped_set, step["id"])

    absent_path = find_absent_ref_path(resolved)
    if absent_path is not None:
        envelope = {
            "ok": False,
            "error": {"message": f"unresolved arg ref: ${{step_data.{absent_path}}}"},
        }
        _log.debug(
            "mcp_tool_call step skipped (unresolved ref)",
            extra={
                "workflow": workflow_name,
                "step_id": step["id"],
                "outcome": "UnresolvedRef",
            },
        )
        return envelope

    effective_timeout = _effective_timeout(step, registry)
    outcome = mcp_client.call(
        server_name=step["server"],
        tool=step["tool"],
        args=resolved,
        registry=registry,
        timeout=effective_timeout,
    )
    _log.debug(
        "mcp_tool_call step executed",
        extra={
            "workflow": workflow_name,
            "step_id": step["id"],
            "outcome": type(outcome).__name__,
        },
    )
    return _map_outcome_to_envelope(outcome)
