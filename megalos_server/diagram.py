"""Render workflow YAML as a Mermaid ``flowchart TD`` diagram.

Static analyzer — no runtime, no registry, no side effects. Takes a
workflow YAML path, returns a string suitable for pasting into a
```mermaid fenced code block on GitHub.

Current scope: sequential step-to-step rendering, branch edges with
condition labels, optional ``default_branch`` fallback edges, and a
visually distinct node shape for ``mcp_tool_call`` steps. Preconditions,
sub-workflow ``call`` subgraph references, and CLI wiring are handled
in later slices.
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


def render(workflow_path: str | Path) -> str:
    """Render a workflow YAML as a Mermaid ``flowchart TD`` block.

    Returns a string starting with the dialect declaration
    (``flowchart TD``) followed by node declarations and edges.
    Raises ``ValueError`` if the workflow fails validation.
    """
    doc = _load_doc(workflow_path)
    steps = doc["steps"]

    lines: list[str] = ["flowchart TD", ""]
    for step in steps:
        lines.append(_node_line(step))
    for index, step in enumerate(steps):
        next_step = steps[index + 1] if index + 1 < len(steps) else None
        lines.extend(_edge_lines(step, next_step))
    return "\n".join(lines)
