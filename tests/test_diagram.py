"""Tests for megalos_server.diagram — workflow renderer.

Covers linear step-to-step rendering, branch edges with condition
labels, ``default_branch`` fallback edges, and a visually distinct
node shape for ``mcp_tool_call`` steps. Parametrized so later slices
can add fixtures (preconditions, sub-workflow ``call``) without
restructuring.
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


# --- Branch rendering -------------------------------------------------------

BRANCH_FIXTURES = [
    FIXTURES / "demo_branching.yaml",
]


@pytest.mark.parametrize("fixture", BRANCH_FIXTURES, ids=lambda p: p.name)
def test_branch_conditions_appear_as_edge_labels(fixture: Path) -> None:
    doc = yaml.safe_load(fixture.read_text())
    out = render(fixture)
    for step in doc["steps"]:
        for branch in step.get("branches", []):
            raw = branch["condition"]
            escaped = raw.replace('"', "&quot;")
            assert raw in out or escaped in out


@pytest.mark.parametrize("fixture", BRANCH_FIXTURES, ids=lambda p: p.name)
def test_default_branch_emits_unlabeled_edge(fixture: Path) -> None:
    doc = yaml.safe_load(fixture.read_text())
    out = render(fixture)
    for step in doc["steps"]:
        if "default_branch" in step:
            assert f"{step['id']} --> {step['default_branch']}" in out


@pytest.mark.parametrize("fixture", BRANCH_FIXTURES, ids=lambda p: p.name)
def test_branched_step_suppresses_linear_fall_through(fixture: Path) -> None:
    """A step with ``branches`` must not also emit a linear
    ``id --> next_step_id`` edge; branches are the outgoing edges."""
    doc = yaml.safe_load(fixture.read_text())
    out = render(fixture)
    steps = doc["steps"]
    for index, step in enumerate(steps):
        if "branches" not in step:
            continue
        if index + 1 >= len(steps):
            continue
        next_id = steps[index + 1]["id"]
        linear = f"{step['id']} --> {next_id}"
        # Labeled edges contain the pipe delimiter; the linear form does not.
        assert linear not in out or next_id == step.get("default_branch")


# --- mcp_tool_call visual distinction ---------------------------------------

MCP_FIXTURES = [
    FIXTURES / "mcp_tool_call" / "success_then_read.yaml",
]


@pytest.mark.parametrize("fixture", MCP_FIXTURES, ids=lambda p: p.name)
def test_mcp_tool_call_steps_have_distinct_marker(fixture: Path) -> None:
    """mcp_tool_call steps render in a shape distinct from LLM rectangles.
    Asserts presence of EITHER preferred marker adjacent to the step id
    (subroutine ``[[`` or hexagon ``{{``) without pinning which one — the
    specific choice is recorded in the decisions register."""
    doc = yaml.safe_load(fixture.read_text())
    out = render(fixture)
    for step in doc["steps"]:
        if step.get("action") == "mcp_tool_call":
            sid = step["id"]
            assert f"{sid}[[" in out or f"{sid}{{{{" in out


@pytest.mark.parametrize("fixture", MCP_FIXTURES, ids=lambda p: p.name)
def test_non_mcp_steps_keep_rectangle_shape(fixture: Path) -> None:
    """LLM steps in an mcp_tool_call fixture still render as plain
    rectangles — the distinct shape is scoped to mcp_tool_call only."""
    doc = yaml.safe_load(fixture.read_text())
    out = render(fixture)
    for step in doc["steps"]:
        if step.get("action") == "mcp_tool_call":
            continue
        sid = step["id"]
        assert f'{sid}["' in out
        assert f"{sid}[[" not in out
