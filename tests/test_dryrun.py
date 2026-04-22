"""Subprocess-driven tests for the dry-run CLI bootstrap entry point.

Each test spawns ``python -m megalos_server.dryrun`` as a subprocess so
the __main__ guard and env-var ordering discipline are exercised in the
same shape as production invocation.
"""

import shutil
import subprocess
import sys
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "workflows"
CANONICAL_FIXTURE = FIXTURES_DIR / "canonical.yaml"


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
