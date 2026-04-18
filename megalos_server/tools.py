"""MCP tool functions for megalos workflow engine."""

import functools
import json
import re

import jsonschema

from . import state
from .errors import ARTIFACT_MAX, CONTENT_MAX, error_response
from .state import COMPLETE as _COMPLETE

_DEFAULT_MAX_RETRIES = 3

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
    """Decorator: convert KeyError → session_not_found, TypeError/ValueError → invalid_argument."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except KeyError as e:
                sid = kwargs.get("session_id") or (args[0] if args and isinstance(args[0], str) else None)
                return error_response("session_not_found", str(e), session_id=sid)
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
    """Look up session and its workflow. Returns (session, wf) or (None, error_dict)."""
    try:
        session = state.get_session(session_id)
    except KeyError as e:
        return None, error_response("session_not_found", str(e), session_id=session_id)
    wf = workflows.get(session["workflow_type"])
    if not wf:
        return None, error_response("workflow_not_loaded", f"Workflow '{session['workflow_type']}' not loaded", session_id=session_id)
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


def register_tools(mcp, workflows):
    """Register workflow tools on the FastMCP app."""

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
            return error_response(
                "session_cap_exceeded",
                f"Session cap reached ({active}/5). Delete or complete a session first.",
                active_sessions=active_sessions,
            )

        wf = workflows[workflow_type]
        first_step = wf["steps"][0]
        sid = state.create_session(workflow_type, current_step=first_step["id"])
        state.increment_visit(sid, first_step["id"])

        result = {
            "session_id": sid,
            "workflow_type": workflow_type,
            "current_step": {"id": first_step["id"], "title": first_step["title"]},
            "directive": first_step["directive_template"],
            "do_not": _DO_NOT_RULES,
            "conversation_repair": _repair_for(wf),
            "gates": first_step["gates"],
            "context": context,
        }
        if first_step.get("directives"):
            result["directives"] = first_step["directives"]
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
                session_id=session_id,
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
                session_id=session_id,
                escalation=session["escalation"],
            )

        current = session["current_step"]
        if current == _COMPLETE:
            return error_response(
                "workflow_complete",
                "Workflow already complete. Call generate_artifact to get final output.",
                session_id=session_id,
            )

        if step_id != current:
            return error_response(
                "out_of_order_submission",
                f"Out-of-order submission: expected '{current}', got '{step_id}'",
                session_id=session_id,
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
                session_id=session_id,
                step_id=step_id,
            )

        steps = wf["steps"]
        idx, step = _find_step(wf, step_id)

        # Handle intermediate artifacts
        ia_list = step.get("intermediate_artifacts")
        if ia_list:
            if not artifact_id:
                expected = [a["id"] for a in ia_list]
                return error_response(
                    "invalid_argument",
                    "Step has intermediate_artifacts. Must specify artifact_id.",
                    field="artifact_id",
                    session_id=session_id,
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
                    session_id=session_id,
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
                    "session_id": session_id,
                    "step_id": step_id,
                    "artifact_id": artifact_id,
                }
            validator = jsonschema.Draft202012Validator(art_def["schema"])
            art_errors = [err.message for err in validator.iter_errors(parsed)]
            if art_errors:
                return {
                    "status": "validation_error",
                    "errors": art_errors,
                    "session_id": session_id,
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
                    return error_response(
                        "session_escalated",
                        "Session escalated by guardrail.",
                        session_id=session_id,
                        guardrail_id=fired_guardrail["id"],
                        message=fired_guardrail["message"],
                    )
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
                        session_id=session_id,
                        step_id=step_id,
                    )
                next_step_id = target
            else:
                is_last = idx == len(steps) - 1
                next_step_id = _COMPLETE if is_last else steps[idx + 1]["id"]

            try:
                next_step_id, _ = _apply_skip_loop(next_step_id, wf, step_data, force_branched)
            except _SkippedPredecessor as e:
                return error_response(
                    "skipped_predecessor_reference",
                    f"Step '{e.referencing_step_id}' precondition references skipped predecessor '{e.sid}'",
                    session_id=session_id,
                    step_id=step_id,
                    referenced_step=e.sid,
                    referencing_field="precondition",
                )

            if next_step_id != _COMPLETE:
                state.increment_visit(session_id, next_step_id)
            state.update_session(session_id, current_step=next_step_id, step_data=step_data)

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
                return {
                    "status": "validation_error",
                    "errors": validation_errors,
                    "session_id": session_id,
                    "step_id": step_id,
                    "retries_exhausted": True,
                    "message": f"Max retries ({max_retries}) exceeded. Workflow stalled.",
                }
            err_result: dict = {
                "status": "validation_error",
                "errors": validation_errors,
                "session_id": session_id,
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
                return error_response(
                    "session_escalated",
                    "Session escalated by guardrail.",
                    session_id=session_id,
                    guardrail_id=fired_guardrail["id"],
                    message=fired_guardrail["message"],
                )
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
                    session_id=session_id,
                    step_id=step_id,
                )
            next_step_id = target
        else:
            is_last = idx == len(steps) - 1
            next_step_id = _COMPLETE if is_last else steps[idx + 1]["id"]

        try:
            next_step_id, _ = _apply_skip_loop(next_step_id, wf, step_data, force_branched)
        except _SkippedPredecessor as e:
            return error_response(
                "skipped_predecessor_reference",
                f"Step '{e.referencing_step_id}' precondition references skipped predecessor '{e.sid}'",
                session_id=session_id,
                step_id=step_id,
                referenced_step=e.sid,
                referencing_field="precondition",
            )

        # Track visit count for the next step
        if next_step_id != _COMPLETE:
            state.increment_visit(session_id, next_step_id)

        state.update_session(session_id, current_step=next_step_id, step_data=step_data)

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

        target_idx, target_step = _find_step(wf, step_id)
        if target_idx < 0:
            return error_response(
                "invalid_argument",
                f"Step '{step_id}' not found in workflow",
                field="step_id",
                session_id=session_id,
            )

        if step_id not in session["step_data"]:
            return error_response(
                "invalid_argument",
                f"Step '{step_id}' has not been completed yet",
                field="step_id",
                session_id=session_id,
            )

        previous_content = session["step_data"][step_id]

        steps_after = [s["id"] for s in wf["steps"][target_idx + 1:]]
        state.invalidate_steps_after(session_id, steps_after)

        state.update_session(session_id, current_step=step_id)

        return {
            "session_id": session_id,
            "revised_step": {"id": target_step["id"], "title": target_step["title"]},
            "previous_content": previous_content,
            "invalidated_steps": steps_after,
            "directive": target_step["directive_template"],
            "do_not": _DO_NOT_RULES,
            "conversation_repair": _repair_for(wf),
            "gates": target_step["gates"],
        }

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
                session_id=parent_session_id,
            )
        if parent_session.get("escalation"):
            return error_response(
                "session_escalated",
                "Parent session is escalated. Resolve the escalation before continuing.",
                session_id=parent_session_id,
                escalation=parent_session["escalation"],
            )

        if parent_session["current_step"] != call_step_id:
            return error_response(
                "out_of_order_submission",
                f"parent current_step is '{parent_session['current_step']}', not '{call_step_id}'",
                session_id=parent_session_id,
                expected_step=parent_session["current_step"],
                submitted_step=call_step_id,
            )

        _, call_step = _find_step(parent_wf, call_step_id)
        if call_step is None or "call" not in call_step:
            return error_response(
                "invalid_argument",
                f"parent step '{call_step_id}' has no `call` field",
                field="call_step_id",
                session_id=parent_session_id,
            )

        if parent_session.get("called_session"):
            return error_response(
                "sub_workflow_pending",
                "a child session is already in flight for this call-step",
                child_session_id=parent_session["called_session"],
                parent_session_id=parent_session_id,
                call_step_id=call_step_id,
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
                    session_id=parent_session_id,
                )
            child_context = extracted if isinstance(extracted, str) else json.dumps(extracted)

        child_wf = workflows[target]
        first_step = child_wf["steps"][0]
        child_sid = state.create_session(
            target, current_step=first_step["id"], parent_session_id=parent_session_id
        )
        state.increment_visit(child_sid, first_step["id"])

        state.set_called_session(parent_session_id, child_sid)

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
        try:
            deleted = state.delete_session(session_id)
        except KeyError as e:
            return error_response("session_not_found", str(e), session_id=session_id)
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
                session_id=session_id,
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
                session_id=session_id,
            )

        return {
            "session_id": session_id,
            "workflow_type": session["workflow_type"],
            "output_format": output_format,
            "artifact": artifact,
        }
