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


# --- Precondition rendering -------------------------------------------------

# skip_continue.yaml picked as the precondition-without-branches fixture:
# three steps, one precondition, no branches — the simplest shape in the
# candidate set {cascade_error, cascade_wrap_child, force_branch_override,
# inject_skipped, revise_unskip, skip_continue}. Verified by reading the
# file before locking in.
PRECONDITION_FIXTURES = [
    FIXTURES / "precondition_with_branches.yaml",
    FIXTURES / "skip_continue.yaml",
]


@pytest.mark.parametrize("fixture", PRECONDITION_FIXTURES, ids=lambda p: p.name)
def test_precondition_steps_have_dotted_edge(fixture: Path) -> None:
    """Any step carrying ``precondition`` triggers a dotted edge
    (Mermaid ``-. … .->``) in the rendered output."""
    doc = yaml.safe_load(fixture.read_text())
    out = render(fixture)
    has_precondition = any(
        isinstance(s, dict) and "precondition" in s for s in doc["steps"]
    )
    if has_precondition:
        assert "-." in out and ".->" in out


@pytest.mark.parametrize("fixture", PRECONDITION_FIXTURES, ids=lambda p: p.name)
def test_precondition_condition_summary_appears_in_label(fixture: Path) -> None:
    """The condition summary (``when <last_seg> == <value>`` or
    ``when <last_seg> present``) appears verbatim somewhere in the
    rendered output for every precondition-carrying step."""
    doc = yaml.safe_load(fixture.read_text())
    out = render(fixture)
    for step in doc["steps"]:
        pre = step.get("precondition") if isinstance(step, dict) else None
        if not pre:
            continue
        if "when_equals" in pre:
            ref = pre["when_equals"]["ref"]
            last_seg = ref.split(".")[-1]
            assert f"when {last_seg} ==" in out
        elif "when_present" in pre:
            ref = pre["when_present"]
            last_seg = ref.split(".")[-1]
            assert f"when {last_seg} present" in out


@pytest.mark.parametrize("fixture", PRECONDITION_FIXTURES, ids=lambda p: p.name)
def test_precondition_dotted_edge_source_matches_ref(fixture: Path) -> None:
    """The dotted edge runs from the ref's source step (second dot-segment
    of the ``step_data.<sid>...`` path) to the gated step."""
    doc = yaml.safe_load(fixture.read_text())
    out = render(fixture)
    for step in doc["steps"]:
        pre = step.get("precondition") if isinstance(step, dict) else None
        if not pre:
            continue
        ref = (
            pre["when_equals"]["ref"] if "when_equals" in pre else pre["when_present"]
        )
        source_sid = ref.split(".")[1]
        gated_sid = step["id"]
        assert f"{source_sid} -." in out
        assert f".-> {gated_sid}" in out


# --- Sub-workflow call rendering --------------------------------------------

# Parents that declare ``call: <child>`` on at least one step. Both are
# single-call parents (call_context_from_parent calls one child; cascade_wrap
# calls one child); the dedupe assertion is a unit-level invariant the
# implementation must still hold.
CALL_FIXTURES = [
    FIXTURES / "call_context_from_parent.yaml",
    FIXTURES / "cascade_wrap_parent.yaml",
]


@pytest.mark.parametrize("fixture", CALL_FIXTURES, ids=lambda p: p.name)
def test_call_step_has_calls_edge(fixture: Path) -> None:
    """Every step with ``call: <child>`` emits a ``|"calls"|`` edge from
    the parent step's id to the child workflow name."""
    doc = yaml.safe_load(fixture.read_text())
    out = render(fixture)
    for step in doc["steps"]:
        if isinstance(step, dict) and "call" in step:
            target = step["call"]
            assert f'{step["id"]} -->|"calls"| {target}' in out


@pytest.mark.parametrize("fixture", CALL_FIXTURES, ids=lambda p: p.name)
def test_call_target_reference_present_exactly_once(fixture: Path) -> None:
    """Each distinct ``call`` target appears exactly once as a child
    reference (either a ``subgraph <target>`` block or a stadium
    ``<target>(("…"))`` node). N calls to the same child still emit one
    reference; the decision register records which shape the
    implementation chose."""
    doc = yaml.safe_load(fixture.read_text())
    out = render(fixture)
    call_targets = {
        step["call"]
        for step in doc["steps"]
        if isinstance(step, dict) and "call" in step
    }
    for target in call_targets:
        subgraph_marker = f"subgraph {target}"
        stadium_marker = f'{target}(("'
        assert out.count(subgraph_marker) + out.count(stadium_marker) == 1


@pytest.mark.parametrize("fixture", CALL_FIXTURES, ids=lambda p: p.name)
def test_child_steps_not_inlined(fixture: Path) -> None:
    """Child workflows are referenced, not inlined. Their own step ids
    must NOT appear as nodes in the parent render. This is the guarantee
    the subgraph/stadium choice exists to provide."""
    doc = yaml.safe_load(fixture.read_text())
    out = render(fixture)
    call_targets = {
        step["call"]
        for step in doc["steps"]
        if isinstance(step, dict) and "call" in step
    }
    for target in call_targets:
        child_path = fixture.parent / f"{target.split('.')[-1]}.yaml"
        if not child_path.exists():
            continue
        child_doc = yaml.safe_load(child_path.read_text())
        for child_step in child_doc["steps"]:
            cid = child_step["id"] if isinstance(child_step, dict) else None
            if cid and cid != target:
                # A child step id should NOT render as a node in the parent.
                assert f'    {cid}["' not in out
                assert f'    {cid}[["' not in out
