"""CLI entry point for the megálos dry-run inspector.

Step through a workflow without calling an LLM. T01 bootstraps the process
(env-var discipline, production workflow loader reuse via ``create_app``)
and stops at "Bootstrap OK"; the REPL loop lands in T02.
"""

# 1. Env var set BEFORE any megalos_server.* import.
import os

os.environ.setdefault("MEGALOS_DB_PATH", ":memory:")

# 2. Stdlib imports.
import argparse
import sys
from pathlib import Path

import yaml

# 3. megalos_server import — only create_app, nothing else.
from megalos_server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="megalos-dryrun",
        description="Step through a megálos workflow interactively, without calling an LLM.",
    )
    parser.add_argument("workflow", type=Path, help="Path to workflow YAML file")
    parser.add_argument(
        "--context",
        default="",
        help="Initial context string passed to start_workflow",
    )
    args = parser.parse_args()

    # Step 1 — Resolve + check target path.
    target: Path = args.workflow.resolve()
    if not target.exists():
        print(f"Workflow file not found: {target}", file=sys.stderr)
        sys.exit(1)

    # Step 2 — Read target, extract workflow name.
    try:
        target_doc = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        print(f"Target file {target} is not valid YAML: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(target_doc, dict) or "name" not in target_doc:
        print(f"Target file {target} has no 'name' field.", file=sys.stderr)
        sys.exit(1)
    target_name = target_doc["name"]

    # Step 3 — Load workflows via create_app with Approach E framing.
    try:
        mcp = create_app(workflow_dir=target.parent)
    except Exception as e:
        print(
            f"Failed to load workflows from {target.parent}:\n"
            f"  {e}\n\n"
            f"Note: dry-run loads all *.yaml files in the parent directory "
            f"(required for sub-workflow 'call' target resolution). If the error "
            f"above names a file other than {target.name}, a sibling workflow "
            f"has a problem — fix it, or move {target.name} to a directory "
            f"containing only it and its call targets.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Step 4 — Verify target in loaded map.
    workflows = mcp._megalos_workflows  # type: ignore[attr-defined]
    if target_name not in workflows:
        print(
            f"Workflow name '{target_name}' from {target} was not loaded. "
            f"Loaded names: {sorted(workflows)}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Step 5 — Bootstrap-OK handoff.
    print(f"Bootstrap OK: workflow '{target_name}' loaded from {target.parent}")
    sys.exit(0)


if __name__ == "__main__":
    main()
