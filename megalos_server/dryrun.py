"""CLI entry point for the megálos dry-run inspector.

Step through a workflow without calling an LLM. Bootstrap (env-var
discipline, production workflow loader reuse via ``create_app``) is
followed by a classification-first REPL loop that drives ``start_workflow``
+ ``submit_step`` through ``app.call_tool`` until the workflow completes or
errors. Schema-validation re-prompts are handled here in-loop without a
local retry counter — production owns retry state via ``state.increment_retry``.

Branch-selection UX, precondition display, advance-delta skip detection, and
invalid-branch error decoding are implemented REPL-side against the envelope
shape emitted by ``megalos_server.tools`` (no new server surface).

Sub-workflow descent + ascent (S04) is modelled as a REPL-side session
stack of frames — bottom frame is the root parent, top frame is the
currently-active session. Descent pushes on ``enter_sub_workflow``;
ascent pops on ``propagated_from_sub_workflow`` envelope marker. The REPL
does not call ``get_state`` — all frame state is derived from envelope
deltas (D047). Call-step detection prefers the envelope's ``call_target``
field but falls back to the workflow dict for first-step entries that
arrive without it (D048).
"""

# 1. Env var set BEFORE any megalos_server.* import.
import os

os.environ.setdefault("MEGALOS_DB_PATH", ":memory:")

# 2. Stdlib imports.
import argparse
import asyncio
import sys
from pathlib import Path

import yaml

# 3. megalos_server import — only create_app, nothing else.
from megalos_server import create_app


# Terminal statuses that end the REPL. `workflow_complete` exits 0; the rest
# exit 1. `session_escalated` and `workflow_changed` are listed for defense —
# production currently emits them under `status="error"` with a `code` field,
# but the plan (D039) pins them as first-class terminals for forward
# compatibility.
_TERMINAL_STATUSES = frozenset(
    {"workflow_complete", "error", "session_escalated", "workflow_changed"}
)

# Visual-nesting cap (D049). Child banners + metadata indent
# ``"  " * min(depth, 4)`` where depth = len(stack) - 1 (0 = root, 1 = first
# child, etc.). At depth 4 we print a one-time `[max nesting depth reached]`
# signal next to the descent banner.
_MAX_DEPTH = 4


def _indent_for(depth: int) -> str:
    """Return the visual-indent prefix for a given stack depth."""
    return "  " * min(depth, _MAX_DEPTH)


def _print_validation_error(envelope: dict, indent: str) -> None:
    """Print a validation_error envelope to stderr; no exit decision here."""
    print(f"{indent}Validation failed:", file=sys.stderr)
    for err in envelope["errors"]:
        print(f"{indent}  - {err}", file=sys.stderr)
    hint = envelope.get("validation_hint")
    if hint:
        print(f"{indent}Hint: {hint}", file=sys.stderr)


def _print_terminal(envelope: dict, indent: str) -> None:
    """Print the operator-facing payload of a terminal envelope.

    Decodes documented error codes into human-readable stderr lines (D044).
    All decoded codes stay terminal; no re-prompt.  Undocumented error codes
    fall through to generic message/code/error surfacing.
    """
    status = envelope.get("status")
    if status == "workflow_complete":
        print(f"\n{indent}=== Workflow complete ===")
        print(f"{indent}{envelope.get('message', '')}")
        return
    code = envelope.get("code")
    error = envelope.get("error")
    # D044: decoded invalid_branch surface. The server's `error` string is
    # already `"Invalid branch '<t>'. Valid options: <list>"` verbatim
    # (tools.py ~1173-1180), so passing it through is the cleanest render.
    if code == "invalid_argument" and envelope.get("field") == "branch" and error:
        print(error, file=sys.stderr)
        return
    # D044: decoded cascade error on skipped predecessor reference.
    if code == "skipped_predecessor_reference":
        referencing = envelope.get("step_id", "?")
        referenced = envelope.get("referenced_step", "?")
        print(
            f"Skipped predecessor reference: step '{referencing}' "
            f"references '{referenced}' which was skipped",
            file=sys.stderr,
        )
        return
    # D044: sub-workflow / call-step decoded surfaces.
    if code == "call_step_requires_enter_sub_workflow":
        step_id = envelope.get("step_id", "?")
        call_target = envelope.get("call_target", "?")
        print(
            f"REPL bug: submit_step was called on call-step '{step_id}' "
            f"(target: {call_target}). This should not happen.",
            file=sys.stderr,
        )
        return
    if code == "sub_workflow_pending":
        child_fp = envelope.get("child_session_fingerprint", "?")
        print(
            f"Sub-workflow pending: child session (fingerprint {child_fp}) "
            f"is in flight. Resolve it before continuing.",
            file=sys.stderr,
        )
        return
    if code == "workflow_not_loaded":
        # Emission sites carry workflow name either in error message or as an
        # explicit target. enter_sub_workflow's surface includes
        # `available_types`; pull both for the decoded line.
        available = envelope.get("available_types") or []
        # The target name appears in the `error` string as
        # "target workflow '<name>' not loaded" — extract from there when
        # enter_sub_workflow emits it; otherwise the raw error is clearer.
        target = "?"
        if error and "'" in error:
            try:
                target = error.split("'", 2)[1]
            except IndexError:
                target = "?"
        print(
            f"Sub-workflow '{target}' not loaded. Available: "
            f"{', '.join(available)}",
            file=sys.stderr,
        )
        return
    if code == "out_of_order_submission":
        expected = envelope.get("expected_step", "?")
        submitted = envelope.get("submitted_step", "?")
        print(
            f"Out-of-order: expected step '{expected}', got '{submitted}'.",
            file=sys.stderr,
        )
        return
    # Parent output_schema rejection of child artifact on propagation. The
    # server emits ``session_escalated`` with a ``called_workflow_error``
    # wrapper whose nested ``child_error`` carries the validation errors
    # produced by ``_validate_output`` against the parent call-step's schema
    # (tools.py lines 712-725). Render the child errors verbatim under a
    # decoded header; do NOT dump the artifact body (wire noise).
    if code == "session_escalated":
        wrapper = envelope.get("called_workflow_error") or {}
        child_error = wrapper.get("child_error") or {}
        if child_error.get("reason") == "parent_output_schema_fail":
            child_wf = wrapper.get("child_workflow_type", "?")
            errors = child_error.get("errors") or []
            print(
                f"Sub-workflow '{child_wf}' completed, but its artifact "
                f"failed parent's output_schema:",
                file=sys.stderr,
            )
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            print("Parent session escalated.", file=sys.stderr)
            return
        # Parent drifted off the call-step during sub-workflow propagation
        # (tools.py lines 700-710). Envelope has no ``called_workflow_error``
        # wrapper; parent_session_fingerprint + child_session_fingerprint sit
        # at the top level. The parent's previously-stamped current_step is
        # not part of the envelope — surface the fingerprints verbatim.
        parent_fp = envelope.get("parent_session_fingerprint", "?")
        child_fp = envelope.get("child_session_fingerprint", "?")
        print(
            f"Sub-workflow state drift: parent session (fingerprint "
            f"{parent_fp}) is no longer at a call-step; child "
            f"(fingerprint {child_fp}) retained for inspection.",
            file=sys.stderr,
        )
        return
    if code == "invalid_argument" and envelope.get("field") == "call_context_from":
        # The error string is "call_context_from '<ref>' did not resolve in
        # parent step_data" — extract the ref between the first pair of
        # single quotes for the decoded surface.
        ref = "?"
        if error and "'" in error:
            try:
                ref = error.split("'", 2)[1]
            except IndexError:
                ref = "?"
        print(
            f"Invalid call_context_from: ref '{ref}' did not resolve "
            f"in parent step_data.",
            file=sys.stderr,
        )
        return
    # Generic non-success terminal: surface message if present, else error keys.
    message = envelope.get("message")
    if message:
        print(message, file=sys.stderr)
    if code:
        print(f"code: {code}", file=sys.stderr)
    if error:
        print(f"error: {error}", file=sys.stderr)


def _render_precondition(pc: dict) -> str | None:
    """Return human-readable `Precondition: ...` line for a step's precondition.

    Returns ``None`` if the predicate shape is unrecognized — forward
    compatibility without raw-dict leakage.
    """
    if "when_equals" in pc:
        we = pc["when_equals"]
        ref = we.get("ref", "?")
        value = we.get("value", "?")
        return f'Precondition: {ref} == "{value}"'
    if "when_present" in pc:
        return f"Precondition: {pc['when_present']} is present"
    return None


def _prompt_branch(branches: list, default: str, indent: str = "") -> str:
    """Prompt the operator for a branch choice; return the resolved step_id.

    Renders a numbered list with a `[default]` tag on the default row, then
    loops locally on invalid numeric input (no server round-trip). Empty
    input resolves to ``default``.
    """
    print(f"{indent}Branches:")
    for i, b in enumerate(branches, start=1):
        tag = " [default]" if b["next"] == default else ""
        print(f"{indent}  {i}. {b['next']} — {b['condition']}{tag}")
    n = len(branches)
    while True:
        try:
            raw = input(f"Choose branch [1-{n}, empty = default]: ")
        except EOFError:
            print("Dry-run aborted by user (EOF)", file=sys.stderr)
            sys.exit(1)
        raw = raw.strip()
        if raw == "":
            print(f"{indent}→ {default} (default)")
            return default
        try:
            idx = int(raw)
        except ValueError:
            print(
                f"Invalid branch selection '{raw}'. Enter 1-{n} or empty.",
                file=sys.stderr,
            )
            continue
        if 1 <= idx <= n:
            target = branches[idx - 1]["next"]
            print(f"{indent}→ {target}")
            return target
        print(
            f"Invalid branch selection '{raw}'. Enter 1-{n} or empty.",
            file=sys.stderr,
        )


def _detect_call_target(envelope: dict, workflow: dict, step_id: str) -> str | None:
    """Return the call target for the currently-active step, or None.

    Prefer the envelope's ``call_target`` (set by submit_step advance +
    _advance_parent + _resume_parent_after_digression — tools.py lines
    523/628/1246/1404). Fall back to the workflow dict's ``"call"`` field
    for the first step of any session (``start_workflow`` and
    ``enter_sub_workflow`` do NOT populate ``call_target`` on their
    first-step envelopes — D048 fallback path).
    """
    # Envelope-populated path (mid-workflow advance).
    next_step = envelope.get("next_step") or {}
    if isinstance(next_step, dict) and next_step.get("call_target"):
        return str(next_step["call_target"])
    current_step = envelope.get("current_step") or {}
    if isinstance(current_step, dict) and current_step.get("call_target"):
        return str(current_step["call_target"])
    # Workflow-dict fallback: first-step call on a fresh session envelope.
    for s in workflow["steps"]:
        if s["id"] == step_id:
            return s.get("call")
    return None


def _descend(
    stack: list, call_step_id: str, mcp: object
) -> dict:
    """Enter a sub-workflow, push a child frame, return the child envelope.

    On error envelope, returns the envelope unchanged so the caller routes
    it into the terminal decoder — no push.
    """
    parent_sid = stack[-1]["session_id"]
    result = asyncio.run(
        mcp.call_tool(  # type: ignore[attr-defined]
            "enter_sub_workflow",
            {"parent_session_id": parent_sid, "call_step_id": call_step_id},
        )
    )
    assert result is not None
    envelope = result.structured_content
    assert envelope is not None
    if envelope.get("status") == "error":
        return envelope
    child_sid = envelope["session_id"]
    child_wf_name = envelope["workflow_type"]
    # ``_megalos_workflows`` attribute is attached to the FastMCP instance by
    # ``create_app``; access via getattr to keep mypy quiet about the dynamic
    # attribute without leaking a type: ignore across every call site.
    child_wf = getattr(mcp, "_megalos_workflows")[child_wf_name]
    depth_before_push = len(stack) - 1  # depth of the child that is about to sit above parent
    # Child-level indent: after push the child is at depth = len(stack).
    # Banner for descent is printed at the CHILD's indent depth (one deeper
    # than the parent).
    child_depth = depth_before_push + 1
    indent = _indent_for(child_depth)
    print(f"{indent}→ Entering sub-workflow '{child_wf_name}'")
    if child_depth >= _MAX_DEPTH:
        print(f"{indent}[max nesting depth reached]")
    context = envelope.get("context", "")
    if context:
        # D048: `call_context_from` non-string values arrive as
        # ``json.dumps`` output (tools.py:1591). Print the string verbatim;
        # do NOT parse or pretty-print.
        print(f"{indent}  Context: {context}")
    stack.append(
        {
            "session_id": child_sid,
            "workflow": child_wf,
            "prev_step_idx": -1,
            "call_step_id": call_step_id,
        }
    )
    return envelope


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="megalos-dryrun",
        description="Step through a megálos workflow interactively, without calling an LLM.",
    )
    parser.add_argument("workflow", type=Path, help="Path to workflow YAML file")
    parser.add_argument(
        "--context",
        default="",
        help="Initial context string passed to start_workflow",
    )
    args = parser.parse_args()

    # Step 1 — Resolve + check target path.
    target: Path = args.workflow.resolve()
    if not target.exists():
        print(f"Workflow file not found: {target}", file=sys.stderr)
        sys.exit(1)

    # Step 2 — Read target, extract workflow name.
    try:
        target_doc = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        print(f"Target file {target} is not valid YAML: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(target_doc, dict) or "name" not in target_doc:
        print(f"Target file {target} has no 'name' field.", file=sys.stderr)
        sys.exit(1)
    target_name = target_doc["name"]

    # Step 3 — Load workflows via create_app with Approach E framing.
    try:
        mcp = create_app(workflow_dir=target.parent)
    except Exception as e:
        print(
            f"Failed to load workflows from {target.parent}:\n"
            f"  {e}\n\n"
            f"Note: dry-run loads all *.yaml files in the parent directory "
            f"(required for sub-workflow 'call' target resolution). If the error "
            f"above names a file other than {target.name}, a sibling workflow "
            f"has a problem — fix it, or move {target.name} to a directory "
            f"containing only it and its call targets.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Step 4 — Verify target in loaded map.
    workflows = mcp._megalos_workflows  # type: ignore[attr-defined]
    if target_name not in workflows:
        print(
            f"Workflow name '{target_name}' from {target} was not loaded. "
            f"Loaded names: {sorted(workflows)}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Step 5 — REPL loop: drive start_workflow + submit_step via app.call_tool.
    # FastMCP types call_tool's .structured_content as Optional[dict]; megálos
    # tools always return a dict envelope, so assert-narrow at each call site.
    result = asyncio.run(
        mcp.call_tool(
            "start_workflow",
            {"workflow_type": target_name, "context": args.context},
        )
    ).structured_content
    assert result is not None
    envelope: dict = result

    # REPL-side session stack. Bottom frame is the root parent; top frame is
    # the currently-active session. Frames carry their own workflow dict,
    # skip-detection cursor (``prev_step_idx``), and the call-step id that
    # spawned them (root frame's is None).
    stack: list[dict] = [
        {
            "session_id": envelope["session_id"],
            "workflow": workflows[target_name],
            "prev_step_idx": -1,
            "call_step_id": None,
        }
    ]

    # step_id captured on the advance path, never reassigned in the re-prompt
    # branch — D039 client-side invariant. Initialized from the start_workflow
    # envelope's active step below.
    step_id = ""

    while True:
        # Dispatch order (classification-first, D039): within this loop the
        # ``propagated_from_sub_workflow`` pop-and-fall-through must be
        # evaluated against the CURRENT envelope before its ``status`` is
        # inspected — that is what lets a terminal propagation envelope
        # (``status=workflow_complete`` on the parent after the last child
        # step) still render an ascent banner before exit. Envelopes like
        # ``workflow_changed`` emitted by ``_resolve_session`` on the child
        # session do NOT carry ``propagated_from_sub_workflow``, so they
        # bypass the pop branch entirely and route straight to the terminal
        # decoder with the stack unmutated.
        #
        # A submit_step call against the top frame's last step returns a
        # PARENT advance envelope with ``propagated_from_sub_workflow: True``.
        # The server currently propagates exactly ONE level per submit_step
        # call (``_propagate_to_parent`` returns ``_advance_parent`` directly
        # at tools.py:733 and does not re-enter
        # ``_auto_resume_on_top_frame_complete`` when the parent itself hits
        # ``_COMPLETE``). The pop loop below is written against
        # ``envelope["session_id"]`` so that if server semantics ever change
        # to chain multi-level propagation in a single response the REPL
        # adapts without edits; in today's world it pops exactly one frame.
        if envelope.get("propagated_from_sub_workflow"):
            target_sid = envelope.get("session_id")
            while stack and stack[-1]["session_id"] != target_sid:
                popped = stack.pop()
                child_wf_name = popped["workflow"]["name"]
                parent_depth = len(stack) - 1
                child_indent = _indent_for(parent_depth + 1)
                print(
                    f"{child_indent}← Returned from sub-workflow "
                    f"'{child_wf_name}'"
                )
            # Do NOT continue: fall through to normal envelope classification.

        depth = len(stack) - 1
        indent = _indent_for(depth)
        top = stack[-1]
        session_id = top["session_id"]
        workflow = top["workflow"]
        steps = workflow["steps"]
        step_index = {s["id"]: i for i, s in enumerate(steps)}
        prev_step_idx = top["prev_step_idx"]

        status = envelope.get("status")

        # Classification 1 — validation_error re-prompt.
        if status == "validation_error":
            _print_validation_error(envelope, indent)
            if envelope.get("retries_exhausted"):
                print(envelope["message"], file=sys.stderr)
                sys.exit(1)
            print(
                f"Retries remaining: {envelope['retries_remaining']}",
                file=sys.stderr,
            )
            # Re-prompt: DO NOT mutate step_id, DO NOT track retry count locally.
            try:
                response = input("> ")
            except EOFError:
                print("Dry-run aborted by user (EOF)", file=sys.stderr)
                sys.exit(1)
            retry_result = asyncio.run(
                mcp.call_tool(
                    "submit_step",
                    {
                        "session_id": session_id,
                        "step_id": step_id,
                        "content": response,
                    },
                )
            ).structured_content
            assert retry_result is not None
            envelope = retry_result
            continue

        # Classification 2 — terminal.
        if status in _TERMINAL_STATUSES:
            _print_terminal(envelope, indent)
            sys.exit(0 if status == "workflow_complete" else 1)

        # Classification 3 — advance. Path-less envelope (no current_step and
        # no next_step) surfaces as KeyError: intentional bug signal.
        active = envelope.get("current_step") or envelope["next_step"]
        step_id = active["id"]

        # D043 — advance-delta skip detection. Walk the FULL range between
        # the prior cursor and the current step so multi-skip chains from
        # ``_apply_skip_loop`` surface in order. No cause assertion — the
        # earlier-rendered ``Precondition:`` line is implicit explanation.
        # Only precondition-bearing steps are flagged: branch-routing deltas
        # leave unreached siblings unannounced (they weren't "skipped",
        # they're an alternate path).
        curr_step_idx = step_index.get(step_id, prev_step_idx + 1)
        for j in range(prev_step_idx + 1, curr_step_idx):
            if "precondition" in steps[j]:
                print(f"{indent}Skipped: {steps[j]['id']}")
        top["prev_step_idx"] = curr_step_idx

        # Call-step detection — BEFORE the step banner + content prompt.
        # The operator is not supposed to type a mock LLM response for a
        # call-step; instead the REPL auto-descends (D049: auto w/ banner,
        # no prompt-to-descend).
        call_target = _detect_call_target(envelope, workflow, step_id)
        if call_target is not None:
            # Print the parent-side step banner so the operator sees which
            # step is delegating — descent banner then indents into the
            # child's nested frame.
            print(f"\n{indent}=== Step: {active['id']} — {active['title']} ===")
            descent_envelope = _descend(stack, step_id, mcp)
            if descent_envelope.get("status") == "error":
                # Error envelope from enter_sub_workflow — route to terminal
                # decode at the PARENT's indent (stack was not pushed).
                _print_terminal(descent_envelope, indent)
                sys.exit(1)
            envelope = descent_envelope
            continue

        print(f"\n{indent}=== Step: {active['id']} — {active['title']} ===")
        # D041 — precondition display at step entry. Read from the in-memory
        # workflow dict (no new tool calls, no server state peek).
        upcoming = steps[curr_step_idx] if 0 <= curr_step_idx < len(steps) else None
        if upcoming is not None and "precondition" in upcoming:
            rendered = _render_precondition(upcoming["precondition"])
            if rendered is not None:
                print(f"{indent}{rendered}")
        # `gates` is a sibling key on the envelope (see tools.py result-shape),
        # not a field on current_step/next_step. Render only when non-empty.
        gates = envelope.get("gates") or []
        if gates:
            print(f"{indent}Gates:")
            for g in gates:
                print(f"{indent}  - {g}")
        directive = envelope["directive"]
        print(directive)
        print()
        try:
            response = input("> ")
        except EOFError:
            print("Dry-run aborted by user (EOF)", file=sys.stderr)
            sys.exit(1)

        # D042 — branch selection. Prompt AFTER content, to match the
        # production ``submit_step(content, branch)`` arg pairing. Source of
        # truth for "does this step branch?" is the workflow dict: the server
        # emits the ``branches`` envelope key on advance results but NOT on
        # start_workflow (see tools.py ~879), so first-step branching has to
        # fall back to the in-memory YAML projection.
        submit_args: dict = {
            "session_id": session_id,
            "step_id": step_id,
            "content": response,
        }
        branches = envelope.get("branches")
        default_branch = envelope.get("default_branch", "")
        if not branches and upcoming is not None and upcoming.get("branches"):
            branches = upcoming["branches"]
            default_branch = upcoming.get("default_branch", "")
        if branches:
            # Call with positional indent only when non-empty, to preserve
            # binary compatibility with S03 tests that monkeypatch
            # ``_prompt_branch`` with a 2-arg signature. At depth 0 (root)
            # the indent is empty and the call stays 2-positional.
            if indent:
                submit_args["branch"] = _prompt_branch(
                    branches, default_branch, indent
                )
            else:
                submit_args["branch"] = _prompt_branch(branches, default_branch)

        advance_result = asyncio.run(
            mcp.call_tool("submit_step", submit_args)
        ).structured_content
        assert advance_result is not None
        envelope = advance_result


if __name__ == "__main__":
    main()
