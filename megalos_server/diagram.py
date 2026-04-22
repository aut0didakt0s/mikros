"""Render workflow YAML as a Mermaid ``flowchart TD`` diagram.

Static analyzer — no runtime, no registry, no side effects. Takes a
workflow YAML path, returns a string suitable for pasting into a
```mermaid fenced code block on GitHub.

Current scope: sequential step-to-step rendering, branch edges with
condition labels, optional ``default_branch`` fallback edges, a
visually distinct node shape for ``mcp_tool_call`` steps, a dotted
gating edge from the source step to any step carrying a ``precondition``
(``when_equals`` or ``when_present``), and named references to child
workflows invoked via ``call`` (an empty ``subgraph <child>\\nend`` block
plus a labeled ``|"calls"|`` edge from the parent step). Child internals
are never inlined — pointing ``render()`` at the child YAML is how a
reader inspects its steps. CLI wiring is handled in a later slice.
"""

from pathlib import Path
from typing import Any

from .schema import validate_workflow


# Error code emitted by validate_workflow when an mcp_tool_call workflow is
# loaded without a registry. We tolerate this specific error so the diagram
# module stays registry-free; every other validation error is fatal.
_REGISTRY_REQUIRED_CODE = "mcp_tool_call_registry_required"


def _escape_label(text: str) -> str:
    """Escape a label for Mermaid's quoted-string form.

    Mermaid's ``id["label"]`` and ``-->|"label"|`` forms tolerate most
    punctuation; the one character that breaks parsing is the literal
    double-quote. Replace with ``&quot;`` — the stable Mermaid
    convention accepted by GitHub's renderer. Used for both node
    labels and edge labels.
    """
    return text.replace('"', "&quot;")


def _load_doc(workflow_path: str | Path) -> dict[str, Any]:
    """Validate + parse workflow YAML, tolerating the registry-required error.

    See module docstring for rationale — diagram rendering must work on
    ``mcp_tool_call`` workflows without dragging registry plumbing into
    this module. Any other validation failure raises ``ValueError``.
    """
    errors, doc = validate_workflow(str(workflow_path), registry=None)
    if errors and doc is not None:
        non_registry = [e for e in errors if _REGISTRY_REQUIRED_CODE not in e]
        if non_registry:
            raise ValueError(non_registry[0])
    elif errors:
        raise ValueError(errors[0])
    assert doc is not None
    return doc


def _node_line(step: dict[str, Any]) -> str:
    """Render a single node declaration.

    ``mcp_tool_call`` steps use Mermaid's subroutine shape
    (``id[["label"]]``) to signal "delegates to an external tool".
    All other steps use the pinned rectangle shape (``id["label"]``).
    """
    sid = step["id"]
    label = _escape_label(step["title"])
    if step.get("action") == "mcp_tool_call":
        return f'    {sid}[["{label}"]]'
    return f'    {sid}["{label}"]'


def _edge_lines(step: dict[str, Any], next_step: dict[str, Any] | None) -> list[str]:
    """Render outgoing edges for a single step.

    - ``branches`` present → one labeled edge per entry, plus an
      unlabeled edge to ``default_branch`` when that field is set.
      The linear fall-through edge is suppressed (branches ARE the
      outgoing edges).
    - ``branches`` absent → one linear edge to the next step in the
      ``steps`` list, or nothing if this is the last step.
    """
    sid = step["id"]
    branches = step.get("branches")
    if branches:
        lines = [
            f'    {sid} -->|"{_escape_label(b["condition"])}"| {b["next"]}'
            for b in branches
        ]
        default_branch = step.get("default_branch")
        if default_branch:
            lines.append(f"    {sid} --> {default_branch}")
        return lines
    if next_step is None:
        return []
    return [f"    {sid} --> {next_step['id']}"]


def _format_condition_value(value: Any) -> str:
    """Render a ``when_equals`` value for a Mermaid edge label.

    Numbers, bools, and ``None`` render bare (``42``, ``true``, ``null``);
    every other type renders quoted with D029 ``&quot;`` escaping so the
    label survives Mermaid's double-quote-delimited edge-label form.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return f'&quot;{_escape_label(str(value))}&quot;'


def _precondition_edge(step: dict[str, Any]) -> str | None:
    """Render a dotted gating edge for a step carrying a ``precondition``.

    Emits ``<source_sid> -. "<summary>" .-> <gated_sid>`` where the source
    id is the second dot-segment of the ``step_data.<sid>[.<field>...]``
    ref path. Returns ``None`` for steps without a precondition. The
    linear/branch edges produced by ``_edge_lines`` continue to emit
    unchanged; the dotted edge is additive (rendering 1 from the decision
    register).
    """
    pre = step.get("precondition")
    if not pre:
        return None
    if "when_equals" in pre:
        ref = pre["when_equals"]["ref"]
        rendered = _format_condition_value(pre["when_equals"]["value"])
        tail = f"== {rendered}"
    else:
        ref = pre["when_present"]
        tail = "present"
    segments = ref.split(".")
    source_sid, last_seg = segments[1], segments[-1]
    return f'    {source_sid} -. "when {last_seg} {tail}" .-> {step["id"]}'


def _call_references(steps: list[dict[str, Any]]) -> list[str]:
    """Render empty ``subgraph`` blocks for every distinct ``call`` target.

    A step with ``call: <child>`` delegates to a separate workflow YAML;
    the diagram references it by name and never inlines its steps. Same
    child called N times emits exactly one reference block (dedupe);
    per-call edges are emitted per-step by ``_call_edge``. Insertion
    order of first occurrence is preserved for deterministic output.
    """
    seen: dict[str, None] = {}
    for step in steps:
        target = step.get("call") if isinstance(step, dict) else None
        if target and target not in seen:
            seen[target] = None
    lines: list[str] = []
    for target in seen:
        lines.append(f"subgraph {target}")
        lines.append("end")
    return lines


def _call_edge(step: dict[str, Any]) -> str | None:
    """Render the ``|"calls"|`` edge for a step carrying ``call: <child>``.

    Additive to linear / branch / default_branch / precondition edges.
    Returns ``None`` when the step does not declare ``call``.
    """
    target = step.get("call")
    if not target:
        return None
    return f'    {step["id"]} -->|"calls"| {target}'


def render(workflow_path: str | Path) -> str:
    """Render a workflow YAML as a Mermaid ``flowchart TD`` block.

    Returns a string starting with the dialect declaration
    (``flowchart TD``) followed by node declarations, any child-workflow
    reference blocks (one per distinct ``call`` target, deduped), and
    then all edges (linear, branch, default-branch, dotted precondition,
    and ``|"calls"|`` call edges). Raises ``ValueError`` if the workflow
    fails validation.
    """
    doc = _load_doc(workflow_path)
    steps = doc["steps"]

    lines: list[str] = ["flowchart TD", ""]
    for step in steps:
        lines.append(_node_line(step))
    lines.extend(_call_references(steps))
    for index, step in enumerate(steps):
        next_step = steps[index + 1] if index + 1 < len(steps) else None
        lines.extend(_edge_lines(step, next_step))
        dotted = _precondition_edge(step)
        if dotted is not None:
            lines.append(dotted)
        call = _call_edge(step)
        if call is not None:
            lines.append(call)
    return "\n".join(lines)


def main() -> None:
    """CLI entry point: render a workflow YAML as Mermaid to stdout.

    Imports ``argparse`` and ``sys`` locally so programmatic callers of
    ``render()`` (tests, other tools) do not pay the argparse parse-time
    cost at import time — this module is library-first, CLI-second.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m megalos_server.diagram",
        description="Render a workflow YAML as a Mermaid flowchart TD block.",
    )
    parser.add_argument("workflow", help="path to workflow YAML file")
    args = parser.parse_args()
    try:
        print(render(args.workflow))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
