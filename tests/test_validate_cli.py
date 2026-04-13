"""Tests for server.validate CLI and validate_workflow function."""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.schema import validate_workflow

WORKFLOWS_DIR = os.path.join(os.path.dirname(__file__), "..", "server", "workflows")


def test_valid_workflow_no_errors():
    """All five built-in workflows pass validation."""
    for name in ("coding", "essay", "blog", "decision", "research"):
        path = os.path.join(WORKFLOWS_DIR, f"{name}.yaml")
        errors = validate_workflow(path)
        assert errors == [], f"{name}.yaml had errors: {errors}"


def test_valid_workflow_cli_exit_0():
    """CLI exits 0 for a valid workflow."""
    path = os.path.join(WORKFLOWS_DIR, "coding.yaml")
    result = subprocess.run(
        [sys.executable, "-m", "server.validate", path],
        capture_output=True, text=True,
        cwd=os.path.join(os.path.dirname(__file__), ".."),
    )
    assert result.returncode == 0


def test_missing_required_fields_exit_1():
    """CLI exits 1 and reports multiple missing top-level fields."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("steps:\n  - id: s1\n    title: T\n    directive_template: D\n    gates: []\n    anti_patterns: []\n")
        f.flush()
        result = subprocess.run(
            [sys.executable, "-m", "server.validate", f.name],
            capture_output=True, text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
    os.unlink(f.name)
    assert result.returncode == 1
    assert "name" in result.stderr
    assert "description" in result.stderr
    assert "category" in result.stderr


def test_bad_step_structure_exit_1():
    """CLI exits 1 when step is missing required keys."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("name: test\ndescription: d\ncategory: c\noutput_format: text\nsteps:\n  - id: s1\n")
        f.flush()
        result = subprocess.run(
            [sys.executable, "-m", "server.validate", f.name],
            capture_output=True, text=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        )
    os.unlink(f.name)
    assert result.returncode == 1
    assert "missing keys" in result.stderr


def test_multiple_errors_reported():
    """validate_workflow collects multiple errors, not just first."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        # Missing name, description, category, output_format + bad step
        f.write("steps:\n  - id: s1\n")
        f.flush()
        errors = validate_workflow(f.name)
    os.unlink(f.name)
    assert len(errors) >= 3  # at least name, description, category missing + step errors
