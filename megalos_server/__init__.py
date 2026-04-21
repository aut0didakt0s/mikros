"""megalos MCP server — factory for creating configured FastMCP app instances."""

from pathlib import Path

from fastmcp import FastMCP

from megalos_server.mcp_registry import Registry
from megalos_server.middleware import (
    CallerIdentityMiddleware,
    RateLimitMiddleware,
    ValidationErrorMiddleware,
)
from megalos_server.ratelimit import RateLimitConfig, RateLimiter
from megalos_server.schema import load_workflow, validate_workflow_calls
from megalos_server.tools import register_tools


def _load_registry(registry_path: str | Path | None) -> Registry | None:
    """Load the MCP server registry if a YAML file is present.

    Absence is non-fatal: workflows that contain ``mcp_tool_call`` steps
    fail schema validation (T01's ``mcp_tool_call_registry_required`` rule)
    when the registry is absent, so the executor never needs to fall back
    on a missing registry in a well-formed deployment.
    """
    path = Path(registry_path) if registry_path else Path("mcp_servers.yaml")
    if not path.exists():
        return None
    return Registry.from_yaml(path)


def create_app(
    workflow_dir: str | Path | None = None,
    registry_path: str | Path | None = None,
) -> FastMCP:
    """Create a FastMCP app with workflows loaded from `workflow_dir`.

    Defaults to the bundled `workflows/` directory next to this package.
    Raises RuntimeError if the resolved directory contains no YAML files.

    ``registry_path`` points at an ``mcp_servers.yaml`` describing the
    external MCP servers ``mcp_tool_call`` steps may dispatch to. Defaults
    to ``./mcp_servers.yaml`` (CWD). If the file is absent the registry is
    held as ``None``; schema validation will reject any workflow that
    contains ``mcp_tool_call`` steps before the executor can reach one.
    """
    wf_path = Path(workflow_dir) if workflow_dir else Path(__file__).parent / "workflows"
    registry = _load_registry(registry_path)
    workflows: dict[str, dict] = {}
    for yaml_path in wf_path.glob("*.yaml"):
        wf = load_workflow(str(yaml_path), registry=registry)
        workflows[wf["name"]] = wf
    # Cross-workflow validation: call targets exist and the call graph is acyclic.
    call_errors = validate_workflow_calls(workflows)
    if call_errors:
        raise ValueError(call_errors[0])
    if not workflows:
        raise RuntimeError(f"No workflow YAML files found in {wf_path}")
    mcp = FastMCP("megalos")
    mcp.add_middleware(ValidationErrorMiddleware())  # type: ignore[attr-defined]
    mcp.add_middleware(CallerIdentityMiddleware())  # type: ignore[attr-defined]
    # Rate-limit middleware sits AFTER CallerIdentityMiddleware so the
    # caller_identity_var is populated before the limiter consults it on
    # the session axis. Primitive is constructed from env-var config.
    limiter = RateLimiter(RateLimitConfig.from_env())
    mcp.add_middleware(RateLimitMiddleware(limiter))  # type: ignore[attr-defined]
    register_tools(mcp, workflows, registry=registry)
    # Attach workflows dict for introspection / test mutation. Underscore = private.
    mcp._megalos_workflows = workflows  # type: ignore[attr-defined]
    mcp._megalos_registry = registry  # type: ignore[attr-defined]
    return mcp


__all__ = ["create_app"]
