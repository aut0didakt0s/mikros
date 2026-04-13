"""mikros MCP server — workflow-driven coding assistant."""

import sys
from pathlib import Path

# FastMCP's CLI loads this file directly (not via python -m), so the
# parent dir isn't automatically on sys.path. Add it so `server.*`
# absolute imports resolve. Same pattern used in tests/conftest.py.
_app_root = str(Path(__file__).resolve().parent.parent)
if _app_root not in sys.path:
    sys.path.insert(0, _app_root)

from fastmcp import FastMCP  # noqa: E402
from server.schema import load_workflow  # noqa: E402
from server import state  # noqa: E402
from server.tools import register_tools  # noqa: E402

mcp = FastMCP("mikros")

# Load workflows at import time — fail fast on bad YAML.
WORKFLOWS_DIR = Path(__file__).parent / "workflows"
WORKFLOWS: dict[str, dict] = {}

for yaml_path in WORKFLOWS_DIR.glob("*.yaml"):
    wf = load_workflow(str(yaml_path))
    WORKFLOWS[wf["name"]] = wf

if not WORKFLOWS:
    raise RuntimeError(f"No workflow YAML files found in {WORKFLOWS_DIR}")

state.expire_sessions(24)
register_tools(mcp, WORKFLOWS)

if __name__ == "__main__":
    # FastMCP reads FASTMCP_HOST / FASTMCP_PORT from env automatically.
    mcp.run(transport="streamable-http")  # type: ignore[arg-type]
