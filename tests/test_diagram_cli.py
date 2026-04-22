"""Tests for the ``python -m megalos_server.diagram`` CLI entry point.

Uses subprocess invocation (not ``sys.argv`` monkey-patching) so the
test exercises the real ``-m`` entry point the author-facing command
produces. Three cases: success on the canonical fixture, failure on a
missing file, and ``--help`` exiting zero.
"""

import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "workflows"


def _run_diagram_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "megalos_server.diagram", *args],
        capture_output=True,
        text=True,
    )


def test_diagram_cli_emits_flowchart_for_canonical_fixture() -> None:
    result = _run_diagram_cli(str(FIXTURES / "canonical.yaml"))
    assert result.returncode == 0
    assert result.stdout.startswith("flowchart TD")
    # Every canonical step id should appear in output.
    for sid in ("alpha", "bravo", "charlie"):
        assert sid in result.stdout


def test_diagram_cli_fails_on_missing_file() -> None:
    result = _run_diagram_cli(str(FIXTURES / "definitely-not-a-real-workflow.yaml"))
    assert result.returncode != 0
    assert "ERROR" in result.stderr


def test_diagram_cli_help_exits_zero() -> None:
    result = _run_diagram_cli("--help")
    assert result.returncode == 0
    assert "workflow" in result.stdout
