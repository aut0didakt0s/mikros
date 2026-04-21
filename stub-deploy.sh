#!/usr/bin/env bash
# stub-deploy.sh — Validate the MCP stub module for Prefect Horizon deployment.
# Horizon settings (web UI): entrypoint=mcp_stub/main.py:mcp, auth=off
# Usage: ./stub-deploy.sh [--inspect]
set -euo pipefail

ok()   { echo -e "\033[0;32m[OK]\033[0m $1"; }
fail() { echo -e "\033[0;31m[FAIL]\033[0m $1"; exit 1; }

[ -f pyproject.toml ] || fail "pyproject.toml not found"
ok "pyproject.toml found"
[ -f mcp_stub/main.py ] || fail "mcp_stub/main.py not found"
ok "mcp_stub/main.py found"
[ -f mcp_stub/tools.py ] || fail "mcp_stub/tools.py not found"
ok "mcp_stub/tools.py found"

if [[ "${1:-}" == "--inspect" ]]; then
    command -v fastmcp &>/dev/null || fail "fastmcp CLI not found"
    echo "" && fastmcp inspect mcp_stub/main.py:mcp
fi

echo ""
echo "Stub module is Horizon-compatible. Deploy steps:"
echo "  1. Push to GitHub"
echo "  2. Visit https://horizon.prefect.io, sign in with GitHub"
echo "  3. Select repo, set entrypoint: mcp_stub/main.py:mcp"
echo "  4. Auth: OFF (stub is intentionally unauthenticated)"
echo "  5. Click Deploy Server, capture the URL"
echo "  6. Set GitHub secret MCP_STUB_URL to the captured URL"
