"""Render workflow YAML as a Mermaid ``flowchart TD`` diagram.

Static analyzer — no runtime, no registry, no side effects. Takes a
workflow YAML path, returns a string suitable for pasting into a
```mermaid fenced code block on GitHub.

T01 scope: sequential step-to-step rendering only. Branches, preconditions,
sub-workflow ``call`` steps, and the ``mcp_tool_call`` visual distinction
are deliberately OUT OF SCOPE — later tasks handle those.
"""

from pathlib import Path

from .schema import validate_workflow


# Error code emitted by validate_workflow when an mcp_tool_call workflow is
# loaded without a registry. We tolerate this specific error so the diagram
# module stays registry-free; every other validation error is fatal.
_REGISTRY_REQUIRED_CODE = "mcp_tool_call_registry_required"


def _escape_label(text: str) -> str:
    """Escape a node label for Mermaid's quoted-string form.

    Mermaid's ``id["label"]`` form tolerates most punctuation; the one
    character that breaks parsing is the literal double-quote. Replace
    with ``&quot;`` — the stable Mermaid convention accepted by GitHub's
    renderer.
    """
    return text.replace('"', "&quot;")


def _load_doc(workflow_path: str | Path) -> dict:
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


def render(workflow_path: str | Path) -> str:
    """Render a workflow YAML as a Mermaid ``flowchart TD`` block.

    Returns a string starting with the dialect declaration (``flowchart TD``)
    followed by one line per node declaration and one line per edge between
    consecutive steps. Raises ``ValueError`` if the workflow fails validation.
    """
    doc = _load_doc(workflow_path)
    steps = doc["steps"]

    lines: list[str] = ["flowchart TD", ""]
    for step in steps:
        sid = step["id"]
        label = _escape_label(step["title"])
        lines.append(f'    {sid}["{label}"]')
    for prev, curr in zip(steps, steps[1:]):
        lines.append(f"    {prev['id']} --> {curr['id']}")
    return "\n".join(lines)
