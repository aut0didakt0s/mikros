"""MCP tool functions for mikros workflow engine."""

from server import state

_COMPLETE = "__complete__"

_DO_NOT_RULES = [
    "Do NOT skip ahead to later steps.",
    "Do NOT produce final artifacts yet.",
    "Do NOT ask multiple questions at once.",
    "Do NOT proceed until all gates for this step are satisfied.",
    "Do NOT submit a step without showing your work to the user and waiting for their confirmation. Each step is a conversation, not a task you complete silently.",
    "Do NOT submit multiple steps in a single response. Complete ONE step, present it, wait for the user to respond, then move to the next.",
    "Do NOT reveal step names, step numbers, or internal workflow mechanics to the user. The workflow should feel like a natural conversation, not a numbered checklist. Never say things like 'Step 2: Decompose and Structure' or 'we are now in the plan phase'.",
]


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
        return None, {"error": str(e), "session_id": session_id}
    wf = workflows.get(session["workflow_type"])
    if not wf:
        return None, {"error": f"Workflow '{session['workflow_type']}' not loaded", "session_id": session_id}
    return (session, wf), None


def register_tools(mcp, workflows):
    """Register workflow tools on the FastMCP app."""

    @mcp.tool()
    def start_workflow(workflow_type: str, context: str) -> dict:
        """Start a new workflow session. Returns the first step's directive."""
        if workflow_type not in workflows:
            return {
                "error": f"Unknown workflow type: {workflow_type}",
                "available_types": list(workflows.keys()),
            }

        wf = workflows[workflow_type]
        first_step = wf["steps"][0]
        sid = state.create_session(workflow_type, current_step=first_step["id"])

        return {
            "session_id": sid,
            "workflow_type": workflow_type,
            "current_step": {"id": first_step["id"], "title": first_step["title"]},
            "directive": first_step["directive_template"],
            "do_not": _DO_NOT_RULES,
            "gates": first_step["gates"],
            "context": context,
        }

    @mcp.tool()
    def get_state(session_id: str) -> dict:
        """Get current session state: step, progress, and what's pending."""
        resolved, err = _resolve_session(session_id, workflows)
        if err:
            return err
        session, wf = resolved

        steps = wf["steps"]
        idx, current = _find_step(wf, session["current_step"])
        pending = [s["title"] for s in steps[idx + 1:]] if idx >= 0 else []

        return {
            "session_id": session_id,
            "workflow_type": session["workflow_type"],
            "current_step": {"id": current["id"], "title": current["title"]} if current else None,
            "progress": f"step {idx + 1} of {len(steps)}" if idx >= 0 else "unknown",
            "pending_steps": pending,
            "step_data": session["step_data"],
        }

    @mcp.tool()
    def get_guidelines(session_id: str) -> dict:
        """Get anti-patterns and gates for the current step."""
        resolved, err = _resolve_session(session_id, workflows)
        if err:
            return err
        session, wf = resolved

        _, step = _find_step(wf, session["current_step"])
        if not step:
            return {"error": f"Step '{session['current_step']}' not found in workflow", "session_id": session_id}

        return {
            "session_id": session_id,
            "current_step": {"id": step["id"], "title": step["title"]},
            "anti_patterns": step["anti_patterns"],
            "gates": step["gates"],
        }

    @mcp.tool()
    def submit_step(session_id: str, step_id: str, content: str) -> dict:
        """Submit content for the current step. Enforces ordering — no skips, no backwards."""
        resolved, err = _resolve_session(session_id, workflows)
        if err:
            return err
        session, wf = resolved

        current = session["current_step"]
        if current == _COMPLETE:
            return {
                "error": "Workflow already complete. Call generate_artifact to get final output.",
                "session_id": session_id,
            }

        if step_id != current:
            return {
                "error": f"Out-of-order submission: expected '{current}', got '{step_id}'",
                "session_id": session_id,
                "expected_step": current,
                "submitted_step": step_id,
            }

        steps = wf["steps"]
        idx, step = _find_step(wf, step_id)

        # Store content
        step_data = session["step_data"]
        step_data[step_id] = content

        is_last = idx == len(steps) - 1
        next_step_id = _COMPLETE if is_last else steps[idx + 1]["id"]
        state.update_session(session_id, current_step=next_step_id, step_data=step_data)

        result: dict = {
            "session_id": session_id,
            "submitted": {"id": step["id"], "title": step["title"]},
            "progress": f"step {idx + 1} of {len(steps)} complete",
        }

        if is_last:
            result["status"] = "workflow_complete"
            result["message"] = "All steps complete. Call generate_artifact to produce final output."
        else:
            nxt = steps[idx + 1]
            result["next_step"] = {"id": nxt["id"], "title": nxt["title"]}
            result["directive"] = nxt["directive_template"]
            result["do_not"] = _DO_NOT_RULES
            result["gates"] = nxt["gates"]

        return result

    @mcp.tool()
    def list_sessions() -> dict:
        """List all sessions with their status (active/completed)."""
        return {"sessions": state.list_sessions()}

    @mcp.tool()
    def delete_session(session_id: str) -> dict:
        """Delete a session regardless of state. Returns what was deleted."""
        try:
            deleted = state.delete_session(session_id)
        except KeyError as e:
            return {"error": str(e), "session_id": session_id}
        return {
            "session_id": deleted["session_id"],
            "workflow_type": deleted["workflow_type"],
            "current_step": deleted["current_step"],
            "completed": deleted["current_step"] == _COMPLETE,
        }

    @mcp.tool()
    def generate_artifact(session_id: str, output_format: str = "auto") -> dict:
        """Generate final artifact from completed workflow. Rejects if workflow is not complete.

        output_format controls the shape of the returned artifact:
        - "auto" (default): uses the workflow's output_format field; falls back to "text" if not set.
        - "text": joins all step contents into a single string separated by double newlines.
        - "structured_code": returns a list of dicts, each with keys step_id, title, and content.
        """
        resolved, err = _resolve_session(session_id, workflows)
        if err:
            return err
        session, wf = resolved

        if session["current_step"] != _COMPLETE:
            idx, _ = _find_step(wf, session["current_step"])
            remaining = [s["title"] for s in wf["steps"][idx:]]
            return {
                "error": "Workflow not complete. Finish all steps first.",
                "session_id": session_id,
                "remaining_steps": remaining,
            }

        fmt = output_format if output_format != "auto" else wf.get("output_format", "text")
        step_data = session["step_data"]

        if fmt == "structured_code":
            artifact: object = [
                {"step_id": s["id"], "title": s["title"], "content": step_data.get(s["id"], "")}
                for s in wf["steps"]
            ]
        else:
            artifact = "\n\n".join(step_data.get(s["id"], "") for s in wf["steps"])

        return {
            "session_id": session_id,
            "workflow_type": session["workflow_type"],
            "output_format": fmt,
            "artifact": artifact,
        }
