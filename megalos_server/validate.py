"""CLI entry point: validate a workflow YAML file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .mcp_registry import Registry, RegistryLoadError
from .schema import validate_workflow


def _discover_registry(workflow_path: str) -> Path | None:
    """Look for ``mcp_servers.yaml`` next to the workflow, then in cwd.

    First hit wins. Returns the path or ``None`` if neither location has one.
    """
    wf_dir = Path(workflow_path).resolve().parent
    candidate = wf_dir / "mcp_servers.yaml"
    if candidate.is_file():
        return candidate
    cwd_candidate = Path.cwd() / "mcp_servers.yaml"
    if cwd_candidate.is_file():
        return cwd_candidate
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m megalos_server.validate",
        description="Validate a workflow YAML file.",
    )
    parser.add_argument("workflow", help="path to workflow YAML file")
    parser.add_argument(
        "--registry",
        metavar="PATH",
        help=(
            "path to mcp_servers.yaml. If omitted, the validator looks for "
            "mcp_servers.yaml next to the workflow and then in the current "
            "working directory."
        ),
    )
    parser.add_argument(
        "--diagram",
        action="store_true",
        help=(
            "also emit a Mermaid flowchart of the workflow after successful "
            "validation. No effect on validation failure."
        ),
    )
    args = parser.parse_args()

    registry: Registry | None = None
    if args.registry:
        registry_path = Path(args.registry)
        try:
            registry = Registry.from_yaml(registry_path)
        except RegistryLoadError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        discovered = _discover_registry(args.workflow)
        if discovered is not None:
            try:
                registry = Registry.from_yaml(discovered)
            except RegistryLoadError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                sys.exit(1)

    errors, _ = validate_workflow(args.workflow, registry=registry)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
    print("Valid.")
    if args.diagram:
        from .diagram import render
        print()
        print(render(args.workflow))
    sys.exit(0)


if __name__ == "__main__":
    main()
