"""Subprocess-driven tests for the dry-run CLI bootstrap entry point.

Each test spawns ``python -m megalos_server.dryrun`` as a subprocess so
the __main__ guard and env-var ordering discipline are exercised in the
same shape as production invocation. One in-process test
(``test_loop_invariant_same_step_id_for_retries``) monkeypatches the
MCP ``call_tool`` surface to pin the D039 client-side loop invariant.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "workflows"
CANONICAL_FIXTURE = FIXTURES_DIR / "canonical.yaml"
DEMO_VALIDATION_FIXTURE = FIXTURES_DIR / "demo_validation.yaml"
DEMO_BRANCHING_FIXTURE = FIXTURES_DIR / "demo_branching.yaml"
PRECONDITION_BRANCHES_FIXTURE = FIXTURES_DIR / "precondition_with_branches.yaml"
ARTIFACT_INLINING_PARENT_FIXTURE = FIXTURES_DIR / "artifact_inlining_parent.yaml"
ARTIFACT_INLINING_CHILD_FIXTURE = FIXTURES_DIR / "artifact_inlining_child.yaml"

RESPONSES_DIR = Path(__file__).parent / "responses"
CANONICAL_GOLDEN = RESPONSES_DIR / "canonical_golden.yaml"
DEMO_VALIDATION_GOLDEN = RESPONSES_DIR / "demo_validation_golden.yaml"
DEMO_BRANCHING_GOLDEN = RESPONSES_DIR / "demo_branching_golden.yaml"
ARTIFACT_INLINING_GOLDEN = RESPONSES_DIR / "artifact_inlining_golden.yaml"

# Schema-failing payload (missing `confirmed`, fewer than 3 goals, short title).
_INVALID_JSON = json.dumps({"title": "xy", "goals": ["only one"]})
# Schema-valid payload for `collect_info` step of demo_validation.yaml.
_VALID_JSON = json.dumps(
    {"title": "Project X", "goals": ["a", "b", "c"], "confirmed": True}
)
_VALIDATION_HINT = (
    "Submit JSON with title (string, 3+ chars), goals (array of 3+ strings), "
    "and confirmed (must be boolean true)."
)


def _run(
    args: list[str], input: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "megalos_server.dryrun", *args],
        capture_output=True,
        text=True,
        input=input,
    )


def test_help_exits_zero() -> None:
    result = _run(["--help"])
    assert result.returncode == 0
    assert "--help" in result.stdout


def test_nonexistent_path_errors_cleanly(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"
    result = _run([str(missing)])
    assert result.returncode == 1
    assert "Workflow file not found" in result.stderr


def test_no_sessions_db_writes(tmp_path: Path) -> None:
    # canonical.yaml has 3 steps; see tests/fixtures/workflows/canonical.yaml
    target = tmp_path / "canonical.yaml"
    shutil.copy(CANONICAL_FIXTURE, target)
    sessions_db = Path("server/megalos_sessions.db")
    pre_exists = sessions_db.exists()
    pre_stat = sessions_db.stat() if pre_exists else None
    result = _run([str(target)], input="ok\n" * 3)
    assert result.returncode == 0, result.stderr
    if pre_exists:
        assert sessions_db.exists()
        post_stat = sessions_db.stat()
        assert pre_stat is not None
        assert post_stat.st_mtime == pre_stat.st_mtime
        assert post_stat.st_size == pre_stat.st_size
    else:
        assert not sessions_db.exists()


def test_broken_sibling_produces_framed_error(tmp_path: Path) -> None:
    target = tmp_path / "canonical.yaml"
    shutil.copy(CANONICAL_FIXTURE, target)
    broken = tmp_path / "broken.yaml"
    # Valid YAML, invalid schema: call-target cross-check fails. The
    # cross-check error embeds the workflow name ('broken') so the raw
    # exception passes through a sibling-identifying string, which the
    # Approach E framing paragraph hands to the user unmodified.
    broken.write_text(
        "name: broken\n"
        "description: Sibling workflow with invalid schema.\n"
        "category: test\n"
        "output_format: structured_code\n"
        "steps:\n"
        "  - id: s1\n"
        "    title: S1\n"
        "    call: nonexistent_workflow\n",
        encoding="utf-8",
    )
    result = _run([str(target)])
    assert result.returncode == 1
    assert "dry-run loads all *.yaml files" in result.stderr
    # Raw exception passes through and identifies the broken workflow by name.
    assert "broken" in result.stderr


def test_broken_target_produces_framed_error(tmp_path: Path) -> None:
    target = tmp_path / "bad_target.yaml"
    # Valid YAML, invalid schema: call-target cross-check fails. The
    # cross-check error embeds the workflow name ('bad_target') which
    # passes through the Approach E framing so the user can identify
    # the failing workflow.
    target.write_text(
        "name: bad_target\n"
        "description: Target workflow with invalid schema.\n"
        "category: test\n"
        "output_format: structured_code\n"
        "steps:\n"
        "  - id: s1\n"
        "    title: S1\n"
        "    call: nonexistent_workflow\n",
        encoding="utf-8",
    )
    result = _run([str(target)])
    assert result.returncode != 0
    assert "dry-run loads all *.yaml files" in result.stderr
    # Target path in framing + workflow name in raw exception.
    assert str(target.parent) in result.stderr
    assert "bad_target" in result.stderr


def test_canonical_fixture_runs_end_to_end(tmp_path: Path) -> None:
    target = tmp_path / "canonical.yaml"
    shutil.copy(CANONICAL_FIXTURE, target)
    # canonical.yaml has 3 steps; see tests/fixtures/workflows/canonical.yaml
    result = _run([str(target)], input="ok\nok\nok\n")
    assert result.returncode == 0, result.stderr
    assert "alpha" in result.stdout
    assert "bravo" in result.stdout
    assert "charlie" in result.stdout
    assert "Workflow complete" in result.stdout


def test_stdin_eof_exits_nonzero(tmp_path: Path) -> None:
    target = tmp_path / "canonical.yaml"
    shutil.copy(CANONICAL_FIXTURE, target)
    result = _run([str(target)], input="")
    assert result.returncode != 0
    assert "Dry-run aborted by user (EOF)" in result.stderr


# ---- S02: validation_error re-prompt + gates rendering ---------------------


def test_validation_retry_loop_advances_on_valid(tmp_path: Path) -> None:
    target = tmp_path / "demo_validation.yaml"
    shutil.copy(DEMO_VALIDATION_FIXTURE, target)
    # stdin: one schema-failing, then valid (advance), then any line for step 2.
    stdin = f"{_INVALID_JSON}\n{_VALID_JSON}\nsummary line\n"
    result = _run([str(target)], input=stdin)
    assert result.returncode == 0, result.stderr
    # Both step banners rendered on stdout.
    assert "collect_info" in result.stdout
    assert "summarize" in result.stdout
    # Validation-error surface on stderr.
    assert "Validation failed:" in result.stderr
    assert "Retries remaining: 2" in result.stderr
    assert _VALIDATION_HINT in result.stderr


def test_validation_budget_exhaustion_exits_nonzero(tmp_path: Path) -> None:
    target = tmp_path / "demo_validation.yaml"
    shutil.copy(DEMO_VALIDATION_FIXTURE, target)
    # Three schema-failing submissions — max_retries=3 exhausts.
    stdin = f"{_INVALID_JSON}\n{_INVALID_JSON}\n{_INVALID_JSON}\n"
    result = _run([str(target)], input=stdin)
    assert result.returncode == 1
    assert "Max retries (3) exceeded" in result.stderr
    # Step 2 banner must NOT appear — no advance on exhaustion.
    assert "summarize" not in result.stdout


def test_gates_rendered_at_step_entry(tmp_path: Path) -> None:
    target = tmp_path / "demo_validation.yaml"
    shutil.copy(DEMO_VALIDATION_FIXTURE, target)
    stdin = f"{_VALID_JSON}\nsummary line\n"
    result = _run([str(target)], input=stdin)
    assert result.returncode == 0, result.stderr
    assert "Gates:" in result.stdout
    # Step 1 gates (3 bullets).
    assert "- User provided project title" in result.stdout
    assert "- User listed at least three goals" in result.stdout
    assert "- User confirmed the information" in result.stdout
    # Step 2 gate (1 bullet).
    assert "- Summary is clear and concise" in result.stdout


def test_validation_hint_rendered_verbatim(tmp_path: Path) -> None:
    target = tmp_path / "demo_validation.yaml"
    shutil.copy(DEMO_VALIDATION_FIXTURE, target)
    stdin = f"{_INVALID_JSON}\n{_VALID_JSON}\nsummary line\n"
    result = _run([str(target)], input=stdin)
    assert result.returncode == 0, result.stderr
    # Verbatim — no paraphrase, no ellipsis.
    assert _VALIDATION_HINT in result.stderr


def test_loop_invariant_same_step_id_for_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """D039 client-side invariant: on validation_error, re-prompt submits the
    SAME ``step_id``; no local retry mutation. Option (a): record every
    ``call_tool`` invocation and assert the submit_step sequence pins
    step_id=collect_info exactly N+1 times (1 retry -> 2 submits) before any
    submit_step with step_id=summarize.
    """
    target = tmp_path / "demo_validation.yaml"
    shutil.copy(DEMO_VALIDATION_FIXTURE, target)

    # In-process import — subprocess boundary can't monkeypatch the MCP layer.
    # Env-var ordering matches production: the module sets MEGALOS_DB_PATH
    # at top level, before any megalos_server imports.
    import importlib

    import megalos_server.dryrun as dryrun_mod

    importlib.reload(dryrun_mod)

    from megalos_server import create_app as real_create_app

    calls: list[tuple[str, dict[str, Any]]] = []

    def recording_create_app(*args: Any, **kwargs: Any) -> Any:
        mcp = real_create_app(*args, **kwargs)
        real_call_tool = mcp.call_tool
        # FastMCP re-invokes call_tool recursively through middleware. Use a
        # reentrancy counter so we record only the operator-initiated (outermost)
        # invocation, not the middleware pass-through.
        depth = {"n": 0}

        async def wrapped_call_tool(
            name: str, arguments: dict[str, Any], *args: Any, **kwargs: Any
        ) -> Any:
            if depth["n"] == 0:
                calls.append((name, dict(arguments)))
            depth["n"] += 1
            try:
                return await real_call_tool(name, arguments, *args, **kwargs)
            finally:
                depth["n"] -= 1

        mcp.call_tool = wrapped_call_tool  # type: ignore[method-assign]
        return mcp

    monkeypatch.setattr(dryrun_mod, "create_app", recording_create_app)
    monkeypatch.setattr(sys, "argv", ["megalos-dryrun", str(target)])

    # Feed stdin: one invalid (triggers validation_error), then one valid
    # (advance), then line for step 2.
    stdin_lines = iter([_INVALID_JSON, _VALID_JSON, "summary"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(stdin_lines))

    with pytest.raises(SystemExit) as exc_info:
        dryrun_mod.main()
    assert exc_info.value.code == 0

    submit_calls = [args for (name, args) in calls if name == "submit_step"]
    # 2 submissions on collect_info (1 retry + 1 valid), then 1 on summarize.
    collect_submits = [c for c in submit_calls if c["step_id"] == "collect_info"]
    summarize_submits = [c for c in submit_calls if c["step_id"] == "summarize"]
    assert len(collect_submits) == 2, submit_calls
    assert len(summarize_submits) == 1, submit_calls
    # Ordering: all collect_info submits precede any summarize submit.
    first_summarize_idx = next(
        i for i, c in enumerate(submit_calls) if c["step_id"] == "summarize"
    )
    assert all(
        c["step_id"] == "collect_info" for c in submit_calls[:first_summarize_idx]
    )


# ---- S03: branches + preconditions + skip detection + error decode ---------


def test_fixtures_load_cleanly(tmp_path: Path) -> None:
    """Pre-flight: both branching fixtures load via create_app no schema errors.

    Guards fixture drift that would silently break downstream S03 tests.
    """
    shutil.copy(DEMO_BRANCHING_FIXTURE, tmp_path / "demo_branching.yaml")
    shutil.copy(PRECONDITION_BRANCHES_FIXTURE, tmp_path / "precondition_with_branches.yaml")
    # Round-trip through subprocess --help path would load fixtures only if
    # we pointed at one; simpler to import create_app here (test-only, not in
    # dryrun.py's allowlist scope).
    from megalos_server import create_app
    mcp = create_app(workflow_dir=str(tmp_path))
    workflows = mcp._megalos_workflows  # type: ignore[attr-defined]
    assert "demo_branching" in workflows
    assert "precondition_with_branches" in workflows


def test_branch_default_selection_reaches_default_target(tmp_path: Path) -> None:
    target = tmp_path / "demo_branching.yaml"
    shutil.copy(DEMO_BRANCHING_FIXTURE, target)
    # demo_branching linearly falls through all post-branch steps — the
    # branch chooses the entry point, then the remaining track-steps run
    # in file order. Feed stdin for every reached step.
    stdin = "mock response\n\nmock intermediate\nmock advanced\n"
    result = _run([str(target)], input=stdin)
    assert result.returncode == 0, result.stderr
    assert "Branches:" in result.stdout
    # All three branch rows render with the target id visible.
    assert "beginner_track" in result.stdout
    assert "intermediate_track" in result.stdout
    assert "advanced_track" in result.stdout
    # Default tag appears on the intermediate row.
    assert "[default]" in result.stdout
    # Echo-back line identifies intermediate as the default resolution.
    assert "→ intermediate_track (default)" in result.stdout
    # Reached the intermediate step banner.
    assert "Intermediate Track" in result.stdout
    assert "Workflow complete" in result.stdout


def test_branch_numeric_selection_reaches_chosen_target(tmp_path: Path) -> None:
    target = tmp_path / "demo_branching.yaml"
    shutil.copy(DEMO_BRANCHING_FIXTURE, target)
    # Choose branch 3 (advanced_track). Workflow ends after advanced_track
    # since it's the file's last step.
    stdin = "mock response\n3\nmock advanced\n"
    result = _run([str(target)], input=stdin)
    assert result.returncode == 0, result.stderr
    # Non-default echo-back: no "(default)" suffix.
    assert "→ advanced_track" in result.stdout
    assert "→ advanced_track (default)" not in result.stdout
    assert "Advanced Track" in result.stdout
    assert "Workflow complete" in result.stdout


def test_branch_invalid_numeric_reprompts_locally(tmp_path: Path) -> None:
    target = tmp_path / "demo_branching.yaml"
    shutil.copy(DEMO_BRANCHING_FIXTURE, target)
    # step 1 content, invalid (99), then valid (3), advanced_track content.
    stdin = "mock response\n99\n3\nmock advanced\n"
    result = _run([str(target)], input=stdin)
    assert result.returncode == 0, result.stderr
    # Local re-prompt on stderr — no server round-trip.
    assert "Invalid branch selection '99'" in result.stderr
    # Echo-back after the successful retry.
    assert "→ advanced_track" in result.stdout


def test_precondition_rendered_at_step_entry(tmp_path: Path) -> None:
    target = tmp_path / "precondition_with_branches.yaml"
    shutil.copy(PRECONDITION_BRANCHES_FIXTURE, target)
    # step_1 yes -> step_2 runs (precondition matches), choose branch 1
    # (step_3a), step_3a + step_3b content (fixture falls through linearly
    # after branch entry).
    stdin = '{"go": "yes"}\nroute\n1\npath A\npath B\n'
    result = _run([str(target)], input=stdin)
    assert result.returncode == 0, result.stderr
    # Precondition render at step_2 entry, verbatim predicate.
    assert 'Precondition: step_data.step_1.go == "yes"' in result.stdout
    # step_3a reached (Path A banner).
    assert "Path A" in result.stdout


def test_skip_detection_single_step(tmp_path: Path) -> None:
    target = tmp_path / "precondition_with_branches.yaml"
    shutil.copy(PRECONDITION_BRANCHES_FIXTURE, target)
    # step_1 no -> step_2 skipped, linear fallback lands on step_3a,
    # then falls through to step_3b.
    stdin = '{"go": "no"}\npath A\npath B\n'
    result = _run([str(target)], input=stdin)
    assert result.returncode == 0, result.stderr
    # Skip line exists, no cause suffix.
    assert "Skipped: step_2" in result.stdout
    assert "Skipped: step_2 (precondition" not in result.stdout
    # step_3a banner reached.
    assert "Path A" in result.stdout


# Multi-step skip fixture (3-step precondition chain) — exercises the
# walk-the-full-range invariant against a single-step-lookahead regression.
_MULTI_SKIP_FIXTURE = """\
schema_version: "0.3"
name: multi_skip
description: Multi-step skip chain fixture.
category: analysis_decision
output_format: text

steps:
  - id: step_1
    title: Emit go
    directive_template: Ask whether to proceed.
    gates:
      - go captured
    anti_patterns:
      - Guessing
    collect: true
    output_schema:
      type: object
      required: [go]
      properties:
        go:
          type: string
          minLength: 2

  - id: step_2
    title: Gated Step 2
    directive_template: Only runs when go=yes.
    gates:
      - done
    anti_patterns:
      - Skipping
    precondition:
      when_equals:
        ref: step_data.step_1.go
        value: "yes"

  - id: step_3
    title: Gated Step 3
    directive_template: Only runs when go=yes.
    gates:
      - done
    anti_patterns:
      - Skipping
    precondition:
      when_equals:
        ref: step_data.step_1.go
        value: "yes"

  - id: step_4
    title: Always
    directive_template: Always runs.
    gates:
      - done
    anti_patterns:
      - Skipping
"""


def test_skip_detection_multi_step_chain(tmp_path: Path) -> None:
    target = tmp_path / "multi_skip.yaml"
    target.write_text(_MULTI_SKIP_FIXTURE, encoding="utf-8")
    # go=no -> step_2 + step_3 both skipped, land on step_4.
    stdin = '{"go": "no"}\nfinal\n'
    result = _run([str(target)], input=stdin)
    assert result.returncode == 0, result.stderr
    # Both skipped step ids emitted; ordering preserved (walk-full-range).
    idx_s2 = result.stdout.find("Skipped: step_2")
    idx_s3 = result.stdout.find("Skipped: step_3")
    assert idx_s2 != -1, result.stdout
    assert idx_s3 != -1, result.stdout
    assert idx_s2 < idx_s3, result.stdout
    # step_4 banner reached.
    assert "Always" in result.stdout


def test_invalid_branch_server_rejection_exits_with_decoded_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-process shim: overwrite the REPL's computed ``branch`` with a bogus
    string between prompt and ``call_tool``, so the server emits the real
    ``invalid_argument``+``field=branch`` envelope. REPL must decode it.
    """
    target = tmp_path / "demo_branching.yaml"
    shutil.copy(DEMO_BRANCHING_FIXTURE, target)

    import importlib

    import megalos_server.dryrun as dryrun_mod

    importlib.reload(dryrun_mod)

    from megalos_server import create_app as real_create_app

    def bogus_prompt_branch(branches: list, default: str) -> str:
        return "not_a_real_step_id"

    monkeypatch.setattr(dryrun_mod, "_prompt_branch", bogus_prompt_branch)
    monkeypatch.setattr(dryrun_mod, "create_app", real_create_app)
    monkeypatch.setattr(sys, "argv", ["megalos-dryrun", str(target)])

    stdin_lines = iter(["mock response"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(stdin_lines))

    # Capture stderr/stdout to assert decoded message.
    import io

    captured_out = io.StringIO()
    captured_err = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured_out)
    monkeypatch.setattr(sys, "stderr", captured_err)

    with pytest.raises(SystemExit) as exc_info:
        dryrun_mod.main()
    assert exc_info.value.code == 1, captured_err.getvalue()
    assert "Invalid branch 'not_a_real_step_id'" in captured_err.getvalue()
    assert "Valid options:" in captured_err.getvalue()


def test_force_branched_field_ignored_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-process shim injects ``force_branched: true`` into a normal advance
    envelope. Guardrail-driven fields must be ignored by skip-detection +
    branch-prompt logic — advance still proceeds normally.

    Uses canonical.yaml (no branches) so the envelope flow is the simplest
    possible, and an unknown top-level field can't trigger any of the S03
    branches; the assertion is that the REPL doesn't crash.
    """
    target = tmp_path / "canonical.yaml"
    shutil.copy(CANONICAL_FIXTURE, target)

    import importlib

    import megalos_server.dryrun as dryrun_mod

    importlib.reload(dryrun_mod)

    from megalos_server import create_app as real_create_app

    def injecting_create_app(*args: Any, **kwargs: Any) -> Any:
        mcp = real_create_app(*args, **kwargs)
        real_call_tool = mcp.call_tool
        depth = {"n": 0}

        async def wrapped(
            name: str, arguments: dict[str, Any], *a: Any, **kw: Any
        ) -> Any:
            result = await real_call_tool(name, arguments, *a, **kw)
            if depth["n"] == 0 and result.structured_content is not None:
                # Inject unknown-at-top-level field on the outbound envelope.
                result.structured_content["force_branched"] = True
            depth["n"] += 1
            try:
                return result
            finally:
                depth["n"] -= 1

        mcp.call_tool = wrapped  # type: ignore[method-assign]
        return mcp

    monkeypatch.setattr(dryrun_mod, "create_app", injecting_create_app)
    monkeypatch.setattr(sys, "argv", ["megalos-dryrun", str(target)])
    stdin_lines = iter(["ok", "ok", "ok"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(stdin_lines))

    import io

    captured_out = io.StringIO()
    captured_err = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured_out)
    monkeypatch.setattr(sys, "stderr", captured_err)

    with pytest.raises(SystemExit) as exc_info:
        dryrun_mod.main()
    # Canonical workflow runs clean despite the injected field.
    assert exc_info.value.code == 0, captured_err.getvalue()
    assert "Workflow complete" in captured_out.getvalue()


# ---- S04: sub-workflow descent + ascent + error decoding -------------------

ARTIFACT_INLINING_PARENT_FIXTURE = FIXTURES_DIR / "artifact_inlining_parent.yaml"
ARTIFACT_INLINING_CHILD_FIXTURE = FIXTURES_DIR / "artifact_inlining_child.yaml"
CALL_CONTEXT_FROM_PARENT_FIXTURE = FIXTURES_DIR / "call_context_from_parent.yaml"
CALL_CONTEXT_FROM_CHILD_FIXTURE = FIXTURES_DIR / "call_context_from_child.yaml"


def _copy_artifact_inlining(tmp_path: Path) -> Path:
    """Copy parent+child into tmp_path, return parent path."""
    parent = tmp_path / "artifact_inlining_parent.yaml"
    shutil.copy(ARTIFACT_INLINING_PARENT_FIXTURE, parent)
    shutil.copy(
        ARTIFACT_INLINING_CHILD_FIXTURE, tmp_path / "artifact_inlining_child.yaml"
    )
    return parent


def test_artifact_inlining_happy_path(tmp_path: Path) -> None:
    """End-to-end descent + ascent flow on the canonical fixture.

    Parent has 2 steps (intro + research=call). Child has 2 steps
    (gather + brief). stdin feeds intro content, then both child-step
    contents; REPL auto-descends on research and auto-ascends on brief
    completion. Expect descent/ascent banners, 2-space indent on child
    banners, exit 0.
    """
    target = _copy_artifact_inlining(tmp_path)
    stdin = "intro paragraph\ngather sources\nfinal brief\n"
    result = _run([str(target)], input=stdin)
    assert result.returncode == 0, result.stderr
    # Descent banner at child depth (2-space indent since child is at depth 1).
    assert "  → Entering sub-workflow 'artifact_inlining_child'" in result.stdout
    # Child step banners indented 2 spaces.
    assert "  === Step: gather" in result.stdout
    assert "  === Step: brief" in result.stdout
    # Child gates indented.
    assert "  Gates:" in result.stdout
    assert "    - sources listed" in result.stdout
    # Ascent banner.
    assert "  ← Returned from sub-workflow 'artifact_inlining_child'" in result.stdout
    assert "Workflow complete" in result.stdout


def test_first_step_call_fallback(tmp_path: Path) -> None:
    """D048 first-step fallback: parent whose FIRST step is a call-step.

    start_workflow does NOT populate envelope.current_step.call_target
    (tools.py:883-901). REPL must fall back to the workflow dict to
    detect the call. Authoring a parent with only a call-step (no intro)
    exercises this path.
    """
    # Copy the child alongside our tmp parent for call-target resolution.
    shutil.copy(
        ARTIFACT_INLINING_CHILD_FIXTURE, tmp_path / "artifact_inlining_child.yaml"
    )
    parent = tmp_path / "first_call.yaml"
    parent.write_text(
        "schema_version: \"0.3\"\n"
        "name: first_call\n"
        "description: Parent whose first step is a call.\n"
        "category: analysis_decision\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: delegate\n"
        "    title: Delegate immediately\n"
        "    directive_template: Hand off to child on first step.\n"
        "    gates:\n"
        "      - handoff performed\n"
        "    anti_patterns:\n"
        "      - Skipping the handoff\n"
        "    call: artifact_inlining_child\n",
        encoding="utf-8",
    )
    # Only child steps consume stdin: gather + brief.
    stdin = "gather content\nfinal brief\n"
    result = _run([str(parent)], input=stdin)
    assert result.returncode == 0, result.stderr
    assert "→ Entering sub-workflow 'artifact_inlining_child'" in result.stdout
    assert "← Returned from sub-workflow 'artifact_inlining_child'" in result.stdout
    assert "Workflow complete" in result.stdout


def test_call_context_from_non_string_propagates_as_json_string(tmp_path: Path) -> None:
    """D048: call_context_from non-string values arrive at child as
    ``json.dumps`` output (tools.py:1591). REPL's Context banner prints
    that string verbatim — NOT a pretty-printed or parsed form.
    """
    # Author a parent whose first step emits an output_schema-validated
    # dict; the call-step's call_context_from resolves to that dict.
    # The child workflow must exist for call-target resolution.
    (tmp_path / "ctx_child.yaml").write_text(
        "schema_version: \"0.3\"\n"
        "name: ctx_child\n"
        "description: Child receiving structured context.\n"
        "category: analysis_decision\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: acknowledge\n"
        "    title: Acknowledge context\n"
        "    directive_template: Acknowledge the provided context.\n"
        "    gates:\n"
        "      - context acknowledged\n"
        "    anti_patterns:\n"
        "      - Ignoring context\n",
        encoding="utf-8",
    )
    parent = tmp_path / "ctx_parent.yaml"
    parent.write_text(
        "schema_version: \"0.3\"\n"
        "name: ctx_parent\n"
        "description: Parent emitting structured context dict for child.\n"
        "category: analysis_decision\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: collect\n"
        "    title: Collect structured data\n"
        "    directive_template: Submit structured JSON.\n"
        "    gates:\n"
        "      - data collected\n"
        "    anti_patterns:\n"
        "      - Empty payload\n"
        "    collect: true\n"
        "    output_schema:\n"
        "      type: object\n"
        "      required: [topic, audience]\n"
        "      properties:\n"
        "        topic:\n"
        "          type: string\n"
        "          minLength: 2\n"
        "        audience:\n"
        "          type: string\n"
        "          minLength: 2\n"
        "  - id: delegate\n"
        "    title: Delegate with structured context\n"
        "    directive_template: Hand off the structured payload.\n"
        "    gates:\n"
        "      - handoff performed\n"
        "    anti_patterns:\n"
        "      - Flattening the payload\n"
        "    call: ctx_child\n"
        "    call_context_from: step_data.collect\n",
        encoding="utf-8",
    )
    structured_payload = json.dumps({"topic": "agents", "audience": "engineers"})
    # stdin: structured JSON for collect, ack line for child acknowledge.
    stdin = f"{structured_payload}\nack\n"
    result = _run([str(parent)], input=stdin)
    assert result.returncode == 0, result.stderr
    # The server json.dumps the extracted dict for the child context. REPL
    # prints it verbatim. Order of keys in json.dumps is insertion-order,
    # which for a parsed JSON object is source-order.
    # Either "topic" first or "audience" first — both are valid json.dumps
    # output depending on parse order. Assert the substring contains both
    # keys inline in the Context banner.
    ctx_lines = [
        line for line in result.stdout.splitlines() if line.strip().startswith("Context:")
    ]
    assert ctx_lines, result.stdout
    ctx_line = ctx_lines[0]
    assert "topic" in ctx_line
    assert "audience" in ctx_line
    # Verbatim JSON-dumped: the dict renders as a JSON object literal with
    # ``{`` + ``}`` delimiters, not Python's repr.
    assert "{" in ctx_line and "}" in ctx_line


def test_workflow_not_loaded_decoded(tmp_path: Path) -> None:
    """Parent references a child that isn't loaded. enter_sub_workflow
    emits ``workflow_not_loaded``; REPL decodes it with Available: list.
    """
    # Parent only — child workflow is NOT in tmp_path.
    parent = tmp_path / "missing_call.yaml"
    parent.write_text(
        "schema_version: \"0.3\"\n"
        "name: missing_call\n"
        "description: Parent calling an unloaded child.\n"
        "category: analysis_decision\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: delegate\n"
        "    title: Delegate to ghost\n"
        "    directive_template: Hand off to a child that is not loaded.\n"
        "    gates:\n"
        "      - handoff attempted\n"
        "    anti_patterns:\n"
        "      - Proceeding anyway\n"
        "    call: absent_child_workflow\n",
        encoding="utf-8",
    )
    result = _run([str(parent)], input="")
    # NOTE: dry-run's own workflow loader cross-checks call targets; the
    # failure surfaces at create_app time (Approach E framing), not at
    # enter_sub_workflow. Test for either rendering.
    assert result.returncode != 0
    stderr = result.stderr
    assert (
        "Sub-workflow 'absent_child_workflow' not loaded" in stderr
        or "absent_child_workflow" in stderr
    )


def test_workflow_not_loaded_decoded_via_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Complement to test_workflow_not_loaded_decoded: the standalone
    subprocess test hits create_app's call-target cross-check before
    enter_sub_workflow is invoked (Approach E framing). This in-process
    variant injects the server's ``workflow_not_loaded`` envelope directly
    on the enter_sub_workflow path to verify the REPL decoder.
    """
    target = _copy_artifact_inlining(tmp_path)

    import importlib

    import megalos_server.dryrun as dryrun_mod

    importlib.reload(dryrun_mod)

    from megalos_server import create_app as real_create_app

    def injecting_create_app(*args: Any, **kwargs: Any) -> Any:
        mcp = real_create_app(*args, **kwargs)
        real_call_tool = mcp.call_tool
        depth = {"n": 0}

        async def wrapped(
            name: str, arguments: dict[str, Any], *a: Any, **kw: Any
        ) -> Any:
            if depth["n"] == 0 and name == "enter_sub_workflow":
                class _R:
                    structured_content = {
                        "status": "error",
                        "code": "workflow_not_loaded",
                        "error": "target workflow 'ghost_child' not loaded",
                        "available_types": ["artifact_inlining_parent", "artifact_inlining_child"],
                    }

                return _R()
            depth["n"] += 1
            try:
                return await real_call_tool(name, arguments, *a, **kw)
            finally:
                depth["n"] -= 1

        mcp.call_tool = wrapped  # type: ignore[method-assign]
        return mcp

    monkeypatch.setattr(dryrun_mod, "create_app", injecting_create_app)
    monkeypatch.setattr(sys, "argv", ["megalos-dryrun", str(target)])
    stdin_lines = iter(["intro content"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(stdin_lines))

    import io

    captured_err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", captured_err)

    with pytest.raises(SystemExit) as exc_info:
        dryrun_mod.main()
    assert exc_info.value.code == 1, captured_err.getvalue()
    err = captured_err.getvalue()
    assert "Sub-workflow 'ghost_child' not loaded" in err
    assert "Available: artifact_inlining_parent, artifact_inlining_child" in err


def test_out_of_order_submission_decoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-process test: inject an ``out_of_order_submission`` envelope into
    the REPL's call_tool path on enter_sub_workflow. REPL must decode.
    """
    target = _copy_artifact_inlining(tmp_path)

    import importlib

    import megalos_server.dryrun as dryrun_mod

    importlib.reload(dryrun_mod)

    from megalos_server import create_app as real_create_app

    def injecting_create_app(*args: Any, **kwargs: Any) -> Any:
        mcp = real_create_app(*args, **kwargs)
        real_call_tool = mcp.call_tool
        depth = {"n": 0}

        async def wrapped(
            name: str, arguments: dict[str, Any], *a: Any, **kw: Any
        ) -> Any:
            if depth["n"] == 0 and name == "enter_sub_workflow":
                # Synthesize an error envelope that matches the server's shape.
                class _R:
                    structured_content = {
                        "status": "error",
                        "code": "out_of_order_submission",
                        "error": "parent current_step is 'research', not 'wrong_step'",
                        "expected_step": "research",
                        "submitted_step": "wrong_step",
                    }

                return _R()
            depth["n"] += 1
            try:
                return await real_call_tool(name, arguments, *a, **kw)
            finally:
                depth["n"] -= 1

        mcp.call_tool = wrapped  # type: ignore[method-assign]
        return mcp

    monkeypatch.setattr(dryrun_mod, "create_app", injecting_create_app)
    monkeypatch.setattr(sys, "argv", ["megalos-dryrun", str(target)])
    stdin_lines = iter(["intro content"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(stdin_lines))

    import io

    captured_err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", captured_err)

    with pytest.raises(SystemExit) as exc_info:
        dryrun_mod.main()
    assert exc_info.value.code == 1, captured_err.getvalue()
    assert "Out-of-order: expected step 'research', got 'wrong_step'." in (
        captured_err.getvalue()
    )


def test_sub_workflow_pending_decoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject a ``sub_workflow_pending`` envelope. REPL decodes with
    child-fingerprint surface.
    """
    target = _copy_artifact_inlining(tmp_path)

    import importlib

    import megalos_server.dryrun as dryrun_mod

    importlib.reload(dryrun_mod)

    from megalos_server import create_app as real_create_app

    def injecting_create_app(*args: Any, **kwargs: Any) -> Any:
        mcp = real_create_app(*args, **kwargs)
        real_call_tool = mcp.call_tool
        depth = {"n": 0}

        async def wrapped(
            name: str, arguments: dict[str, Any], *a: Any, **kw: Any
        ) -> Any:
            if depth["n"] == 0 and name == "enter_sub_workflow":
                class _R:
                    structured_content = {
                        "status": "error",
                        "code": "sub_workflow_pending",
                        "error": "a child session is already in flight for this call-step",
                        "child_session_fingerprint": "abcd1234",
                        "parent_session_fingerprint": "efgh5678",
                        "frame_type": "call",
                    }

                return _R()
            depth["n"] += 1
            try:
                return await real_call_tool(name, arguments, *a, **kw)
            finally:
                depth["n"] -= 1

        mcp.call_tool = wrapped  # type: ignore[method-assign]
        return mcp

    monkeypatch.setattr(dryrun_mod, "create_app", injecting_create_app)
    monkeypatch.setattr(sys, "argv", ["megalos-dryrun", str(target)])
    stdin_lines = iter(["intro content"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(stdin_lines))

    import io

    captured_err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", captured_err)

    with pytest.raises(SystemExit) as exc_info:
        dryrun_mod.main()
    assert exc_info.value.code == 1, captured_err.getvalue()
    err = captured_err.getvalue()
    assert "Sub-workflow pending" in err
    assert "abcd1234" in err


def test_call_step_requires_enter_sub_workflow_decoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject an envelope simulating ``call_step_requires_enter_sub_workflow``.
    REPL decodes with the step_id + call_target.
    """
    target = _copy_artifact_inlining(tmp_path)

    import importlib

    import megalos_server.dryrun as dryrun_mod

    importlib.reload(dryrun_mod)

    from megalos_server import create_app as real_create_app

    def injecting_create_app(*args: Any, **kwargs: Any) -> Any:
        mcp = real_create_app(*args, **kwargs)
        real_call_tool = mcp.call_tool
        depth = {"n": 0}

        async def wrapped(
            name: str, arguments: dict[str, Any], *a: Any, **kw: Any
        ) -> Any:
            # Inject on the first submit_step (intro), simulating a scenario
            # where submit_step is wrongly called on a call-step.
            if depth["n"] == 0 and name == "submit_step":
                class _R:
                    structured_content = {
                        "status": "error",
                        "code": "call_step_requires_enter_sub_workflow",
                        "error": "step 'research' has `call: artifact_inlining_child`. Use the `enter_sub_workflow` tool, not `submit_step`, to invoke the child workflow.",
                        "step_id": "research",
                        "call_target": "artifact_inlining_child",
                        "hint": "enter_sub_workflow",
                    }

                return _R()
            depth["n"] += 1
            try:
                return await real_call_tool(name, arguments, *a, **kw)
            finally:
                depth["n"] -= 1

        mcp.call_tool = wrapped  # type: ignore[method-assign]
        return mcp

    monkeypatch.setattr(dryrun_mod, "create_app", injecting_create_app)
    monkeypatch.setattr(sys, "argv", ["megalos-dryrun", str(target)])
    stdin_lines = iter(["intro content"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(stdin_lines))

    import io

    captured_err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", captured_err)

    with pytest.raises(SystemExit) as exc_info:
        dryrun_mod.main()
    assert exc_info.value.code == 1, captured_err.getvalue()
    err = captured_err.getvalue()
    assert "REPL bug: submit_step was called on call-step 'research'" in err
    assert "artifact_inlining_child" in err


def test_invalid_call_context_from_decoded(tmp_path: Path) -> None:
    """Parent has call_context_from pointing at a ref that won't resolve.
    enter_sub_workflow emits ``invalid_argument`` with
    ``field=call_context_from``. REPL decodes it.
    """
    # Child must exist to get past the call-target cross-check.
    (tmp_path / "ccf_child.yaml").write_text(
        "schema_version: \"0.3\"\n"
        "name: ccf_child\n"
        "description: Child receiving unresolvable context.\n"
        "category: analysis_decision\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: ack\n"
        "    title: Acknowledge\n"
        "    directive_template: Acknowledge.\n"
        "    gates:\n"
        "      - acknowledged\n"
        "    anti_patterns:\n"
        "      - Ignoring\n",
        encoding="utf-8",
    )
    parent = tmp_path / "ccf_parent.yaml"
    parent.write_text(
        "schema_version: \"0.3\"\n"
        "name: ccf_parent\n"
        "description: Parent with unresolvable call_context_from.\n"
        "category: analysis_decision\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: intro\n"
        "    title: Intro\n"
        "    directive_template: Intro content.\n"
        "    gates:\n"
        "      - intro done\n"
        "    anti_patterns:\n"
        "      - Skipping\n"
        "  - id: delegate\n"
        "    title: Delegate with bogus ref\n"
        "    directive_template: Hand off.\n"
        "    gates:\n"
        "      - handoff attempted\n"
        "    anti_patterns:\n"
        "      - Proceeding\n"
        "    call: ccf_child\n"
        "    call_context_from: step_data.intro.nonexistent_field\n",
        encoding="utf-8",
    )
    result = _run([str(parent)], input="intro content\n")
    assert result.returncode != 0
    err = result.stderr
    assert "Invalid call_context_from" in err
    assert "step_data.intro.nonexistent_field" in err


def test_repl_never_calls_get_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC #11 / D047: dryrun REPL must derive all frame state from
    envelope deltas. No ``get_state`` peek. In-process shim records every
    ``call_tool`` invocation during a full descent + ascent flow.
    """
    target = _copy_artifact_inlining(tmp_path)

    import importlib

    import megalos_server.dryrun as dryrun_mod

    importlib.reload(dryrun_mod)

    from megalos_server import create_app as real_create_app

    tool_names: list[str] = []

    def recording_create_app(*args: Any, **kwargs: Any) -> Any:
        mcp = real_create_app(*args, **kwargs)
        real_call_tool = mcp.call_tool
        depth = {"n": 0}

        async def wrapped(
            name: str, arguments: dict[str, Any], *a: Any, **kw: Any
        ) -> Any:
            if depth["n"] == 0:
                tool_names.append(name)
            depth["n"] += 1
            try:
                return await real_call_tool(name, arguments, *a, **kw)
            finally:
                depth["n"] -= 1

        mcp.call_tool = wrapped  # type: ignore[method-assign]
        return mcp

    monkeypatch.setattr(dryrun_mod, "create_app", recording_create_app)
    monkeypatch.setattr(sys, "argv", ["megalos-dryrun", str(target)])
    stdin_lines = iter(["intro", "gather", "brief"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(stdin_lines))

    import io

    captured_out = io.StringIO()
    captured_err = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured_out)
    monkeypatch.setattr(sys, "stderr", captured_err)

    with pytest.raises(SystemExit) as exc_info:
        dryrun_mod.main()
    assert exc_info.value.code == 0, captured_err.getvalue()
    # Full flow ran — expect start_workflow + enter_sub_workflow + several submit_step.
    assert "start_workflow" in tool_names
    assert "enter_sub_workflow" in tool_names
    assert tool_names.count("submit_step") == 3  # intro + gather + brief.
    # Hard assertion: no get_state peek across the entire flow.
    assert "get_state" not in tool_names, tool_names


# ---- S04 edge cases: parent_output_schema_fail, state drift, 3-level ------


OUTPUT_SCHEMA_FAIL_PARENT_FIXTURE = FIXTURES_DIR / "output_schema_fail_parent.yaml"
OUTPUT_SCHEMA_FAIL_CHILD_FIXTURE = FIXTURES_DIR / "output_schema_fail_child.yaml"


def _copy_output_schema_fail(tmp_path: Path) -> Path:
    """Copy the schema-failing parent+child into tmp_path, return parent path."""
    parent = tmp_path / "output_schema_fail_parent.yaml"
    shutil.copy(OUTPUT_SCHEMA_FAIL_PARENT_FIXTURE, parent)
    shutil.copy(
        OUTPUT_SCHEMA_FAIL_CHILD_FIXTURE,
        tmp_path / "output_schema_fail_child.yaml",
    )
    return parent


def test_parent_output_schema_fail_decoded(tmp_path: Path) -> None:
    """Parent call-step carries an output_schema the child's final artifact
    does NOT satisfy. ``_propagate_to_parent`` builds a ``session_escalated``
    envelope whose nested ``child_error.reason`` is
    ``parent_output_schema_fail`` (tools.py lines 712-725). REPL decodes
    with the child validation errors listed verbatim; exit 1.
    """
    target = _copy_output_schema_fail(tmp_path)
    # Parent: intro (p1) → call-child (p2) → wrap-up (p3, unreachable).
    # Child: investigate (c1) → emit-freeform (c2).
    # stdin feeds p1, c1, c2; propagation fires after c2 and should
    # escalate — no line consumed for p3.
    stdin = "intro line\ninvestigation notes\nfreeform prose without approval field\n"
    result = _run([str(target)], input=stdin)
    assert result.returncode == 1, (result.stdout, result.stderr)
    err = result.stderr
    assert (
        "Sub-workflow 'output_schema_fail_child' completed, but its artifact "
        "failed parent's output_schema:"
    ) in err
    # Validation errors from the child's final artifact (plain prose; the
    # parent's output_schema requires a JSON object) are surfaced verbatim.
    # Server's _validate_output reports "Content is not valid JSON: ..." as
    # the first error when the artifact is non-JSON; assert that line appears
    # under the decoded header.
    assert "Content is not valid JSON" in err
    assert "Parent session escalated." in err
    # D051: artifact body content must NOT be echoed. The child's freeform
    # content starts with 'freeform prose' — assert it is not reproduced.
    assert "freeform prose without approval field" not in err


def test_sub_workflow_state_drift_decoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-process: inject a ``session_escalated`` envelope that represents
    ``sub_workflow_state_drift`` (tools.py lines 700-710): no
    ``called_workflow_error`` wrapper, just ``parent_session_fingerprint``
    + ``child_session_fingerprint`` at the top level. REPL decodes.
    """
    target = _copy_artifact_inlining(tmp_path)

    import importlib

    import megalos_server.dryrun as dryrun_mod

    importlib.reload(dryrun_mod)

    from megalos_server import create_app as real_create_app

    def injecting_create_app(*args: Any, **kwargs: Any) -> Any:
        mcp = real_create_app(*args, **kwargs)
        real_call_tool = mcp.call_tool
        depth = {"n": 0}
        # Inject on the submit_step that terminates the child workflow —
        # i.e. the second submit_step call (intro already submitted, then
        # descent into child, child gather submitted, child brief would
        # propagate). Simulate state-drift response on the ``brief`` submit.
        submit_count = {"n": 0}

        async def wrapped(
            name: str, arguments: dict[str, Any], *a: Any, **kw: Any
        ) -> Any:
            if depth["n"] == 0 and name == "submit_step":
                submit_count["n"] += 1
                if submit_count["n"] == 3:
                    class _R:
                        structured_content = {
                            "status": "error",
                            "code": "session_escalated",
                            "error": "parent state drift during sub-workflow propagation",
                            "parent_session_fingerprint": "parent_fp_abcd",
                            "child_session_fingerprint": "child_fp_wxyz",
                        }

                    return _R()
            depth["n"] += 1
            try:
                return await real_call_tool(name, arguments, *a, **kw)
            finally:
                depth["n"] -= 1

        mcp.call_tool = wrapped  # type: ignore[method-assign]
        return mcp

    monkeypatch.setattr(dryrun_mod, "create_app", injecting_create_app)
    monkeypatch.setattr(sys, "argv", ["megalos-dryrun", str(target)])
    stdin_lines = iter(["intro", "gather", "brief"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(stdin_lines))

    import io

    captured_err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", captured_err)

    with pytest.raises(SystemExit) as exc_info:
        dryrun_mod.main()
    assert exc_info.value.code == 1, captured_err.getvalue()
    err = captured_err.getvalue()
    assert "Sub-workflow state drift" in err
    assert "parent_fp_abcd" in err
    assert "child_fp_wxyz" in err


def _write_three_level_chain(tmp_path: Path) -> Path:
    """Author a 3-level parent→child→grandchild chain in ``tmp_path``.

    Parent has 3 steps (intro, call-child, final). Child has 3 steps
    (pre-call, call-grandchild, post-call). Grandchild has 1 step
    (terminal). Returns the parent path.

    Empirical semantics (see tools.py:733 and tools.py:1222): one frame
    pops per submit_step call. When the grandchild's terminal step is
    submitted, the server advances the child past its call-step to
    ``post-call``; REPL pops the grandchild frame. The child's ``post-call``
    submission then propagates to the parent, which advances to ``final``;
    REPL pops the child frame. Two separate pops across two submit_step
    calls — not a single multi-frame response.
    """
    (tmp_path / "grandchild_wf.yaml").write_text(
        "schema_version: \"0.3\"\n"
        "name: grandchild_wf\n"
        "description: Terminal-step grandchild.\n"
        "category: analysis_decision\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: gc_terminal\n"
        "    title: Grandchild terminal\n"
        "    directive_template: Emit the grandchild artifact.\n"
        "    gates:\n"
        "      - grandchild artifact emitted\n"
        "    anti_patterns:\n"
        "      - Leaving grandchild empty\n",
        encoding="utf-8",
    )
    (tmp_path / "child_wf.yaml").write_text(
        "schema_version: \"0.3\"\n"
        "name: child_wf\n"
        "description: Child whose middle step calls a grandchild.\n"
        "category: analysis_decision\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: c_pre\n"
        "    title: Child pre-call\n"
        "    directive_template: Prep work before delegating down.\n"
        "    gates:\n"
        "      - pre-call done\n"
        "    anti_patterns:\n"
        "      - Skipping prep\n"
        "  - id: c_call\n"
        "    title: Child delegates to grandchild\n"
        "    directive_template: Hand off to grandchild.\n"
        "    gates:\n"
        "      - grandchild delegated\n"
        "    anti_patterns:\n"
        "      - Skipping delegation\n"
        "    call: grandchild_wf\n"
        "  - id: c_post\n"
        "    title: Child post-call\n"
        "    directive_template: Wrap up after grandchild ascent.\n"
        "    gates:\n"
        "      - post-call done\n"
        "    anti_patterns:\n"
        "      - Skipping wrap-up\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent_wf.yaml"
    parent.write_text(
        "schema_version: \"0.3\"\n"
        "name: parent_wf\n"
        "description: Root parent whose middle step calls the child.\n"
        "category: analysis_decision\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: p_intro\n"
        "    title: Parent intro\n"
        "    directive_template: Write the intro.\n"
        "    gates:\n"
        "      - intro written\n"
        "    anti_patterns:\n"
        "      - Skipping intro\n"
        "  - id: p_call\n"
        "    title: Parent delegates to child\n"
        "    directive_template: Hand off to child.\n"
        "    gates:\n"
        "      - child delegated\n"
        "    anti_patterns:\n"
        "      - Skipping delegation\n"
        "    call: child_wf\n"
        "  - id: p_final\n"
        "    title: Parent final\n"
        "    directive_template: Wrap up at root.\n"
        "    gates:\n"
        "      - final written\n"
        "    anti_patterns:\n"
        "      - Skipping final\n",
        encoding="utf-8",
    )
    return parent


def test_nested_descent_three_level_single_frame_pop(tmp_path: Path) -> None:
    """3-level descent: parent → child → grandchild. Grandchild completion
    propagates into the child (one pop banner). Child's post-call step is
    then driven by the operator; child completion propagates into the
    parent (second pop banner). Two descent banners + two ascent banners
    across two submit_step calls. Exit 0.
    """
    parent = _write_three_level_chain(tmp_path)
    # stdin: p_intro, c_pre, gc_terminal, c_post, p_final.
    stdin = "intro\npre work\ngrandchild artifact\npost work\nfinal\n"
    result = _run([str(parent)], input=stdin)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    # Two descent banners: parent → child (depth 1 = 2-space indent), then
    # child → grandchild (depth 2 = 4-space indent).
    assert "  → Entering sub-workflow 'child_wf'" in out
    assert "    → Entering sub-workflow 'grandchild_wf'" in out
    # Two ascent banners: grandchild → child at depth 2 (4-space indent),
    # then child → parent at depth 1 (2-space indent).
    assert "    ← Returned from sub-workflow 'grandchild_wf'" in out
    assert "  ← Returned from sub-workflow 'child_wf'" in out
    # Exactly one of each banner text (single-frame pop per submit_step).
    assert out.count("← Returned from sub-workflow 'grandchild_wf'") == 1
    assert out.count("← Returned from sub-workflow 'child_wf'") == 1
    # Parent's final step runs.
    assert "=== Step: p_final" in out
    assert "Workflow complete" in out


def test_workflow_changed_mid_descent_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject a ``workflow_changed`` envelope on the child's first
    submit_step while a descent frame is live. REPL must route straight
    to the terminal decoder (no ascent banner, no parent-resume attempt).
    The envelope does NOT carry ``propagated_from_sub_workflow``; the
    dispatch-order comment in dryrun.py pins this contract.
    """
    target = _copy_artifact_inlining(tmp_path)

    import importlib

    import megalos_server.dryrun as dryrun_mod

    importlib.reload(dryrun_mod)

    from megalos_server import create_app as real_create_app

    # Fire on the second submit_step call: first is the parent's intro,
    # second is the child's first step after auto-descent.
    def injecting_create_app(*args: Any, **kwargs: Any) -> Any:
        mcp = real_create_app(*args, **kwargs)
        real_call_tool = mcp.call_tool
        depth = {"n": 0}
        submit_count = {"n": 0}

        async def wrapped(
            name: str, arguments: dict[str, Any], *a: Any, **kw: Any
        ) -> Any:
            if depth["n"] == 0 and name == "submit_step":
                submit_count["n"] += 1
                if submit_count["n"] == 2:
                    class _R:
                        structured_content = {
                            "status": "error",
                            "code": "workflow_changed",
                            "error": (
                                "Workflow 'artifact_inlining_child' has changed "
                                "since this session was started."
                            ),
                            "session_fingerprint": "child_fp_123",
                            "workflow_type": "artifact_inlining_child",
                            "previous_fingerprint": "old_fp",
                            "current_fingerprint": "new_fp",
                        }

                    return _R()
            depth["n"] += 1
            try:
                return await real_call_tool(name, arguments, *a, **kw)
            finally:
                depth["n"] -= 1

        mcp.call_tool = wrapped  # type: ignore[method-assign]
        return mcp

    monkeypatch.setattr(dryrun_mod, "create_app", injecting_create_app)
    monkeypatch.setattr(sys, "argv", ["megalos-dryrun", str(target)])
    stdin_lines = iter(["intro", "gather"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(stdin_lines))

    import io

    captured_out = io.StringIO()
    captured_err = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured_out)
    monkeypatch.setattr(sys, "stderr", captured_err)

    with pytest.raises(SystemExit) as exc_info:
        dryrun_mod.main()
    assert exc_info.value.code == 1, captured_err.getvalue()
    out = captured_out.getvalue()
    # Descent happened BEFORE the injection.
    assert "→ Entering sub-workflow 'artifact_inlining_child'" in out
    # No ascent banner: the terminal status fired without ascent pop.
    assert "← Returned from sub-workflow" not in out


def test_call_step_with_branches_auto_takes_default(tmp_path: Path) -> None:
    """Regression: a parent call-step that declares ``branches`` +
    ``default_branch`` must NOT prompt the operator for a branch on
    propagation. ``_advance_parent`` deterministically picks
    ``default_branch`` server-side (tools.py:480-481); the REPL must not
    spuriously open a branch prompt on the post-ascent envelope.
    """
    shutil.copy(
        ARTIFACT_INLINING_CHILD_FIXTURE, tmp_path / "artifact_inlining_child.yaml"
    )
    parent = tmp_path / "branches_call_parent.yaml"
    # Step order: alt_wrap appears BEFORE happy_wrap so only the branch
    # that the call-step's default_branch names is reachable from the
    # post-propagation envelope — alt_wrap is only reachable via explicit
    # branch selection, which the regression under test asserts does NOT
    # happen. happy_wrap is last (no trailing step), so it terminates the
    # workflow cleanly without accidentally falling into alt_wrap.
    parent.write_text(
        "schema_version: \"0.3\"\n"
        "name: branches_call_parent\n"
        "description: Parent whose call-step has branches + default_branch.\n"
        "category: analysis_decision\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: intro\n"
        "    title: Intro\n"
        "    directive_template: Intro content.\n"
        "    gates:\n"
        "      - intro\n"
        "    anti_patterns:\n"
        "      - Skip\n"
        "  - id: delegate\n"
        "    title: Delegate with branches\n"
        "    directive_template: Delegate then route.\n"
        "    gates:\n"
        "      - delegated\n"
        "    anti_patterns:\n"
        "      - Skip delegation\n"
        "    call: artifact_inlining_child\n"
        "    branches:\n"
        "      - next: happy_wrap\n"
        "        condition: Default path\n"
        "      - next: alt_wrap\n"
        "        condition: Alternative path\n"
        "    default_branch: happy_wrap\n"
        "  - id: alt_wrap\n"
        "    title: Alt wrap-up\n"
        "    directive_template: Wrap up alt.\n"
        "    gates:\n"
        "      - wrapped alt\n"
        "    anti_patterns:\n"
        "      - Skip\n"
        "  - id: happy_wrap\n"
        "    title: Happy wrap-up\n"
        "    directive_template: Wrap up happy.\n"
        "    gates:\n"
        "      - wrapped\n"
        "    anti_patterns:\n"
        "      - Skip\n",
        encoding="utf-8",
    )
    # stdin: intro, gather, brief, happy_wrap content. NO branch selection
    # line — regression target is the absence of a prompt.
    stdin = "intro\ngather\nbrief\nhappy\n"
    result = _run([str(parent)], input=stdin)
    assert result.returncode == 0, (result.stdout, result.stderr)
    out = result.stdout
    # default_branch fired automatically: we reach happy_wrap, not alt_wrap.
    assert "=== Step: happy_wrap" in out
    assert "=== Step: alt_wrap" not in out
    # No branch prompt text was emitted — the REPL's branch prompt always
    # prints a ``Branches:`` header.
    assert "Branches:" not in out


def test_validation_retry_inside_descent(tmp_path: Path) -> None:
    """Child step with an output_schema inside a live descent: operator
    submits invalid JSON (re-prompt fires at child depth), then valid
    (descent continues). Stack stays at child depth through the retry;
    no ascent banner fires mid-retry. Exit 0.
    """
    # Copy demo_validation.yaml (2 steps, first is JSON-validated) as child.
    shutil.copy(
        FIXTURES_DIR / "demo_validation.yaml",
        tmp_path / "demo_validation.yaml",
    )
    parent = tmp_path / "retry_parent.yaml"
    parent.write_text(
        "schema_version: \"0.3\"\n"
        "name: retry_parent\n"
        "description: Parent that delegates to the validation-gated child.\n"
        "category: analysis_decision\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: intro\n"
        "    title: Intro\n"
        "    directive_template: Intro.\n"
        "    gates:\n"
        "      - intro\n"
        "    anti_patterns:\n"
        "      - Skip\n"
        "  - id: delegate\n"
        "    title: Delegate to validation-gated child\n"
        "    directive_template: Delegate.\n"
        "    gates:\n"
        "      - delegated\n"
        "    anti_patterns:\n"
        "      - Skip\n"
        "    call: demo_validation\n",
        encoding="utf-8",
    )
    # stdin: intro, invalid JSON (triggers child-side re-prompt), valid
    # JSON, summary line for demo_validation step 2.
    stdin = (
        f"intro\n{_INVALID_JSON}\n{_VALID_JSON}\nsummary line\n"
    )
    result = _run([str(parent)], input=stdin)
    assert result.returncode == 0, (result.stdout, result.stderr)
    out = result.stdout
    err = result.stderr
    # Descent banner rendered.
    assert "  → Entering sub-workflow 'demo_validation'" in out
    # Validation re-prompt fired at child indent (2-space).
    assert "  Validation failed:" in err
    # Retry did NOT fire an ascent banner — ascent only follows final-step
    # submission, which requires the retry to have advanced past the gated step.
    # The ascent banner for the child does fire at the very end once both
    # child steps complete; assert it appears exactly once.
    assert out.count("← Returned from sub-workflow 'demo_validation'") == 1
    assert "Workflow complete" in out


def _write_five_level_chain(tmp_path: Path) -> Path:
    """Author a 5-level descent chain (A → B → C → D → E). Each inner
    workflow has exactly one call-step that targets the next one;
    the leaf (E) is a single terminal step.
    """
    (tmp_path / "level_e.yaml").write_text(
        "schema_version: \"0.3\"\n"
        "name: level_e\n"
        "description: Terminal leaf.\n"
        "category: analysis_decision\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: e_terminal\n"
        "    title: Leaf terminal\n"
        "    directive_template: Emit leaf.\n"
        "    gates:\n"
        "      - leaf\n"
        "    anti_patterns:\n"
        "      - Skip\n",
        encoding="utf-8",
    )
    for name, target in (("level_d", "level_e"), ("level_c", "level_d"), ("level_b", "level_c")):
        (tmp_path / f"{name}.yaml").write_text(
            f"schema_version: \"0.3\"\n"
            f"name: {name}\n"
            f"description: Chain link to {target}.\n"
            f"category: analysis_decision\n"
            f"output_format: text\n"
            f"steps:\n"
            f"  - id: {name}_call\n"
            f"    title: Delegate to {target}\n"
            f"    directive_template: Hand off.\n"
            f"    gates:\n"
            f"      - delegated\n"
            f"    anti_patterns:\n"
            f"      - Skip\n"
            f"    call: {target}\n",
            encoding="utf-8",
        )
    parent = tmp_path / "level_a.yaml"
    parent.write_text(
        "schema_version: \"0.3\"\n"
        "name: level_a\n"
        "description: Root of a 5-level descent chain.\n"
        "category: analysis_decision\n"
        "output_format: text\n"
        "steps:\n"
        "  - id: a_call\n"
        "    title: Delegate to level_b\n"
        "    directive_template: Hand off.\n"
        "    gates:\n"
        "      - delegated\n"
        "    anti_patterns:\n"
        "      - Skip\n"
        "    call: level_b\n",
        encoding="utf-8",
    )
    return parent


def test_max_nesting_depth_banner_at_depth_4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5-level descent (A → B → C → D → E) exercises the D049 visual cap.
    ``_indent_for`` clamps indentation at depth 4 (8 spaces). The
    ``[max nesting depth reached]`` banner prints the first time the REPL
    descends into a frame at depth == _MAX_DEPTH.

    ``enter_sub_workflow`` has no server-side depth cap (only ``push_flow``
    enforces one — see state.py:240-251 via max_stack_depth=3 in
    tools.py:1726), so the chain runs to completion.

    This runs in-process so the test can drive the REPL without feeding
    5 stdin lines through a pipe; the shape under test is REPL visual
    output, not subprocess plumbing.
    """
    parent = _write_five_level_chain(tmp_path)

    import importlib

    import megalos_server.dryrun as dryrun_mod

    importlib.reload(dryrun_mod)

    monkeypatch.setattr(sys, "argv", ["megalos-dryrun", str(parent)])
    # Only one stdin consumption: at the leaf terminal step.
    stdin_lines = iter(["leaf content"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(stdin_lines))

    import io

    captured_out = io.StringIO()
    captured_err = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured_out)
    monkeypatch.setattr(sys, "stderr", captured_err)

    with pytest.raises(SystemExit) as exc_info:
        dryrun_mod.main()
    assert exc_info.value.code == 0, captured_err.getvalue()
    out = captured_out.getvalue()
    # All 4 descent banners fired.
    assert "  → Entering sub-workflow 'level_b'" in out
    assert "    → Entering sub-workflow 'level_c'" in out
    assert "      → Entering sub-workflow 'level_d'" in out
    # Level E sits at depth 4 — the visual cap. Indent stays at 8 spaces
    # (4 levels × 2 spaces). Banner appears at that indent.
    assert "        → Entering sub-workflow 'level_e'" in out
    # The cap banner renders alongside the depth-4 descent banner.
    assert "        [max nesting depth reached]" in out


def test_repl_never_calls_get_state_across_edge_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC #11 / D047 regression under the edge-case fixtures introduced
    here: the 3-level descent path must derive all frame state from
    envelope deltas, never via ``get_state``.
    """
    parent = _write_three_level_chain(tmp_path)

    import importlib

    import megalos_server.dryrun as dryrun_mod

    importlib.reload(dryrun_mod)

    from megalos_server import create_app as real_create_app

    tool_names: list[str] = []

    def recording_create_app(*args: Any, **kwargs: Any) -> Any:
        mcp = real_create_app(*args, **kwargs)
        real_call_tool = mcp.call_tool
        depth = {"n": 0}

        async def wrapped(
            name: str, arguments: dict[str, Any], *a: Any, **kw: Any
        ) -> Any:
            if depth["n"] == 0:
                tool_names.append(name)
            depth["n"] += 1
            try:
                return await real_call_tool(name, arguments, *a, **kw)
            finally:
                depth["n"] -= 1

        mcp.call_tool = wrapped  # type: ignore[method-assign]
        return mcp

    monkeypatch.setattr(dryrun_mod, "create_app", recording_create_app)
    monkeypatch.setattr(sys, "argv", ["megalos-dryrun", str(parent)])
    stdin_lines = iter(["intro", "pre work", "gc artifact", "post work", "final"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(stdin_lines))

    import io

    captured_out = io.StringIO()
    captured_err = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured_out)
    monkeypatch.setattr(sys, "stderr", captured_err)

    with pytest.raises(SystemExit) as exc_info:
        dryrun_mod.main()
    assert exc_info.value.code == 0, captured_err.getvalue()
    # Hard assertion: no get_state across the 3-level path.
    assert "get_state" not in tool_names, tool_names


def test_s01_s02_s03_regression_guard() -> None:
    """Existing S01/S02/S03 tests must run unchanged. If any needs edit
    during S04 work, the guard fires by virtue of pytest already running
    them — this test is a narrative marker plus a sanity assertion that
    the expected S01-S03 test names are still defined in this file.
    """
    import tests.test_dryrun as this_mod

    expected = {
        "test_help_exits_zero",
        "test_nonexistent_path_errors_cleanly",
        "test_canonical_fixture_runs_end_to_end",
        "test_validation_retry_loop_advances_on_valid",
        "test_branch_default_selection_reaches_default_target",
        "test_branch_numeric_selection_reaches_chosen_target",
        "test_branch_invalid_numeric_reprompts_locally",
        "test_precondition_rendered_at_step_entry",
        "test_skip_detection_single_step",
        "test_skip_detection_multi_step_chain",
        "test_invalid_branch_server_rejection_exits_with_decoded_message",
    }
    present = {name for name in dir(this_mod) if name.startswith("test_")}
    missing = expected - present
    assert not missing, f"S01-S03 tests dropped: {missing}"


# ---- Scripted-responses file (--responses-file) ----------------------------


def test_scripted_readers_are_called_not_interactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Structural invariant: scripted-mode reader replacements are installed
    BEFORE the REPL calls any reader.

    Monkey-patch both ``_read_response`` and ``_prompt_branch`` with
    recording stubs that would raise if called; run ``main()`` with
    ``--responses-file``; assert neither stub fired because
    ``_install_scripted_mode`` overwrites them with scripted variants.
    """
    target = tmp_path / "canonical.yaml"
    shutil.copy(CANONICAL_FIXTURE, target)

    import importlib

    import megalos_server.dryrun as dryrun_mod

    importlib.reload(dryrun_mod)

    def boom_read_response() -> str:
        raise AssertionError("_read_response stub should have been replaced")

    def boom_prompt_branch(
        branches: list, default: str, indent: str = ""
    ) -> str:
        raise AssertionError("_prompt_branch stub should have been replaced")

    monkeypatch.setattr(dryrun_mod, "_read_response", boom_read_response)
    monkeypatch.setattr(dryrun_mod, "_prompt_branch", boom_prompt_branch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "megalos-dryrun",
            str(target),
            "--responses-file",
            str(CANONICAL_GOLDEN),
        ],
    )

    # Capture stdout/stderr so the assertion-error traceback (if any) lands
    # in captured_err rather than the test harness.
    import io

    captured_out = io.StringIO()
    captured_err = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured_out)
    monkeypatch.setattr(sys, "stderr", captured_err)

    with pytest.raises(SystemExit) as exc_info:
        dryrun_mod.main()
    assert exc_info.value.code == 0, captured_err.getvalue()


def test_scripted_canonical_golden(tmp_path: Path) -> None:
    """Reuse canonical.yaml with canonical_golden.yaml; assert exit 0 and
    all three step banners rendered on stdout."""
    target = tmp_path / "canonical.yaml"
    shutil.copy(CANONICAL_FIXTURE, target)
    result = _run([str(target), "--responses-file", str(CANONICAL_GOLDEN)])
    assert result.returncode == 0, result.stderr
    assert "alpha" in result.stdout
    assert "bravo" in result.stdout
    assert "charlie" in result.stdout
    assert "Workflow complete" in result.stdout


def test_scripted_demo_validation_golden(tmp_path: Path) -> None:
    """Reuse demo_validation.yaml with demo_validation_golden.yaml; assert
    retry surface is exercised (one invalid + one valid) and exit 0."""
    target = tmp_path / "demo_validation.yaml"
    shutil.copy(DEMO_VALIDATION_FIXTURE, target)
    result = _run([str(target), "--responses-file", str(DEMO_VALIDATION_GOLDEN)])
    assert result.returncode == 0, result.stderr
    assert "Validation failed:" in result.stderr
    assert "Retries remaining:" in result.stderr
    assert "summarize" in result.stdout


def test_scripted_demo_branching_golden(tmp_path: Path) -> None:
    """Reuse demo_branching.yaml with demo_branching_golden.yaml; assert
    exit 0 and advanced_track banner rendered (confirming branch choice)."""
    target = tmp_path / "demo_branching.yaml"
    shutil.copy(DEMO_BRANCHING_FIXTURE, target)
    result = _run([str(target), "--responses-file", str(DEMO_BRANCHING_GOLDEN)])
    assert result.returncode == 0, result.stderr
    assert "assess_expertise" in result.stdout
    assert "advanced_track" in result.stdout
    # Other tracks not taken — this is a sanity check on branch routing, not
    # strict exclusion of the strings (they appear in the branches list).
    assert "Workflow complete" in result.stdout


def test_scripted_artifact_inlining_golden(tmp_path: Path) -> None:
    """Reuse parent+child artifact_inlining fixtures; assert exit 0 and
    both parent and indented child banners render."""
    parent = tmp_path / "artifact_inlining_parent.yaml"
    child = tmp_path / "artifact_inlining_child.yaml"
    shutil.copy(ARTIFACT_INLINING_PARENT_FIXTURE, parent)
    shutil.copy(ARTIFACT_INLINING_CHILD_FIXTURE, child)
    result = _run([str(parent), "--responses-file", str(ARTIFACT_INLINING_GOLDEN)])
    assert result.returncode == 0, result.stderr
    assert "intro" in result.stdout
    assert "Entering sub-workflow" in result.stdout
    # Indented child banners (two-space indent per _indent_for at depth 1).
    assert "  === Step: gather" in result.stdout
    assert "  === Step: brief" in result.stdout
    assert "Returned from sub-workflow" in result.stdout


def test_scripted_missing_version_rejected(tmp_path: Path) -> None:
    """Responses file without 'version' key exits 1 with decoded banner.

    Format validation runs before workflow load, so the workflow_dir isn't
    actually loaded here — but keep responses file outside it anyway to stay
    consistent with the other scripted-mode tests.
    """
    workflow_dir = tmp_path / "wf"
    workflow_dir.mkdir()
    target = workflow_dir / "canonical.yaml"
    shutil.copy(CANONICAL_FIXTURE, target)
    responses = tmp_path / "no_version.yaml"
    responses.write_text(
        "entries:\n"
        "  - step_id: alpha\n"
        "    response: ok\n",
        encoding="utf-8",
    )
    result = _run([str(target), "--responses-file", str(responses)])
    assert result.returncode == 1
    assert "missing required 'version' field" in result.stderr


def test_scripted_unknown_version_rejected(tmp_path: Path) -> None:
    """Responses file with unsupported version exits 1 with decoded banner."""
    workflow_dir = tmp_path / "wf"
    workflow_dir.mkdir()
    target = workflow_dir / "canonical.yaml"
    shutil.copy(CANONICAL_FIXTURE, target)
    responses = tmp_path / "bad_version.yaml"
    responses.write_text(
        "version: 99\n"
        "entries:\n"
        "  - step_id: alpha\n"
        "    response: ok\n",
        encoding="utf-8",
    )
    result = _run([str(target), "--responses-file", str(responses)])
    assert result.returncode == 1
    assert "Unknown responses-file version: 99" in result.stderr
    assert "Supported: [1]" in result.stderr


def test_scripted_step_id_drift_exits(tmp_path: Path) -> None:
    """Entry step_id not matching current REPL step exits 1 with both
    expected and actual step_id named on stderr."""
    workflow_dir = tmp_path / "wf"
    workflow_dir.mkdir()
    target = workflow_dir / "canonical.yaml"
    shutil.copy(CANONICAL_FIXTURE, target)
    responses = tmp_path / "drift.yaml"
    # First entry matches (alpha), second drifts to unknown_step instead of
    # bravo — REPL is at bravo when it consumes entry 2.
    responses.write_text(
        "version: 1\n"
        "entries:\n"
        "  - step_id: alpha\n"
        "    response: ok\n"
        "  - step_id: unknown_step\n"
        "    response: ok\n"
        "  - step_id: charlie\n"
        "    response: ok\n",
        encoding="utf-8",
    )
    result = _run([str(target), "--responses-file", str(responses)])
    assert result.returncode == 1
    assert "unknown_step" in result.stderr
    assert "bravo" in result.stderr


def test_scripted_entry_type_mismatch_exits(tmp_path: Path) -> None:
    """Entry has 'response' when a 'branch' is expected: exit 1, decoded banner."""
    workflow_dir = tmp_path / "wf"
    workflow_dir.mkdir()
    target = workflow_dir / "demo_branching.yaml"
    shutil.copy(DEMO_BRANCHING_FIXTURE, target)
    responses = tmp_path / "type_mismatch.yaml"
    # assess_expertise expects a content response then a branch selection.
    # Provide two 'response' entries for assess_expertise — second one should
    # have been 'branch:' so the scripted reader detects the type mismatch.
    responses.write_text(
        "version: 1\n"
        "entries:\n"
        "  - step_id: assess_expertise\n"
        "    response: expert\n"
        "  - step_id: assess_expertise\n"
        "    response: oops_should_be_branch\n",
        encoding="utf-8",
    )
    result = _run([str(target), "--responses-file", str(responses)])
    assert result.returncode == 1
    assert "expected branch selection but script provided response" in result.stderr


def test_scripted_exhaustion_exits_with_expected_type(tmp_path: Path) -> None:
    """Responses file too short: exit 1 with exhaustion banner naming the
    expected entry type."""
    workflow_dir = tmp_path / "wf"
    workflow_dir.mkdir()
    target = workflow_dir / "canonical.yaml"
    shutil.copy(CANONICAL_FIXTURE, target)
    responses = tmp_path / "too_short.yaml"
    # canonical has three steps; provide only one entry.
    responses.write_text(
        "version: 1\n"
        "entries:\n"
        "  - step_id: alpha\n"
        "    response: ok\n",
        encoding="utf-8",
    )
    result = _run([str(target), "--responses-file", str(responses)])
    assert result.returncode == 1
    assert "Responses file exhausted at step" in result.stderr
    assert "(expecting: step response)" in result.stderr


def test_scripted_unused_entries_guard(tmp_path: Path) -> None:
    """Responses file longer than workflow needs: exit 1 with unused-entries banner."""
    workflow_dir = tmp_path / "wf"
    workflow_dir.mkdir()
    target = workflow_dir / "canonical.yaml"
    shutil.copy(CANONICAL_FIXTURE, target)
    responses = tmp_path / "too_long.yaml"
    responses.write_text(
        "version: 1\n"
        "entries:\n"
        "  - step_id: alpha\n"
        "    response: ok\n"
        "  - step_id: bravo\n"
        "    response: ok\n"
        "  - step_id: charlie\n"
        "    response: ok\n"
        "  - step_id: extra\n"
        "    response: not_consumed\n",
        encoding="utf-8",
    )
    result = _run([str(target), "--responses-file", str(responses)])
    assert result.returncode == 1
    assert "unused entries after workflow completion" in result.stderr
    assert "1" in result.stderr  # one unused entry


def test_interactive_mode_unchanged(tmp_path: Path) -> None:
    """Regression: without --responses-file, interactive stdin-piped mode
    still works identically to the pre-refactor behavior."""
    target = tmp_path / "canonical.yaml"
    shutil.copy(CANONICAL_FIXTURE, target)
    result = _run([str(target)], input="ok\nok\nok\n")
    assert result.returncode == 0, result.stderr
    assert "alpha" in result.stdout
    assert "Workflow complete" in result.stdout
