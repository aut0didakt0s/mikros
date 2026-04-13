"""mikros MCP server — workflow-driven coding assistant."""

from pathlib import Path
from fastmcp import FastMCP
from .schema import load_workflow
from .tools import register_tools

mcp = FastMCP("mikros")

# Load workflows at import time — fail fast on bad YAML.
WORKFLOWS_DIR = Path(__file__).parent / "workflows"
WORKFLOWS: dict[str, dict] = {}

for yaml_path in WORKFLOWS_DIR.glob("*.yaml"):
    wf = load_workflow(str(yaml_path))
    WORKFLOWS[wf["name"]] = wf

if not WORKFLOWS:
    raise RuntimeError(f"No workflow YAML files found in {WORKFLOWS_DIR}")

register_tools(mcp, WORKFLOWS)

if __name__ == "__main__":
    # FastMCP reads FASTMCP_HOST / FASTMCP_PORT from env automatically.
    mcp.run(transport="streamable-http")  # type: ignore[arg-type]
