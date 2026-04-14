"""Smoke-test a mikros MCP endpoint: verify expected workflows are present.

Usage:
    python3 scripts/smoke_endpoint.py <url-or-local> --expected name1 [name2 ...]
        [--workflow-dir PATH]

<url-or-local>: an http(s):// URL -> FastMCP HTTP Client path.
                Anything else (e.g. "local") -> in-memory create_app path.

Exits 0 iff every expected name is present in list_workflows response.
Exits non-zero with a one-line error on connection failure, malformed
response, or any expected name missing.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def _extract_names(payload):
    """Pull workflow names out of a list_workflows response. Raise ValueError if malformed."""
    if not isinstance(payload, dict):
        raise ValueError(f"expected dict, got {type(payload).__name__}")
    wfs = payload.get("workflows")
    if not isinstance(wfs, list):
        raise ValueError("missing or non-list 'workflows' field")
    names = []
    for w in wfs:
        if not isinstance(w, dict) or "name" not in w:
            raise ValueError("workflow entry missing 'name'")
        names.append(w["name"])
    return names


async def _fetch_names_http(url: str):
    from fastmcp import Client  # type: ignore[attr-defined]

    async with Client(url) as client:
        result = await client.call_tool("list_workflows", {})
    data = getattr(result, "data", None)
    if data is None:
        data = getattr(result, "structured_content", None)
    return _extract_names(data)


def _fetch_names_local(workflow_dir: Path | None):
    from mikros_server import create_app

    mcp = create_app(workflow_dir=workflow_dir)
    result = asyncio.run(mcp.call_tool("list_workflows", {}))
    return _extract_names(getattr(result, "structured_content", None))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("target", help="http(s):// URL or any non-URL string for local mode")
    p.add_argument("--expected", nargs="+", required=True, help="workflow names that must be present")
    p.add_argument("--workflow-dir", default=None, help="local mode: YAML directory (default: bundled)")
    args = p.parse_args(argv)

    is_http = args.target.startswith(("http://", "https://"))
    try:
        if is_http:
            names = asyncio.run(_fetch_names_http(args.target))
        else:
            wf_dir = Path(args.workflow_dir) if args.workflow_dir else None
            names = _fetch_names_local(wf_dir)
    except Exception as e:
        print(f"ERROR: {args.target}: {e}", file=sys.stderr)
        return 1

    missing = [n for n in args.expected if n not in names]
    if missing:
        print(
            f"ERROR: missing expected workflow(s) at {args.target}: {', '.join(missing)} "
            f"(present: {', '.join(sorted(names)) or 'none'})",
            file=sys.stderr,
        )
        return 1
    print(f"OK: all {len(args.expected)} expected workflow(s) present at {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
