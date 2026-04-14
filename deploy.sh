#!/usr/bin/env bash
# deploy.sh — Validate megalos MCP server for Prefect Horizon deployment.
# Horizon settings (web UI): entrypoint=megalos_server/main.py:mcp, auth=off
# Result URL: https://Megalos.fastmcp.app/mcp
# Usage: ./deploy.sh [--inspect]
set -euo pipefail

ok()   { echo -e "\033[0;32m[OK]\033[0m $1"; }
fail() { echo -e "\033[0;31m[FAIL]\033[0m $1"; exit 1; }

[ -f pyproject.toml ] || fail "pyproject.toml not found"
ok "pyproject.toml found"
[ -f megalos_server/main.py ] || fail "megalos_server/main.py not found"
ok "megalos_server/main.py found"
count=$(find megalos_server/workflows -name '*.yaml' 2>/dev/null | wc -l | tr -d ' ')
[ "$count" -gt 0 ] || fail "No workflow YAML files in megalos_server/workflows/"
ok "$count workflow(s) found"

if [[ "${1:-}" == "--inspect" ]]; then
    command -v fastmcp &>/dev/null || fail "fastmcp CLI not found"
    echo "" && fastmcp inspect megalos_server/main.py:mcp
fi

echo ""
echo "Repo is Horizon-compatible. Deploy steps:"
echo "  1. Push to GitHub"
echo "  2. Visit https://horizon.prefect.io, sign in with GitHub"
echo "  3. Select repo, set entrypoint: megalos_server/main.py:mcp"
echo "  4. Click Deploy Server"
echo "  5. URL: https://Megalos.fastmcp.app/mcp"
