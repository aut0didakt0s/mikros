"""MCP tool functions for megalos workflow engine."""

import functools
import json
import re

import jsonschema

from . import state
from .errors import (
    ARTIFACT_MAX,
    CONTENT_MAX,
    CROSS_SESSION_ACCESS_DENIED,
    FRAME_TYPE_NOT_POPPABLE,
    NO_FRAME_TO_POP,
    SESSION_STACK_FULL,
    SUB_WORKFLOW_PENDING,
    SessionNotFoundError,
    error_response,
)
from .identity_ctx import caller_identity_var
from .mcp_registry import Registry
from .state import COMPLETE as _COMPLETE
from .state import _compute_fingerprint as _fp

_DEFAULT_MAX_RETRIES = 3

# max_stack_depth = 3 leave one slot headroom under the 5 active-session cap for
# a new top-level session spawned from a separate client conversation. Deeper
# stack also risks client-side UX edge cases: interrupt-inside-interrupt-
# inside-interrupt is already rare; four-deep is pathological.
max_stack_depth = 3

_DO_NOT_RULES = [
    "Do NOT skip ahead to later steps.",
    "Do NOT produce final artifacts yet.",
    "Do NOT ask multiple questions at once.",
    "Do NOT proceed until all gates for this step are satisfied.",
    "Do NOT submit a step without showing your work to the user and waiting for their confirmation. Each step is a conversation, not a task you complete silently.",
    "Do NOT submit multiple steps in a single response. Complete ONE step, present it, wait for the user to respond, then move to the next.",
    "Do NOT reveal step names, step numbers, or internal workflow mechanics to the user. The workflow should feel like a natural conversation, not a numbered checklist. Never say things like 'Step 2: Decompose and Structure' or 'we are now in the plan phase'.",
]

_CONVERSATION_REPAIR_DEFAULTS = {
    "on_go_back": "Guide the user to use revise_step",
    "on_cancel": "Confirm cancellation, then use delete_session",
    "on_digression": "Acknowledge, then redirect to current step",
    "on_clarification": "Re-explain the current step's directive more simply",
}


class _SkippedPredecessor(Exception):
    def __init__(self, sid: str, referencing_step_id: str):
        self.sid = sid
        self.referencing_step_id = referencing_step_id
        super().__init__(
            f"Step '{referencing_step_id}' precondition references skipped predecessor '{sid}'"
        )


_REF_ABSENT = object()


def _resolve_ref(ref: str, step_data: dict, skipped_set: set[str], referencing_step_id: str):
    """Resolve a `step_data.<sid>[.<field>...]` ref. Raise _SkippedPredecessor on cascade.
    Return _REF_ABSENT if the predecessor/subpath is missing."""
    parts = ref.split(".")
    # parts[0] == "step_data" (guaranteed by schema parse-time validation)
    sid = parts[1]
    if sid in skipped_set:
        raise _SkippedPredecessor(sid, referencing_step_id)
    if sid not in step_data:
        return _REF_ABSENT
    if len(parts) == 2:
        return step_data[sid]
    try:
        value = json.loads(step_data[sid])
    except (json.JSONDecodeError, TypeError):
        return _REF_ABSENT
    for seg in parts[2:]:
        if not isinstance(value, dict) or seg not in value:
            return _REF_ABSENT
        value = value[seg]
    return value


def _evaluate_precondition(
    precondition: dict, step_data: dict, skipped_set: set[str], referencing_step_id: str
) -> bool:
    """Return True → run; False → skip. Raise _SkippedPredecessor on ref to skipped predecessor."""
    if "when_equals" in precondition:
        we = precondition["when_equals"]
        resolved = _resolve_ref(we["ref"], step_data, skipped_set, referencing_step_id)
        if resolved is _REF_ABSENT:
            return False
        return resolved == we["value"]
    # when_present
    ref = precondition["when_present"]
    sid = ref.split(".")[1]
    if sid in skipped_set:
        raise _SkippedPredecessor(sid, referencing_step_id)
    return sid in step_data


def _compute_skipped_steps(wf: dict, step_data: dict) -> list[str]:
    """Pure. Walk step list in order, computing skipped set from live step_data.
    Swallow cascade errors — observability must not crash; treat as skipped."""
    skipped: list[str] = []
    skipped_set: set[str] = set()
    for step in wf["steps"]:
        sid = step["id"]
        if sid in step_data:
            continue
        pc = step.get("precondition")
        if pc is None:
            continue
        try:
            runs = _evaluate_precondition(pc, step_data, skipped_set, referencing_step_id=sid)
        except _SkippedPredecessor:
            skipped.append(sid)
            skipped_set.add(sid)
            continue
        if not runs:
            skipped.append(sid)
            skipped_set.add(sid)
    return skipped


def _apply_skip_loop(
    next_step_id: str, wf: dict, step_data: dict, force_branched: bool
) -> tuple[str, list[str]]:
    """Phase 1: if force_branched, commit without precondition eval.
    Phase 2: cascade — skip forward linearly while preconditions evaluate false."""
    skipped: list[str] = []
    if force_branched:
        return next_step_id, skipped
    skipped_set: set[str] = set(_compute_skipped_steps(wf, step_data))
    steps = wf["steps"]
    while next_step_id != _COMPLETE:
        idx, step = _find_step(wf, next_step_id)
        pc = step.get("precondition") if step else None
        if pc is None:
            return next_step_id, skipped
        if _evaluate_precondition(pc, step_data, skipped_set, referencing_step_id=next_step_id):
            return next_step_id, skipped
        skipped.append(next_step_id)
        skipped_set.add(next_step_id)
        next_step_id = _COMPLETE if idx == len(steps) - 1 else steps[idx + 1]["id"]
    return next_step_id, skipped


def _auto_execute_mcp_steps(
    next_step_id: str,
    wf: dict,
    session_id: str,
    step_data: dict,
    registry: Registry | None,
) -> tuple[str, dict | None]:
    """Execute any ``mcp_tool_call`` step(s) at/after ``next_step_id`` in-line.

    Loops while the step pointed at by ``next_step_id`` has ``action:
    mcp_tool_call``: resolves args, calls the MCP client, writes the
    envelope to ``step_data``, bumps that step's visit count, and advances
    to whichever step comes next. Exits as soon as the pointer lands on a
    non-mcp step (which the client must then submit normally) or
    ``_COMPLETE``.

    Cascade handling: if a ref-path in the args tree points at a skipped
    predecessor, ``_SkippedPredecessor`` bubbles out of
    ``execute_mcp_tool_call_step``. We map this to an
    ``skipped_predecessor_reference`` error envelope returned as the second
    tuple element (mirroring the handling in submit_step); the caller is
    responsible for any parent-escalation wrapping. On this path,
    ``step_data`` is left unchanged for the offending step and the loop
    stops.

    Returns ``(final_step_id, None)`` on success, or
    ``(next_step_id_at_failure, error_envelope_dict)`` on cascade.
    """
    # Local import to avoid cycle at module load time.
    from .mcp_executor import execute_mcp_tool_call_step

    while next_step_id != _COMPLETE:
        idx, step = _find_step(wf, next_step_id)
        if step is None or step.get("action") != "mcp_tool_call":
            return next_step_id, None

        # Snapshot skipped_set BEFORE executing this step so resolve_args
        # sees the same cascade semantics as a precondition evaluated at
        # the same point.
        skipped_set = set(_compute_skipped_steps(wf, step_data))

        try:
            envelope = execute_mcp_tool_call_step(
                step,
                step_data,
                skipped_set,
                registry,
                workflow_name=wf.get("name", "<unknown>"),
            )
        except _SkippedPredecessor as e:
            err = error_response(
                "skipped_predecessor_reference",
                f"Step '{e.referencing_step_id}' precondition references skipped predecessor '{e.sid}'",
                session_fingerprint=_fp(session_id),
                step_id=next_step_id,
                referenced_step=e.sid,
                referencing_field="args",
            )
            return next_step_id, err

        step_data[step["id"]] = json.dumps(envelope)
        state.increment_visit(session_id, step["id"])

        # Advance: branches-default honoured (v1 doesn't pick branches from the
        # envelope; workflow authors must use a follow-up LLM step if they want
        # conditional routing off the envelope). Linear otherwise.
        if step.get("branches"):
            next_step_id = step.get("default_branch", _COMPLETE)
        else:
            is_last = idx == len(wf["steps"]) - 1
            next_step_id = _COMPLETE if is_last else wf["steps"][idx + 1]["id"]

        # Re-apply skip loop so precondition-based skips on downstream steps
        # see the envelope we just wrote. Propagated _SkippedPredecessor maps
        # to the same cascade envelope as the args-path failure above.
        try:
            next_step_id, _ = _apply_skip_loop(
                next_step_id, wf, step_data, force_branched=False
            )
        except _SkippedPredecessor as e:
            err = error_response(
                "skipped_predecessor_reference",
                f"Step '{e.referencing_step_id}' precondition references skipped predecessor '{e.sid}'",
                session_fingerprint=_fp(session_id),
                step_id=next_step_id,
                referenced_step=e.sid,
                referencing_field="precondition",
            )
            return next_step_id, err

    return next_step_id, None


def _check_str(value: object, name: str, *, required: bool = False) -> dict | None:
    """Return invalid_argument error_response if value isn't str (or empty when required)."""
    if not isinstance(value, str):
        return error_response(
            "invalid_argument",
            f"{name} must be a string, got {type(value).__name__}",
            field=name,
        )
    if required and not value:
        return error_response("invalid_argument", f"{name} must not be empty", field=name)
    return None


def _trap_errors(field: str = "unknown"):
    """Decorator: convert SessionNotFoundError → session_not_found, TypeError/ValueError → invalid_argument.

    Message text is constructed from the exception type, never from str(e), to
    avoid leaking the raw session_id capability token into the error envelope.
    The fingerprint goes into the structured session_fingerprint key instead.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except SessionNotFoundError:
                sid = kwargs.get("session_id") or (args[0] if args and isinstance(args[0], str) else None)
                return error_response(
                    "session_not_found",
                    "session not found",
                    session_fingerprint=_fp(sid) if sid else None,
                )
            except (TypeError, ValueError) as e:
                return error_response("invalid_argument", str(e), field=field)
        return wrapper
    return decorator


def _repair_for(workflow: dict) -> dict:
    """Merge per-workflow conversation_repair overrides on top of defaults."""
    overrides = workflow.get("conversation_repair") or {}
    return {**_CONVERSATION_REPAIR_DEFAULTS, **overrides}


def _find_step(workflow, step_id):
    """Find (index, step_dict) in a workflow by id. Returns (-1, None) if missing."""
    for i, step in enumerate(workflow["steps"]):
        if step["id"] == step_id:
            return i, step
    return -1, None


def _resolve_session(session_id, workflows):
    """Look up session and its workflow. Returns (session, wf) or (None, error_dict).

    Single planting site for the caller/owner identity access-check: the
    per-request caller_identity (populated by CallerIdentityMiddleware) is
    compared against session["owner_identity"] before the session is handed
    back to any tool. Every session-scoped tool in the surface routes through
    this helper, so the check is present for all of them without threading
    a ctx parameter through every tool signature.

    Today both sides carry ANONYMOUS_IDENTITY so the check is structurally a
    no-op; the emission path is kept covered by test_identity_seam.py so the
    Phase G bearer-auth path drops in without re-architecture."""
    try:
        session = state.get_session(session_id)
    except SessionNotFoundError:
        return None, error_response(
            "session_not_found", "session not found", session_fingerprint=_fp(session_id)
        )
    caller_identity = caller_identity_var.get()
    if caller_identity != session["owner_identity"]:
        return None, error_response(
            CROSS_SESSION_ACCESS_DENIED,
            f"caller identity does not own session {session['fingerprint']}",
            session_fingerprint=session["fingerprint"],
        )
    wf = workflows.get(session["workflow_type"])
    if not wf:
        return None, error_response(
            "workflow_not_loaded",
            f"Workflow '{session['workflow_type']}' not loaded",
            session_fingerprint=session["fingerprint"],
        )
    return (session, wf), None


def _format_validation_error(err) -> str:
    """Format a jsonschema ValidationError as '<field_path>: <message>' or plain message at root."""
    path = err.json_path  # e.g. "$", "$.title", "$.tags[0]"
    if path == "$":
        return err.message
    field = path[2:] if path.startswith("$.") else path
    return f"{field}: {err.message}"


def _validate_output(content: str, step: dict) -> list[str] | None:
    """Validate content against step's output_schema. Returns error list or None if valid/no schema."""
    output_schema = step.get("output_schema")
    if not output_schema:
        return None
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError) as e:
        return [f"Content is not valid JSON: {e}"]
    validator = jsonschema.Draft202012Validator(output_schema)
    errors = [_format_validation_error(err) for err in validator.iter_errors(parsed)]
    return errors if errors else None


_SUMMARY_LIMIT = 500


def _assemble_context(inject_context: list[dict], step_data: dict) -> list[dict]:
    """Build injected context from inject_context spec and stored step_data."""
    result = []
    for entry in inject_context:
        source_id = entry["from"]
        raw = step_data.get(source_id)
        if raw is None:
            result.append({"from": source_id, "content": None})
            continue

        # Try to parse as JSON for field filtering
        parsed = None
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass

        fields = entry.get("fields")
        if fields and isinstance(parsed, dict):
            content = {k: parsed.get(k) for k in fields}
            item = {"from": source_id, "fields": content}
        else:
            content = raw
            if entry.get("summary") and len(content) > _SUMMARY_LIMIT:
                content = content[:_SUMMARY_LIMIT] + "[truncated]"
            item = {"from": source_id, "content": content}

        result.append(item)
    return result


def _evaluate_guardrails(guardrails, content, session, step_id):
    """Return first matching guardrail dict or None. Declaration order = priority."""
    for gr in guardrails:
        trigger = gr["trigger"]
        t_type = trigger["type"]
        if t_type == "keyword_match":
            for pattern in trigger.get("patterns", []):
                if re.search(pattern, content, re.IGNORECASE):
                    return gr
        elif t_type == "step_count":
            if len(session["step_data"]) >= trigger.get("max", 0):
                return gr
        elif t_type == "step_revisit":
            visits = session["step_visit_counts"].get(step_id, 0)
            if visits >= trigger.get("max_visits", 0):
                return gr
        elif t_type == "output_length":
            if len(content) > trigger.get("max_chars", 0):
                return gr
    return None


def _advance_parent(
    parent_sid: str,
    parent_wf: dict,
    call_step: dict,
    idx: int,
    step_data: dict,
    registry: Registry | None = None,
) -> dict:
    """Advance parent session after child-artifact propagation.

    Returns the parent's next-step directive dict. Next-step resolution:
    call+branches → default_branch (guaranteed present at parse time by
    schema.py's call_branches_without_default rule; see S01 amendment
    dbae376). Linear → next step in list, _COMPLETE if last.
    """
    steps = parent_wf["steps"]

    if call_step.get("branches"):
        next_step_id = call_step["default_branch"]
    else:
        is_last = idx == len(steps) - 1
        next_step_id = _COMPLETE if is_last else steps[idx + 1]["id"]

    try:
        next_step_id, _ = _apply_skip_loop(next_step_id, parent_wf, step_data, force_branched=False)
    except _SkippedPredecessor as e:
        return error_response(
            "skipped_predecessor_reference",
            f"Step '{e.referencing_step_id}' precondition references skipped predecessor '{e.sid}'",
            session_fingerprint=_fp(parent_sid),
            step_id=call_step["id"],
            referenced_step=e.sid,
            referencing_field="precondition",
        )

    # Auto-execute mcp_tool_call steps at the parent's new pointer.
    next_step_id, cascade_err = _auto_execute_mcp_steps(
        next_step_id, parent_wf, parent_sid, step_data, registry
    )
    if cascade_err is not None:
        state.update_session(parent_sid, current_step=next_step_id, step_data=step_data)
        return cascade_err

    if next_step_id != _COMPLETE:
        state.increment_visit(parent_sid, next_step_id)
    state.update_session(parent_sid, current_step=next_step_id, step_data=step_data)

    result: dict = {
        "session_id": parent_sid,
        "submitted": {"id": call_step["id"], "title": call_step["title"]},
        "progress": f"step {idx + 1} of {len(steps)} complete",
        "propagated_from_sub_workflow": True,
    }
    if next_step_id == _COMPLETE:
        result["status"] = "workflow_complete"
        result["message"] = "All steps complete. Call generate_artifact to produce final output."
    else:
        _, nxt = _find_step(parent_wf, next_step_id)
        result["next_step"] = {"id": nxt["id"], "title": nxt["title"]}
        if nxt.get("call"):
            result["next_step"]["call_target"] = nxt["call"]
        result["directive"] = nxt["directive_template"]
        result["do_not"] = _DO_NOT_RULES
        result["conversation_repair"] = _repair_for(parent_wf)
        result["gates"] = nxt["gates"]
        if nxt.get("inject_context"):
            result["injected_context"] = _assemble_context(nxt["inject_context"], step_data)
        if nxt.get("directives"):
            result["directives"] = nxt["directives"]
        if nxt.get("branches"):
            result["branches"] = nxt["branches"]
            if nxt.get("default_branch"):
                result["default_branch"] = nxt["default_branch"]
    return result


def _wrap_child_failure_into_parent_escalation(
    child_session: dict,
    child_wf: dict,
    reason: str,
    child_error: dict,
) -> dict:
    """If child has a parent, wrap its failure into a parent escalation.

    Returns parent-escalation response with called_workflow_error wrapper.
    If child has no parent (top-level session), returns child_error unchanged.
    Uniform escalation label across all failure paths: "called_workflow_failed".
    Specifics live inside the wrapper's child_error dict (code / reason).
    """
    parent_sid = state.parent_of(child_session["session_id"])
    if not parent_sid:
        return child_error

    child_sid = child_session["session_id"]
    state.set_escalation(
        parent_sid,
        "called_workflow_failed",
        f"child '{child_sid}' failed: {reason}",
    )

    wrapper = {
        "child_session_fingerprint": _fp(child_sid),
        "child_workflow_type": child_wf["name"],
        "child_error": child_error,
    }
    return error_response(
        "session_escalated",
        f"parent escalated due to sub-workflow failure in child with fingerprint '{_fp(child_sid)}'",
        parent_session_fingerprint=_fp(parent_sid),
        called_workflow_error=wrapper,
    )


def _resume_parent_after_digression(
    child_session: dict, workflows: dict, registry: Registry | None = None
) -> dict:
    """Digression-frame completion: pop frame, delete child, hand next-step directive
    back to the outer session. No artifact propagation (digression-frames have no
    data contract — the push_flow call is the entire handoff). Outer session
    resumes at whatever current_step it was parked on when the push happened.
    """
    child_sid = child_session["session_id"]
    outer_sid = state.parent_of(child_sid)
    assert outer_sid is not None, "resume_parent_after_digression called on a root session"

    outer_resolved, err = _resolve_session(outer_sid, workflows)
    if err:
        return err
    outer_session, outer_wf = outer_resolved

    state.delete_session(child_sid)

    outer_current = outer_session["current_step"]
    idx, current_step = _find_step(outer_wf, outer_current)
    steps = outer_wf["steps"]

    result: dict = {
        "session_id": outer_sid,
        "resumed_from_digression": True,
        "child_session_id": child_sid,
    }

    if outer_current == _COMPLETE or current_step is None:
        result["status"] = "workflow_complete"
        result["message"] = "Outer workflow already complete; digression returned."
        return result

    result["current_step"] = {"id": current_step["id"], "title": current_step["title"]}
    result["progress"] = f"step {idx + 1} of {len(steps)} resumed"
    result["directive"] = current_step["directive_template"]
    result["do_not"] = _DO_NOT_RULES
    result["conversation_repair"] = _repair_for(outer_wf)
    result["gates"] = current_step["gates"]
    if current_step.get("inject_context"):
        result["injected_context"] = _assemble_context(
            current_step["inject_context"], outer_session["step_data"]
        )
    if current_step.get("directives"):
        result["directives"] = current_step["directives"]
    if current_step.get("call"):
        result["current_step"]["call_target"] = current_step["call"]
    if current_step.get("branches"):
        result["branches"] = current_step["branches"]
        if current_step.get("default_branch"):
            result["default_branch"] = current_step["default_branch"]
    return result


def _auto_resume_on_top_frame_complete(
    child_session: dict,
    child_wf: dict,
    workflows: dict,
    registry: Registry | None = None,
) -> dict:
    """Frame-type dispatch on child completion. Reads the child's own frame row to
    decide: 'call' → propagate artifact to parent via M004 semantics; 'digression'
    → pop and resume outer with no data handoff. No fallback reads — own_frame
    is the stack-authoritative lookup.
    """
    own = state.own_frame(child_session["session_id"])
    frame_type = own["frame_type"] if own else "call"  # defensive default matches M004
    if frame_type == "digression":
        return _resume_parent_after_digression(child_session, workflows, registry=registry)
    return _propagate_to_parent(child_session, child_wf, workflows, registry=registry)


def _propagate_to_parent(
    child_session: dict,
    child_wf: dict,
    workflows: dict,
    registry: Registry | None = None,
) -> dict:
    """Bridge: child completed → propagate final artifact to parent, advance parent.

    On parent output_schema failure, escalates the parent and retains the child
    (T03 will enrich the escalation payload). On state drift (parent not at a
    call-step anymore), escalates defensively and retains the child. Otherwise
    writes child artifact to parent.step_data[call_step_id], deletes child,
    clears parent.called_session, and returns parent's next-step directive.
    """
    child_sid = child_session["session_id"]
    parent_sid = state.parent_of(child_sid)
    assert parent_sid is not None, "propagate_to_parent called on a root session"
    last_step_id = child_wf["steps"][-1]["id"]
    artifact = child_session["step_data"].get(last_step_id, "")

    parent_resolved, err = _resolve_session(parent_sid, workflows)
    if err:
        # Parent vanished mid-flight — defense in depth. Return the error;
        # child stays at _COMPLETE retained.
        return err
    parent_session, parent_wf = parent_resolved

    call_step_id = parent_session["current_step"]
    idx, call_step = _find_step(parent_wf, call_step_id)
    if call_step is None or "call" not in call_step:
        # Parent drifted off the call-step (e.g. revised). Escalate + retain child.
        state.set_escalation(
            parent_sid,
            "sub_workflow_state_drift",
            f"parent at '{call_step_id}' is not a call-step",
        )
        return error_response(
            "session_escalated",
            "parent state drift during sub-workflow propagation",
            parent_session_fingerprint=_fp(parent_sid),
            child_session_fingerprint=_fp(child_sid),
        )

    if call_step.get("output_schema"):
        validation_errors = _validate_output(artifact, call_step)
        if validation_errors is not None:
            child_error = {
                "status": "validation_error",
                "errors": validation_errors,
                "session_fingerprint": _fp(child_sid),
                "step_id": child_wf["steps"][-1]["id"],
                "reason": "parent_output_schema_fail",
                "message": "child artifact failed parent call-step output_schema",
            }
            return _wrap_child_failure_into_parent_escalation(
                child_session, child_wf, reason="parent_output_schema_fail", child_error=child_error,
            )

    parent_step_data = parent_session["step_data"]
    parent_step_data[call_step_id] = artifact

    state.delete_session(child_sid)
    state.set_called_session(parent_sid, None)

    return _advance_parent(
        parent_sid, parent_wf, call_step, idx, parent_step_data, registry=registry
    )


def register_tools(mcp, workflows, registry: Registry | None = None):
    """Register workflow tools on the FastMCP app.

    ``registry`` is the loaded ``Registry`` of external MCP servers used by
    ``mcp_tool_call`` steps. May be ``None`` in deployments with no MCP
    integration; in that case any workflow carrying ``mcp_tool_call`` steps
    would have been rejected at load time by the schema validator's
    registry-required rule (T01), so the executor never reaches the fork
    with a None registry in normal operation.
    """

    @mcp.tool()
    @_trap_errors("category")
    def list_workflows(category: str = "") -> dict:
        """List available workflow types, optionally filtered by category.

        Categories: writing_communication, analysis_decision, planning_strategy,
        learning_development, professional, creative.
        Pass empty string or omit to list all.
        """
        err = _check_str(category, "category")
        if err:
            return err
        items = []
        for name, wf in workflows.items():
            wf_cat = wf.get("category", "")
            if category and wf_cat != category:
                continue
            items.append({
                "name": name,
                "description": wf.get("description", ""),
                "category": wf_cat,
                "steps": len(wf["steps"]),
            })
        return {"workflows": items, "total": len(items)}

    @mcp.tool()
    @_trap_errors("workflow_type")
    def start_workflow(workflow_type: str, context: str) -> dict:
        """Start a new workflow session. Returns the first step's directive."""
        err = _check_str(workflow_type, "workflow_type", required=True) or _check_str(context, "context")
        if err:
            return err
        if workflow_type not in workflows:
            return error_response(
                "workflow_not_loaded",
                f"Unknown workflow type: {workflow_type}",
                available_types=list(workflows.keys()),
            )

        active = state.count_active()
        if active >= 5:
            active_sessions = [s for s in state.list_sessions() if s["status"] == "active"]
            # depth_breakdown: per-root {root_session_id, depth}, sorted deepest first.
            # Depth semantics: depth = frames above root = stack_depth(root_session_id)
            # return value. Lone session → 0; one push → 1. Same field and same shape
            # as on session_stack_full below — operators get a single diagnostic view
            # across both cap-hit paths.
            return error_response(
                "session_cap_exceeded",
                f"Session cap reached ({active}/5). Delete or complete a session first.",
                active_sessions=active_sessions,
                depth_breakdown=state.depth_breakdown(),
            )

        wf = workflows[workflow_type]
        first_step = wf["steps"][0]
        sid = state.create_session(workflow_type, current_step=first_step["id"])
        state.increment_visit(sid, first_step["id"])

        # Auto-execute any mcp_tool_call steps at the front of the workflow
        # before returning the first directive-bearing step to the client.
        # Start with a clean step_data + the first step's id.
        step_data: dict = {}
        next_step_id, cascade_err = _auto_execute_mcp_steps(
            first_step["id"], wf, sid, step_data, registry
        )
        if cascade_err is not None:
            state.update_session(sid, current_step=next_step_id, step_data=step_data)
            return cascade_err
        if next_step_id != first_step["id"]:
            # mcp steps executed — persist step_data and the advance.
            state.update_session(sid, current_step=next_step_id, step_data=step_data)
            if next_step_id != _COMPLETE:
                state.increment_visit(sid, next_step_id)

        if next_step_id == _COMPLETE:
            return {
                "session_id": sid,
                "workflow_type": workflow_type,
                "status": "workflow_complete",
                "message": "All steps complete. Call generate_artifact to produce final output.",
                "context": context,
            }

        _, effective_first = _find_step(wf, next_step_id)
        assert effective_first is not None
        result = {
            "session_id": sid,
            "workflow_type": workflow_type,
            "current_step": {"id": effective_first["id"], "title": effective_first["title"]},
            "directive": effective_first["directive_template"],
            "do_not": _DO_NOT_RULES,
            "conversation_repair": _repair_for(wf),
            "gates": effective_first["gates"],
            "context": context,
        }
        if effective_first.get("directives"):
            result["directives"] = effective_first["directives"]
        if effective_first.get("inject_context"):
            result["injected_context"] = _assemble_context(
                effective_first["inject_context"], step_data
            )
        return result

    @mcp.tool()
    @_trap_errors("session_id")
    def get_state(session_id: str) -> dict:
        """Get current session state: step, progress, and what's pending."""
        err = _check_str(session_id, "session_id", required=True)
        if err:
            return err
        resolved, err = _resolve_session(session_id, workflows)
        if err:
            return err
        session, wf = resolved

        steps = wf["steps"]
        idx, current = _find_step(wf, session["current_step"])
        pending = [s["title"] for s in steps[idx + 1:]] if idx >= 0 else []

        result = {
            "session_id": session_id,
            "workflow_type": session["workflow_type"],
            "current_step": {"id": current["id"], "title": current["title"]} if current else None,
            "progress": f"step {idx + 1} of {len(steps)}" if idx >= 0 else "unknown",
            "pending_steps": pending,
            "step_data": session["step_data"],
        }
        if current and current.get("inject_context"):
            result["injected_context"] = _assemble_context(current["inject_context"], session["step_data"])
        if current and current.get("directives"):
            result["directives"] = current["directives"]
        if current and current.get("intermediate_artifacts"):
            artifacts = state.get_artifacts(session_id, current["id"])
            if artifacts:
                result["artifact_checkpoints"] = artifacts
        skipped = _compute_skipped_steps(wf, session["step_data"])
        if skipped:
            result["skipped_steps"] = skipped
        top = state.top_frame_for(session_id)
        if top is not None:
            result["called_session"] = top["session_id"]
        # Full-chain stack view: same array for every session in the chain so
        # callers locate themselves by session_id match (no divergent 'frames
        # above me' truncation). Bare session (no stack involvement) -> [].
        own = state.own_frame(session_id)
        if own is not None:
            result["stack"] = state.full_stack(own["root_session_id"])
        elif state.stack_depth(session_id) > 0:
            # session_id is the root of a non-empty chain (no own frame, but
            # frames sit above it).
            result["stack"] = state.full_stack(session_id)
        else:
            result["stack"] = []
        return result

    @mcp.tool()
    @_trap_errors("session_id")
    def get_guidelines(session_id: str) -> dict:
        """Get anti-patterns and gates for the current step."""
        err = _check_str(session_id, "session_id", required=True)
        if err:
            return err
        resolved, err = _resolve_session(session_id, workflows)
        if err:
            return err
        session, wf = resolved

        _, step = _find_step(wf, session["current_step"])
        if not step:
            return error_response(
                "schema_violation",
                f"Step '{session['current_step']}' not found in workflow",
                session_fingerprint=session["fingerprint"],
            )

        return {
            "session_id": session_id,
            "current_step": {"id": step["id"], "title": step["title"]},
            "anti_patterns": step["anti_patterns"],
            "gates": step["gates"],
        }

    @mcp.tool()
    @_trap_errors("content")
    def submit_step(session_id: str, step_id: str, content: str, branch: str = "", artifact_id: str = "") -> dict:
        """Submit content for the current step. Enforces ordering — no skips, no backwards.

        branch: when the current step has branches, selects which step comes next.
        If empty and step has branches, default_branch is used as fallback."""
        err = (
            _check_str(session_id, "session_id", required=True)
            or _check_str(step_id, "step_id", required=True)
            or _check_str(content, "content")
            or _check_str(branch, "branch")
            or _check_str(artifact_id, "artifact_id")
        )
        if err:
            return err
        resolved, err = _resolve_session(session_id, workflows)
        if err:
            return err
        session, wf = resolved

        # Reject if session is escalated
        if session.get("escalation"):
            return error_response(
                "session_escalated",
                "Session is escalated. Resolve the escalation before continuing.",
                session_fingerprint=session["fingerprint"],
                escalation=session["escalation"],
            )

        current = session["current_step"]
        if current == _COMPLETE:
            return error_response(
                "workflow_complete",
                "Workflow already complete. Call generate_artifact to get final output.",
                session_fingerprint=session["fingerprint"],
            )

        if step_id != current:
            return error_response(
                "out_of_order_submission",
                f"Out-of-order submission: expected '{current}', got '{step_id}'",
                session_fingerprint=session["fingerprint"],
                expected_step=current,
                submitted_step=step_id,
            )

        content_bytes = len(content.encode("utf-8"))
        if content_bytes > CONTENT_MAX:
            return error_response(
                "oversize_payload",
                f"content exceeds {CONTENT_MAX} bytes ({content_bytes} bytes received)",
                field="content",
                max_bytes=CONTENT_MAX,
                actual_bytes=content_bytes,
                session_fingerprint=session["fingerprint"],
                step_id=step_id,
            )

        steps = wf["steps"]
        idx, step = _find_step(wf, step_id)

        # Generalized pending guard: refuse submit_step on any step if this session
        # has a frame above it in the stack (call-frame from M004's enter_sub_workflow
        # or digression-frame from M005's push_flow). The caller is submitting against
        # a non-top session and should resolve the frame above first.
        top = state.top_frame_for(session_id)
        if top is not None:
            called_sid = top["session_id"]
            called_fp = _fp(called_sid)
            return error_response(
                "sub_workflow_pending",
                f"a child session (fingerprint '{called_fp}') is already in flight above session "
                f"fingerprint '{session['fingerprint']}'. "
                f"Complete or revise the child before submitting further steps.",
                parent_session_fingerprint=session["fingerprint"],
                child_session_fingerprint=called_fp,
                call_step_id=step_id,
                frame_type=top["frame_type"],
            )

        # Call-step routing guard: submit_step is the wrong tool for call-steps.
        if "call" in step:
            return error_response(
                "call_step_requires_enter_sub_workflow",
                f"step '{step_id}' has `call: {step['call']}`. Use the `enter_sub_workflow` tool, "
                f"not `submit_step`, to invoke the child workflow.",
                session_fingerprint=session["fingerprint"],
                step_id=step_id,
                hint="enter_sub_workflow",
                call_target=step["call"],
            )

        # Handle intermediate artifacts
        ia_list = step.get("intermediate_artifacts")
        if ia_list:
            if not artifact_id:
                expected = [a["id"] for a in ia_list]
                return error_response(
                    "invalid_argument",
                    "Step has intermediate_artifacts. Must specify artifact_id.",
                    field="artifact_id",
                    session_fingerprint=session["fingerprint"],
                    step_id=step_id,
                    expected_artifacts=expected,
                )
            # Find matching artifact definition
            art_def = None
            for a in ia_list:
                if a["id"] == artifact_id:
                    art_def = a
                    break
            if not art_def:
                return error_response(
                    "unknown_artifact",
                    f"Unknown artifact_id '{artifact_id}'",
                    session_fingerprint=session["fingerprint"],
                    step_id=step_id,
                    expected_artifacts=[a["id"] for a in ia_list],
                )
            # Validate against artifact's schema
            try:
                parsed = json.loads(content)
            except (json.JSONDecodeError, TypeError) as e:
                return {
                    "status": "validation_error",
                    "errors": [f"Content is not valid JSON: {e}"],
                    "session_fingerprint": session["fingerprint"],
                    "step_id": step_id,
                    "artifact_id": artifact_id,
                }
            validator = jsonschema.Draft202012Validator(art_def["schema"])
            art_errors = [err.message for err in validator.iter_errors(parsed)]
            if art_errors:
                return {
                    "status": "validation_error",
                    "errors": art_errors,
                    "session_fingerprint": session["fingerprint"],
                    "step_id": step_id,
                    "artifact_id": artifact_id,
                }
            # Store checkpoint if flagged
            if art_def.get("checkpoint"):
                state.store_artifact(session_id, step_id, artifact_id, content)
            # If not output_from, accept but don't advance
            output_from = step.get("output_from", "")
            if artifact_id != output_from:
                return {
                    "status": "artifact_accepted",
                    "session_id": session_id,
                    "step_id": step_id,
                    "artifact_id": artifact_id,
                    "current_step": step_id,
                }
            # artifact_id == output_from: fall through to normal flow
            # (skip output_schema validation — artifact schema already validated)
            step_data = session["step_data"]
            step_data[step_id] = content

            # Evaluate guardrails before state transition
            guardrails = wf.get("guardrails", [])
            fired_guardrail = _evaluate_guardrails(guardrails, content, session, step_id) if guardrails else None
            guardrail_warning = None

            if fired_guardrail:
                action = fired_guardrail["action"]
                if action == "escalate":
                    state.set_escalation(session_id, fired_guardrail["id"], fired_guardrail["message"])
                    state.update_session(session_id, step_data=step_data)
                    child_error = error_response(
                        "session_escalated",
                        "Session escalated by guardrail.",
                        session_fingerprint=session["fingerprint"],
                        guardrail_id=fired_guardrail["id"],
                        message=fired_guardrail["message"],
                    )
                    if state.parent_of(session_id) is not None:
                        return _wrap_child_failure_into_parent_escalation(
                            session, wf, reason="guardrail_escalate", child_error=child_error,
                        )
                    return child_error
                elif action == "warn":
                    guardrail_warning = fired_guardrail["message"]

            # Determine next step (same logic as non-artifact path)
            force_branched = False
            if fired_guardrail is not None and fired_guardrail["action"] == "force_branch":
                force_branched = True
                next_step_id = fired_guardrail["target_step"]
            elif step.get("branches"):
                valid_targets = [b["next"] for b in step["branches"]]
                target = branch if branch else step.get("default_branch", "")
                if target not in valid_targets:
                    return error_response(
                        "invalid_argument",
                        f"Invalid branch '{target}'. Valid options: {valid_targets}",
                        field="branch",
                        session_fingerprint=session["fingerprint"],
                        step_id=step_id,
                    )
                next_step_id = target
            else:
                is_last = idx == len(steps) - 1
                next_step_id = _COMPLETE if is_last else steps[idx + 1]["id"]

            try:
                next_step_id, _ = _apply_skip_loop(next_step_id, wf, step_data, force_branched)
            except _SkippedPredecessor as e:
                child_error = error_response(
                    "skipped_predecessor_reference",
                    f"Step '{e.referencing_step_id}' precondition references skipped predecessor '{e.sid}'",
                    session_fingerprint=session["fingerprint"],
                    step_id=step_id,
                    referenced_step=e.sid,
                    referencing_field="precondition",
                )
                if state.parent_of(session_id) is not None:
                    return _wrap_child_failure_into_parent_escalation(
                        session, wf, reason="cascade_error", child_error=child_error,
                    )
                return child_error

            # Auto-execute mcp_tool_call steps at the new pointer.
            next_step_id, cascade_err = _auto_execute_mcp_steps(
                next_step_id, wf, session_id, step_data, registry
            )
            if cascade_err is not None:
                state.update_session(session_id, current_step=next_step_id, step_data=step_data)
                if state.parent_of(session_id) is not None:
                    return _wrap_child_failure_into_parent_escalation(
                        session, wf, reason="cascade_error", child_error=cascade_err,
                    )
                return cascade_err

            if next_step_id != _COMPLETE:
                state.increment_visit(session_id, next_step_id)
            state.update_session(session_id, current_step=next_step_id, step_data=step_data)

            # Top-frame completion → frame-type dispatch: call-frames propagate
            # the child's artifact to the parent (M004); digression-frames just
            # pop and resume the outer session (M005).
            if next_step_id == _COMPLETE and state.parent_of(session_id) is not None:
                return _auto_resume_on_top_frame_complete(
                    {**session, "current_step": next_step_id, "step_data": step_data},
                    wf,
                    workflows,
                    registry=registry,
                )

            result: dict = {
                "session_id": session_id,
                "submitted": {"id": step["id"], "title": step["title"]},
                "artifact_id": artifact_id,
                "progress": f"step {idx + 1} of {len(steps)} complete",
            }
            if guardrail_warning:
                result["guardrail_warning"] = guardrail_warning
            if next_step_id == _COMPLETE:
                result["status"] = "workflow_complete"
                result["message"] = "All steps complete. Call generate_artifact to produce final output."
            else:
                _, nxt = _find_step(wf, next_step_id)
                result["next_step"] = {"id": nxt["id"], "title": nxt["title"]}
                if nxt.get("call"):
                    result["next_step"]["call_target"] = nxt["call"]
                result["directive"] = nxt["directive_template"]
                result["do_not"] = _DO_NOT_RULES
                result["conversation_repair"] = _repair_for(wf)
                result["gates"] = nxt["gates"]
                if nxt.get("inject_context"):
                    result["injected_context"] = _assemble_context(nxt["inject_context"], step_data)
                if nxt.get("directives"):
                    result["directives"] = nxt["directives"]
                if nxt.get("branches"):
                    result["branches"] = nxt["branches"]
                    if nxt.get("default_branch"):
                        result["default_branch"] = nxt["default_branch"]
            return result

        # Validate against output_schema if present
        validation_errors = _validate_output(content, step)
        if validation_errors is not None:
            max_retries = step.get("max_retries", _DEFAULT_MAX_RETRIES)
            retry_count = state.increment_retry(session_id, step_id)
            if retry_count >= max_retries:
                child_error = {
                    "status": "validation_error",
                    "errors": validation_errors,
                    "session_fingerprint": session["fingerprint"],
                    "step_id": step_id,
                    "retries_exhausted": True,
                    "message": f"Max retries ({max_retries}) exceeded. Workflow stalled.",
                }
                if state.parent_of(session_id) is not None:
                    return _wrap_child_failure_into_parent_escalation(
                        session, wf, reason="schema_violation", child_error=child_error,
                    )
                return child_error
            err_result: dict = {
                "status": "validation_error",
                "errors": validation_errors,
                "session_fingerprint": session["fingerprint"],
                "step_id": step_id,
                "retries_remaining": max_retries - retry_count,
            }
            hint = step.get("validation_hint")
            if hint:
                err_result["validation_hint"] = hint
            return err_result

        step_data = session["step_data"]
        step_data[step_id] = content

        # Evaluate guardrails before state transition
        guardrails = wf.get("guardrails", [])
        fired_guardrail = _evaluate_guardrails(guardrails, content, session, step_id) if guardrails else None
        guardrail_warning = None

        if fired_guardrail:
            action = fired_guardrail["action"]
            if action == "escalate":
                state.set_escalation(session_id, fired_guardrail["id"], fired_guardrail["message"])
                state.update_session(session_id, step_data=step_data)
                child_error = error_response(
                    "session_escalated",
                    "Session escalated by guardrail.",
                    session_fingerprint=session["fingerprint"],
                    guardrail_id=fired_guardrail["id"],
                    message=fired_guardrail["message"],
                )
                if state.parent_of(session_id) is not None:
                    return _wrap_child_failure_into_parent_escalation(
                        session, wf, reason="guardrail_escalate", child_error=child_error,
                    )
                return child_error
            elif action == "warn":
                guardrail_warning = fired_guardrail["message"]
            # force_branch handled below during next-step determination

        # Determine next step: force_branch guardrail overrides everything
        force_branched = False
        if fired_guardrail is not None and fired_guardrail["action"] == "force_branch":
            force_branched = True
            next_step_id = fired_guardrail["target_step"]
        elif step.get("branches"):
            valid_targets = [b["next"] for b in step["branches"]]
            target = branch if branch else step.get("default_branch", "")
            if target not in valid_targets:
                return error_response(
                    "invalid_argument",
                    f"Invalid branch '{target}'. Valid options: {valid_targets}",
                    field="branch",
                    session_fingerprint=session["fingerprint"],
                    step_id=step_id,
                )
            next_step_id = target
        else:
            is_last = idx == len(steps) - 1
            next_step_id = _COMPLETE if is_last else steps[idx + 1]["id"]

        try:
            next_step_id, _ = _apply_skip_loop(next_step_id, wf, step_data, force_branched)
        except _SkippedPredecessor as e:
            child_error = error_response(
                "skipped_predecessor_reference",
                f"Step '{e.referencing_step_id}' precondition references skipped predecessor '{e.sid}'",
                session_fingerprint=session["fingerprint"],
                step_id=step_id,
                referenced_step=e.sid,
                referencing_field="precondition",
            )
            if state.parent_of(session_id) is not None:
                return _wrap_child_failure_into_parent_escalation(
                    session, wf, reason="cascade_error", child_error=child_error,
                )
            return child_error

        # Auto-execute mcp_tool_call steps at the new pointer.
        next_step_id, cascade_err = _auto_execute_mcp_steps(
            next_step_id, wf, session_id, step_data, registry
        )
        if cascade_err is not None:
            state.update_session(session_id, current_step=next_step_id, step_data=step_data)
            if state.parent_of(session_id) is not None:
                return _wrap_child_failure_into_parent_escalation(
                    session, wf, reason="cascade_error", child_error=cascade_err,
                )
            return cascade_err

        # Track visit count for the next step
        if next_step_id != _COMPLETE:
            state.increment_visit(session_id, next_step_id)

        state.update_session(session_id, current_step=next_step_id, step_data=step_data)

        # Top-frame completion → frame-type dispatch: call-frames propagate
        # the child's artifact to the parent (M004); digression-frames just
        # pop and resume the outer session (M005).
        if next_step_id == _COMPLETE and state.parent_of(session_id) is not None:
            return _auto_resume_on_top_frame_complete(
                {**session, "current_step": next_step_id, "step_data": step_data},
                wf,
                workflows,
                registry=registry,
            )

        result = {
            "session_id": session_id,
            "submitted": {"id": step["id"], "title": step["title"]},
            "progress": f"step {idx + 1} of {len(steps)} complete",
        }
        if guardrail_warning:
            result["guardrail_warning"] = guardrail_warning

        if next_step_id == _COMPLETE:
            result["status"] = "workflow_complete"
            result["message"] = "All steps complete. Call generate_artifact to produce final output."
        else:
            _, nxt = _find_step(wf, next_step_id)
            result["next_step"] = {"id": nxt["id"], "title": nxt["title"]}
            if nxt.get("call"):
                result["next_step"]["call_target"] = nxt["call"]
            result["directive"] = nxt["directive_template"]
            result["do_not"] = _DO_NOT_RULES
            result["conversation_repair"] = _repair_for(wf)
            result["gates"] = nxt["gates"]
            if nxt.get("inject_context"):
                result["injected_context"] = _assemble_context(nxt["inject_context"], step_data)
            if nxt.get("directives"):
                result["directives"] = nxt["directives"]
            if nxt.get("branches"):
                result["branches"] = nxt["branches"]
                if nxt.get("default_branch"):
                    result["default_branch"] = nxt["default_branch"]

        return result

    @mcp.tool()
    @_trap_errors("step_id")
    def revise_step(session_id: str, step_id: str) -> dict:
        """Revise a previously completed step. Resets current_step to the target,
        returns its previous content, and deletes all step_data after it."""
        err = _check_str(session_id, "session_id", required=True) or _check_str(step_id, "step_id", required=True)
        if err:
            return err
        resolved, err = _resolve_session(session_id, workflows)
        if err:
            return err
        session, wf = resolved

        # Parent-owned guard (generalized): refuse mutation of any framed session
        # (call-frame or digression-frame) as long as it is still part of an active
        # stack rooted at a parent. Enforced via own_frame(session_id) — a non-None
        # return means this session has a stack row, i.e. a parent owns it.
        own = state.own_frame(session_id)
        if own is not None:
            parent_sid = state.parent_of(session_id)
            try:
                parent_session = state.get_session(parent_sid) if parent_sid else None
            except SessionNotFoundError:
                parent_session = None
            parent_current_step = parent_session.get("current_step", "") if parent_session else ""
            return error_response(
                "sub_workflow_parent_owned",
                f"child session fingerprint '{session['fingerprint']}' is owned by parent fingerprint "
                f"'{_fp(parent_sid) if parent_sid else None}' at "
                f"step '{parent_current_step}'. Revise the parent's step to unlink.",
                session_fingerprint=session["fingerprint"],
                parent_session_fingerprint=_fp(parent_sid) if parent_sid else None,
                call_step_id=parent_current_step,
                frame_type=own["frame_type"],
            )

        target_idx, target_step = _find_step(wf, step_id)
        if target_idx < 0:
            return error_response(
                "invalid_argument",
                f"Step '{step_id}' not found in workflow",
                field="step_id",
                session_fingerprint=session["fingerprint"],
            )

        # Call-steps: allow revise even when step_data[step_id] absent if a child is
        # retained/in-flight (propagation may have failed). Otherwise require completion.
        child_frame = state.top_frame_for(session_id)
        is_call_with_child = "call" in target_step and child_frame is not None
        if step_id not in session["step_data"] and not is_call_with_child:
            return error_response(
                "invalid_argument",
                f"Step '{step_id}' has not been completed yet",
                field="step_id",
                session_fingerprint=session["fingerprint"],
            )

        previous_content = session["step_data"].get(step_id, "")

        steps_after = [s["id"] for s in wf["steps"][target_idx + 1:]]
        state.invalidate_steps_after(session_id, steps_after)

        # Call-step cleanup: delete retained child, clear link, clear target's step_data.
        retained_child_deleted = None
        if "call" in target_step:
            called_sid = child_frame["session_id"] if child_frame is not None else None
            if called_sid:
                try:
                    state.delete_session(called_sid)
                except SessionNotFoundError:
                    pass  # Child already gone; clear link anyway.
                state.set_called_session(session_id, None)
                retained_child_deleted = called_sid
            state.clear_step_data_key(session_id, step_id)

        state.update_session(session_id, current_step=step_id)

        response = {
            "session_id": session_id,
            "revised_step": {"id": target_step["id"], "title": target_step["title"]},
            "previous_content": previous_content,
            "invalidated_steps": steps_after,
            "directive": target_step["directive_template"],
            "do_not": _DO_NOT_RULES,
            "conversation_repair": _repair_for(wf),
            "gates": target_step["gates"],
        }
        if retained_child_deleted is not None:
            response["retained_child_deleted"] = retained_child_deleted
        return response

    @mcp.tool()
    @_trap_errors("parent_session_id")
    def enter_sub_workflow(parent_session_id: str, call_step_id: str) -> dict:
        """Invoke a child workflow from a parent step that declares `call:`."""
        err = _check_str(parent_session_id, "parent_session_id", required=True) or \
              _check_str(call_step_id, "call_step_id", required=True)
        if err:
            return err

        resolved, err = _resolve_session(parent_session_id, workflows)
        if err:
            return err
        parent_session, parent_wf = resolved

        if parent_session["current_step"] == _COMPLETE:
            return error_response(
                "workflow_complete",
                "Parent workflow already complete.",
                session_fingerprint=parent_session["fingerprint"],
            )
        if parent_session.get("escalation"):
            return error_response(
                "session_escalated",
                "Parent session is escalated. Resolve the escalation before continuing.",
                session_fingerprint=parent_session["fingerprint"],
                escalation=parent_session["escalation"],
            )

        if parent_session["current_step"] != call_step_id:
            return error_response(
                "out_of_order_submission",
                f"parent current_step is '{parent_session['current_step']}', not '{call_step_id}'",
                session_fingerprint=parent_session["fingerprint"],
                expected_step=parent_session["current_step"],
                submitted_step=call_step_id,
            )

        _, call_step = _find_step(parent_wf, call_step_id)
        if call_step is None or "call" not in call_step:
            return error_response(
                "invalid_argument",
                f"parent step '{call_step_id}' has no `call` field",
                field="call_step_id",
                session_fingerprint=parent_session["fingerprint"],
            )

        pending_child = state.top_frame_for(parent_session_id)
        if pending_child is not None:
            return error_response(
                SUB_WORKFLOW_PENDING,
                "a child session is already in flight for this call-step",
                child_session_fingerprint=_fp(pending_child["session_id"]),
                parent_session_fingerprint=parent_session["fingerprint"],
                call_step_id=call_step_id,
                frame_type=pending_child["frame_type"],
            )

        target = call_step["call"]
        if target not in workflows:
            return error_response(
                "workflow_not_loaded",
                f"target workflow '{target}' not loaded",
                available_types=list(workflows.keys()),
            )

        child_context = ""
        ccf = call_step.get("call_context_from")
        if ccf:
            extracted = _resolve_ref(ccf, parent_session["step_data"], set(), call_step_id)
            if extracted is _REF_ABSENT or extracted is None:
                return error_response(
                    "invalid_argument",
                    f"call_context_from '{ccf}' did not resolve in parent step_data",
                    field="call_context_from",
                    session_fingerprint=parent_session["fingerprint"],
                )
            child_context = extracted if isinstance(extracted, str) else json.dumps(extracted)

        child_wf = workflows[target]
        first_step = child_wf["steps"][0]
        child_sid = state.create_session(
            target, current_step=first_step["id"], parent_session_id=parent_session_id
        )
        state.increment_visit(child_sid, first_step["id"])

        state.set_called_session(parent_session_id, child_sid, call_step_id=call_step_id)

        result = {
            "session_id": child_sid,
            "workflow_type": target,
            "current_step": {"id": first_step["id"], "title": first_step["title"]},
            "directive": first_step["directive_template"],
            "do_not": _DO_NOT_RULES,
            "conversation_repair": _repair_for(child_wf),
            "gates": first_step["gates"],
            "context": child_context,
            "parent_session_id": parent_session_id,
        }
        if first_step.get("directives"):
            result["directives"] = first_step["directives"]
        return result

    @mcp.tool()
    @_trap_errors("session_id")
    def push_flow(
        session_id: str, workflow_type: str, paused_at_step: str, context: str
    ) -> dict:
        """Interrupt the current session to run a different workflow as a digression.

        Pushes a digression-frame above session_id running workflow_type. When the
        digression completes, the outer session auto-resumes at paused_at_step.

        session_id: the currently-active top session to pause.
        workflow_type: workflow name to spawn as the digression.
        paused_at_step: outer-flow step where the interrupt happens. Server validates
            this matches outer.current_step (defensive echo; mirrors enter_sub_workflow's
            call_step_id arg) — mismatch returns out_of_order_submission rather than
            silently succeeding.
        context: initial context string for the spawned digression, analogous to
            start_workflow's context param. Digression frames have no authoring-time
            context handoff (unlike call-frames with call_context_from), so the
            client must seed context from the conversation on every push.
        """
        err = (
            _check_str(session_id, "session_id", required=True)
            or _check_str(workflow_type, "workflow_type", required=True)
            or _check_str(paused_at_step, "paused_at_step", required=True)
            or _check_str(context, "context")
        )
        if err:
            return err

        resolved, err = _resolve_session(session_id, workflows)
        if err:
            return err
        outer_session, _outer_wf = resolved

        if outer_session["current_step"] == _COMPLETE:
            return error_response(
                "workflow_complete",
                "Outer workflow already complete — cannot push a digression.",
                session_fingerprint=outer_session["fingerprint"],
            )
        if outer_session.get("escalation"):
            return error_response(
                "session_escalated",
                "Outer session is escalated. Resolve the escalation before pushing a digression.",
                session_fingerprint=outer_session["fingerprint"],
                escalation=outer_session["escalation"],
            )

        if outer_session["current_step"] != paused_at_step:
            return error_response(
                "out_of_order_submission",
                f"outer current_step is '{outer_session['current_step']}', not '{paused_at_step}'",
                session_fingerprint=outer_session["fingerprint"],
                expected_step=outer_session["current_step"],
                submitted_step=paused_at_step,
            )

        # Generalized pending guard: outer must itself be at the top of its chain.
        top = state.top_frame_for(session_id)
        if top is not None:
            top_fp = _fp(top["session_id"])
            return error_response(
                SUB_WORKFLOW_PENDING,
                f"a child session (fingerprint '{top_fp}') is already in flight above session "
                f"fingerprint '{outer_session['fingerprint']}'. Resolve it before pushing another digression.",
                parent_session_fingerprint=outer_session["fingerprint"],
                child_session_fingerprint=top_fp,
                call_step_id=paused_at_step,
                frame_type=top["frame_type"],
            )

        if workflow_type not in workflows:
            return error_response(
                "workflow_not_loaded",
                f"Unknown workflow type: {workflow_type}",
                available_types=list(workflows.keys()),
            )

        # Compute the outer session's root to cheaply surface session_stack_full
        # in the common pre-flight case. The authoritative guard is the atomic
        # check inside state.create_session below (BEGIN IMMEDIATE txn shared
        # with the stack insert); this pre-flight just spares one transaction
        # start + rollback in the steady state. Concurrent-race correctness
        # lives in create_session, not here.
        outer_own = state.own_frame(session_id)
        outer_root = outer_own["root_session_id"] if outer_own else session_id
        current_depth = state.stack_depth(outer_root)
        # Depth semantics (pinned at error-construction site): depth = frames
        # above root = stack_depth(root_session_id) return value. Lone session
        # → depth 0; one push → depth 1; at depth max_stack_depth, the next
        # push is rejected. Ordering: this check fires BEFORE session_cap_exceeded
        # because stack cap is the more specific condition.
        if current_depth >= max_stack_depth:
            return error_response(
                SESSION_STACK_FULL,
                f"session stack full: depth {current_depth} at max {max_stack_depth} "
                f"for root fingerprint '{_fp(outer_root)}'. Resolve a frame before pushing again.",
                current_depth=current_depth,
                max_depth=max_stack_depth,
                root_session_fingerprint=_fp(outer_root),
                depth_breakdown=state.depth_breakdown(),
            )

        # Global active-session cap: push_flow creates a new session just like
        # start_workflow, so the same ceiling applies. Same depth_breakdown
        # field shape as session_stack_full above so operators get a consistent
        # diagnostic across both cap-hit paths.
        active = state.count_active()
        if active >= 5:
            active_sessions = [s for s in state.list_sessions() if s["status"] == "active"]
            return error_response(
                "session_cap_exceeded",
                f"Session cap reached ({active}/5). Delete or complete a session first.",
                active_sessions=active_sessions,
                depth_breakdown=state.depth_breakdown(),
            )

        child_wf = workflows[workflow_type]
        first_step = child_wf["steps"][0]
        # create_session pushes a stack frame in the same transaction when
        # parent_session_id is set. Pass frame_type='digression' and stamp
        # call_step_id with paused_at_step (column is semantically overloaded:
        # call-step ID for frame_type='call', paused_at_step for 'digression').
        # max_stack_depth here is the ATOMIC guard: the depth re-read and the
        # stack insert live in the same BEGIN IMMEDIATE txn so two concurrent
        # pushes cannot both observe 'depth under cap' and both insert. If this
        # raises, no session row was committed — the rollback undoes both the
        # sessions INSERT and the session_stack INSERT atomically.
        try:
            child_sid = state.create_session(
                workflow_type,
                current_step=first_step["id"],
                parent_session_id=session_id,
                frame_type="digression",
                call_step_id=paused_at_step,
                max_stack_depth=max_stack_depth,
            )
        except state.StackFull as e:
            return error_response(
                SESSION_STACK_FULL,
                f"session stack full: depth {e.current_depth} at max {e.max_depth} "
                f"for root fingerprint '{_fp(e.root_session_id)}'. Lost the race; resolve a frame and retry.",
                current_depth=e.current_depth,
                max_depth=e.max_depth,
                root_session_fingerprint=_fp(e.root_session_id),
                depth_breakdown=state.depth_breakdown(),
            )
        state.increment_visit(child_sid, first_step["id"])

        frame_depth = state.stack_depth(
            state.own_frame(child_sid)["root_session_id"]  # type: ignore[index]
        )

        result = {
            "session_id": child_sid,
            "workflow_type": workflow_type,
            "frame_depth": frame_depth,
            "current_step": {"id": first_step["id"], "title": first_step["title"]},
            "directive": first_step["directive_template"],
            "do_not": _DO_NOT_RULES,
            "conversation_repair": _repair_for(child_wf),
            "gates": first_step["gates"],
            "context": context,
            "parent_session_id": session_id,
            "paused_at_step": paused_at_step,
        }
        if first_step.get("directives"):
            result["directives"] = first_step["directives"]
        return result

    @mcp.tool()
    @_trap_errors("session_id")
    def pop_flow(session_id: str) -> dict:
        """Explicitly pop a digression frame and resume the frame below.

        Client-driven counterpart to S01's auto-resume-on-complete path. Use this
        when the digression is abandoned / no longer relevant mid-flow — as
        opposed to submit_step driving it to __complete__, which triggers the
        same resume path automatically.

        Rejects call-frames (frame_type_not_poppable — call-frames are
        author-declared and author-resumed; use revise_step on the parent's
        call-step to abandon). Rejects bare sessions and roots
        (no_frame_to_pop — use delete_session to remove a root).

        Happy-path response mirrors the auto-resume-on-complete shape from
        _resume_parent_after_digression so clients handle both transitions the
        same way.
        """
        err = _check_str(session_id, "session_id", required=True)
        if err:
            return err

        resolved, err = _resolve_session(session_id, workflows)
        if err:
            return err
        session, _wf = resolved

        if session.get("escalation"):
            return error_response(
                "session_escalated",
                "Session is escalated. Resolve the escalation before popping.",
                session_fingerprint=session["fingerprint"],
                escalation=session["escalation"],
            )

        own = state.own_frame(session_id)
        if own is None:
            # Covers bare sessions (no stack row) and roots of multi-frame
            # chains — under the current schema, pushed frames start at
            # depth 1, so roots have no own-frame row. If depth-0 frames
            # later become expressible, split this into a distinct
            # bottom_frame_pop_rejected branch.
            return error_response(
                NO_FRAME_TO_POP,
                f"session fingerprint '{session['fingerprint']}' has no stack frame to pop.",
                session_fingerprint=session["fingerprint"],
            )

        if own["frame_type"] == "call":
            return error_response(
                FRAME_TYPE_NOT_POPPABLE,
                f"session fingerprint '{session['fingerprint']}' is a call-frame; call-frames are "
                "author-resumed. Use revise_step on the parent's call-step to abandon.",
                session_fingerprint=session["fingerprint"],
                frame_type="call",
            )

        # Happy path: digression frame at depth >= 1. Delegate to the same
        # resume helper the auto-resume-on-complete path uses so clients see
        # byte-compatible response shapes across both transitions.
        return _resume_parent_after_digression(session, workflows, registry=registry)

    @mcp.tool()
    @_trap_errors()
    def list_sessions() -> dict:
        """List all sessions with their status (active/completed)."""
        return {"sessions": state.list_sessions()}

    @mcp.tool()
    @_trap_errors("session_id")
    def delete_session(session_id: str) -> dict:
        """Delete a session regardless of state. Returns what was deleted."""
        err = _check_str(session_id, "session_id", required=True)
        if err:
            return err
        # Identity access-check: delete_session bypasses _resolve_session (it
        # doesn't need the workflow object), so plant the check explicitly
        # here. Structurally a no-op today under ANONYMOUS_IDENTITY both sides.
        try:
            _owner_session = state.get_session(session_id)
        except SessionNotFoundError:
            _owner_session = None
        if _owner_session is not None:
            caller_identity = caller_identity_var.get()
            if caller_identity != _owner_session["owner_identity"]:
                return error_response(
                    CROSS_SESSION_ACCESS_DENIED,
                    f"caller identity does not own session {_owner_session['fingerprint']}",
                    session_fingerprint=_owner_session["fingerprint"],
                )
        # Parent-owned guard (generalized): refuse deletion of any framed session
        # (call-frame or digression-frame) while a parent still owns it. See the
        # matching guard in revise_step for the mechanism.
        own = state.own_frame(session_id)
        if own is not None:
            parent_sid = state.parent_of(session_id)
            try:
                parent_session = state.get_session(parent_sid) if parent_sid else None
            except SessionNotFoundError:
                parent_session = None
            parent_current_step = parent_session.get("current_step", "") if parent_session else ""
            return error_response(
                "sub_workflow_parent_owned",
                f"child session fingerprint '{_fp(session_id)}' is owned by parent fingerprint "
                f"'{_fp(parent_sid) if parent_sid else None}' at "
                f"step '{parent_current_step}'. Revise the parent's step to unlink.",
                session_fingerprint=_fp(session_id),
                parent_session_fingerprint=_fp(parent_sid) if parent_sid else None,
                call_step_id=parent_current_step,
                frame_type=own["frame_type"],
            )
        try:
            deleted = state.delete_session(session_id)
        except SessionNotFoundError:
            return error_response(
                "session_not_found", "session not found", session_fingerprint=_fp(session_id)
            )
        return {
            "session_id": deleted["session_id"],
            "workflow_type": deleted["workflow_type"],
            "current_step": deleted["current_step"],
            "completed": deleted["current_step"] == _COMPLETE,
        }

    @mcp.tool()
    @_trap_errors("session_id")
    def generate_artifact(session_id: str, output_format: str = "auto") -> dict:
        """Generate final artifact from completed workflow. Rejects if workflow is not complete.

        output_format controls the shape of the returned artifact:
        - "auto" (default): returns the raw content of the last step only.
        - "text": joins all step contents into a single string separated by double newlines.
        - "structured_code": returns a list of dicts, each with keys step_id, title, and content.
        """
        err = _check_str(session_id, "session_id", required=True) or _check_str(output_format, "output_format")
        if err:
            return err
        resolved, err = _resolve_session(session_id, workflows)
        if err:
            return err
        session, wf = resolved

        if session["current_step"] != _COMPLETE:
            idx, _ = _find_step(wf, session["current_step"])
            remaining = [s["title"] for s in wf["steps"][idx:]]
            return error_response(
                "invalid_argument",
                "Workflow not complete. Finish all steps first.",
                field="session_state",
                session_fingerprint=session["fingerprint"],
                remaining_steps=remaining,
            )

        step_data = session["step_data"]

        if output_format == "auto":
            last_step_id = wf["steps"][-1]["id"]
            artifact: object = step_data.get(last_step_id, "")
        elif output_format == "structured_code":
            artifact = [
                {"step_id": s["id"], "title": s["title"], "content": step_data.get(s["id"], "")}
                for s in wf["steps"]
            ]
        else:
            artifact = "\n\n".join(step_data.get(s["id"], "") for s in wf["steps"])

        if isinstance(artifact, str):
            artifact_bytes = len(artifact.encode("utf-8"))
        else:
            artifact_bytes = len(json.dumps(artifact).encode("utf-8"))
        if artifact_bytes > ARTIFACT_MAX:
            return error_response(
                "oversize_payload",
                f"artifact exceeds {ARTIFACT_MAX} bytes ({artifact_bytes} bytes serialized)",
                field="artifact",
                max_bytes=ARTIFACT_MAX,
                actual_bytes=artifact_bytes,
                session_fingerprint=session["fingerprint"],
            )

        return {
            "session_id": session_id,
            "workflow_type": session["workflow_type"],
            "output_format": output_format,
            "artifact": artifact,
        }
