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


def _print_validation_error(envelope: dict) -> None:
    """Print a validation_error envelope to stderr; no exit decision here."""
    print("Validation failed:", file=sys.stderr)
    for err in envelope["errors"]:
        print(f"  - {err}", file=sys.stderr)
    hint = envelope.get("validation_hint")
    if hint:
        print(f"Hint: {hint}", file=sys.stderr)


def _print_terminal(envelope: dict) -> None:
    """Print the operator-facing payload of a terminal envelope.

    Decodes two error codes into human-readable stderr lines (D044):
    ``invalid_argument`` on ``field=branch`` and
    ``skipped_predecessor_reference``. Both stay terminal; no re-prompt.
    Other error codes fall through to generic message/code/error surfacing.
    """
    status = envelope.get("status")
    if status == "workflow_complete":
        print("\n=== Workflow complete ===")
        print(envelope.get("message", ""))
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


def _prompt_branch(branches: list, default: str) -> str:
    """Prompt the operator for a branch choice; return the resolved step_id.

    Renders a numbered list with a `[default]` tag on the default row, then
    loops locally on invalid numeric input (no server round-trip). Empty
    input resolves to ``default``.
    """
    print("Branches:")
    for i, b in enumerate(branches, start=1):
        tag = " [default]" if b["next"] == default else ""
        print(f"  {i}. {b['next']} — {b['condition']}{tag}")
    n = len(branches)
    while True:
        try:
            raw = input(f"Choose branch [1-{n}, empty = default]: ")
        except EOFError:
            print("Dry-run aborted by user (EOF)", file=sys.stderr)
            sys.exit(1)
        raw = raw.strip()
        if raw == "":
            print(f"→ {default} (default)")
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
            print(f"→ {target}")
            return target
        print(
            f"Invalid branch selection '{raw}'. Enter 1-{n} or empty.",
            file=sys.stderr,
        )


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

    # Cache the workflow's step list for precondition lookup + skip-detection
    # cursor math. Server-side state is the source of truth; this is a local
    # read-only projection of the YAML the server already loaded.
    steps = workflows[target_name]["steps"]
    step_index = {s["id"]: i for i, s in enumerate(steps)}

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
    session_id = envelope["session_id"]
    # step_id captured on the advance path, never reassigned in the re-prompt
    # branch — D039 client-side invariant. Initialized from the start_workflow
    # envelope's active step below.
    step_id = ""
    # Skip-detection cursor over the workflow's top-level step list (D043).
    # -1 before the first advance; each advance walks the full range between
    # the previous cursor and the current step to emit `Skipped: <id>` for
    # every step elided by ``_apply_skip_loop`` server-side.
    prev_step_idx = -1

    while True:
        status = envelope.get("status")

        # Classification 1 — validation_error re-prompt.
        if status == "validation_error":
            _print_validation_error(envelope)
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
            _print_terminal(envelope)
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
                print(f"Skipped: {steps[j]['id']}")
        prev_step_idx = curr_step_idx

        print(f"\n=== Step: {active['id']} — {active['title']} ===")
        # D041 — precondition display at step entry. Read from the in-memory
        # workflow dict (no new tool calls, no server state peek).
        upcoming = steps[curr_step_idx] if 0 <= curr_step_idx < len(steps) else None
        if upcoming is not None and "precondition" in upcoming:
            rendered = _render_precondition(upcoming["precondition"])
            if rendered is not None:
                print(rendered)
        # `gates` is a sibling key on the envelope (see tools.py result-shape),
        # not a field on current_step/next_step. Render only when non-empty.
        gates = envelope.get("gates") or []
        if gates:
            print("Gates:")
            for g in gates:
                print(f"  - {g}")
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
            submit_args["branch"] = _prompt_branch(branches, default_branch)

        advance_result = asyncio.run(
            mcp.call_tool("submit_step", submit_args)
        ).structured_content
        assert advance_result is not None
        envelope = advance_result


if __name__ == "__main__":
    main()
