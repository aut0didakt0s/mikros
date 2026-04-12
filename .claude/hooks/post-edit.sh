#!/usr/bin/env bash
# mikrós PostToolUse hook for Write and Edit.
#
# Runs a fast guard (lint + type-check) on the edited file and then invokes
# loc-budget.sh. Exit 2 blocks the tool call, surfacing the error back to
# the model as immediate feedback.
#
# PostToolUse hooks receive input via stdin as JSON:
#   { "tool_name": "Write", "tool_input": { "file_path": "...", ... }, ... }

set -e

EDITED=$(jq -r '.tool_input.file_path // empty' 2>/dev/null || true)

# No path reported → nothing to check (some tool calls don't carry a file path)
if [ -z "$EDITED" ]; then
  exit 0
fi

# If the file doesn't exist (e.g. deleted by the tool call), skip language checks.
if [ -f "$EDITED" ]; then
  case "$EDITED" in
    *.py)
      ruff check "$EDITED" >&2 || exit 2
      mypy "$EDITED" >&2 || exit 2
      ;;
    *.ts|*.tsx|*.js|*.jsx)
      # Lightweight syntax check. Project can override with stricter commands.
      node --check "$EDITED" 2>/dev/null || true
      ;;
  esac
fi

# Always enforce the LOC budget.
if [ -x .claude/skills/simplicity-guard/scripts/loc-budget.sh ]; then
  bash .claude/skills/simplicity-guard/scripts/loc-budget.sh || exit 2
fi

exit 0
