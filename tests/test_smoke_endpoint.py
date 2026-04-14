"""Tests for scripts/smoke_endpoint.py against a locally-booted app."""

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "smoke_endpoint.py"


def _run(*args):
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{REPO}{os.pathsep}{existing}" if existing else str(REPO)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=env,
    )


def test_smoke_success_local():
    r = _run("local", "--expected", "example")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "OK" in r.stdout


def test_smoke_missing_workflow_fails():
    r = _run("local", "--expected", "definitely_not_a_real_workflow")
    assert r.returncode != 0
    assert "missing" in r.stderr.lower()
