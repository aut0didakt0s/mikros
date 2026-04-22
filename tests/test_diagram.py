"""Tests for megalos_server.diagram — sequential workflow renderer.

T01 scope covers only linear step-to-step rendering. Parametrized so T02
can add branching fixtures without restructuring.
"""

from pathlib import Path

import pytest
import yaml

from megalos_server.diagram import render


FIXTURES = Path(__file__).parent / "fixtures" / "workflows"


# Second slot is an inline tmp_path fixture (indirect via the `linear_workflow`
# fixture below) because no existing on-disk fixture has ≥3 linear steps with
# no branches / preconditions / call / mcp_tool_call. See T01 plan §Tests.
SEQUENTIAL_FIXTURE_NAMES: list[str] = ["canonical.yaml", "inline_four_steps"]


@pytest.fixture
def linear_workflow(tmp_path: Path) -> Path:
    """A 4-step linear workflow written to a tmp file — satisfies the 'one more
    linear fixture' requirement without adding a permanent fixture file.
    """
    doc = """\
name: inline_linear
description: Inline linear fixture for diagram tests.
category: test
output_format: structured_code
steps:
  - id: one
    title: One
    directive_template: Stub directive for tests.
    gates: [done]
    anti_patterns: [skipping]
  - id: two
    title: Two
    directive_template: Stub directive for tests.
    gates: [done]
    anti_patterns: [skipping]
  - id: three
    title: Three
    directive_template: Stub directive for tests.
    gates: [done]
    anti_patterns: [skipping]
  - id: four
    title: Four
    directive_template: Stub directive for tests.
    gates: [done]
    anti_patterns: [skipping]
"""
    path = tmp_path / "inline_four_steps.yaml"
    path.write_text(doc)
    return path


def _resolve(name: str, linear_workflow: Path) -> Path:
    if name == "inline_four_steps":
        return linear_workflow
    return FIXTURES / name


@pytest.mark.parametrize("fixture_name", SEQUENTIAL_FIXTURE_NAMES)
def test_render_produces_flowchart_header(
    fixture_name: str, linear_workflow: Path
) -> None:
    out = render(_resolve(fixture_name, linear_workflow))
    assert out.startswith("flowchart TD")


@pytest.mark.parametrize("fixture_name", SEQUENTIAL_FIXTURE_NAMES)
def test_render_contains_every_step_id(
    fixture_name: str, linear_workflow: Path
) -> None:
    path = _resolve(fixture_name, linear_workflow)
    doc = yaml.safe_load(path.read_text())
    out = render(path)
    for step in doc["steps"]:
        assert step["id"] in out


@pytest.mark.parametrize("fixture_name", SEQUENTIAL_FIXTURE_NAMES)
def test_render_emits_one_edge_per_consecutive_pair(
    fixture_name: str, linear_workflow: Path
) -> None:
    path = _resolve(fixture_name, linear_workflow)
    doc = yaml.safe_load(path.read_text())
    out = render(path)
    expected_edges = len(doc["steps"]) - 1
    assert out.count(" --> ") == expected_edges
