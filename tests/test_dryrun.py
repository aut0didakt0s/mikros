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
