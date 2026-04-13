"""CLI entry point: validate a workflow YAML file."""

import sys

from server.schema import validate_workflow


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 -m server.validate <path/to/workflow.yaml>", file=sys.stderr)
        sys.exit(1)
    errors = validate_workflow(sys.argv[1])
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print("Valid.")
    sys.exit(0)


if __name__ == "__main__":
    main()
