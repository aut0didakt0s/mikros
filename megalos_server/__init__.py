"""megalos MCP server — factory for creating configured FastMCP app instances."""

from pathlib import Path

from fastmcp import FastMCP

from megalos_server.middleware import ValidationErrorMiddleware
from megalos_server.schema import load_workflow, validate_workflow_calls
from megalos_server.tools import register_tools


def create_app(workflow_dir: str | Path | None = None) -> FastMCP:
    """Create a FastMCP app with workflows loaded from `workflow_dir`.

    Defaults to the bundled `workflows/` directory next to this package.
    Raises RuntimeError if the resolved directory contains no YAML files.
    """
    wf_path = Path(workflow_dir) if workflow_dir else Path(__file__).parent / "workflows"
    workflows: dict[str, dict] = {}
    for yaml_path in wf_path.glob("*.yaml"):
        wf = load_workflow(str(yaml_path))
        workflows[wf["name"]] = wf
    # Cross-workflow validation: call targets exist and the call graph is acyclic.
    call_errors = validate_workflow_calls(workflows)
    if call_errors:
        raise ValueError(call_errors[0])
    if not workflows:
        raise RuntimeError(f"No workflow YAML files found in {wf_path}")
    mcp = FastMCP("megalos")
    mcp.add_middleware(ValidationErrorMiddleware())  # type: ignore[attr-defined]
    register_tools(mcp, workflows)
    # Attach workflows dict for introspection / test mutation. Underscore = private.
    mcp._megalos_workflows = workflows  # type: ignore[attr-defined]
    return mcp


__all__ = ["create_app"]
