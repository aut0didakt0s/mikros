#!/usr/bin/env bash
# Fail if the active worktree's diff exceeds the LOC budget declared for the
# current task in .mikros/STATE.md. Exit 0 if there is no active task or the
# diff is within budget. Exit 2 to signal a guard failure (blocks the tool
# call when run as a Claude Code PostToolUse hook).
set -e

STATE_FILE=".mikros/STATE.md"
if [ ! -f "$STATE_FILE" ]; then
  # No active mikrós task — nothing to enforce.
  exit 0
fi

# Read loc_budget from STATE.md; default 300 if not present.
BUDGET=$(grep -E '^loc_budget:' "$STATE_FILE" 2>/dev/null | head -n 1 | sed 's/loc_budget:[[:space:]]*//')
if [ -z "$BUDGET" ]; then
  BUDGET=300
fi

# Count insertions from git diff shortstat. Tolerate a missing count (empty diff).
SHORTSTAT=$(git diff --shortstat 2>/dev/null || true)
ADDED=$(echo "$SHORTSTAT" | grep -oE '[0-9]+ insertions?' | awk '{print $1}')
ADDED=${ADDED:-0}

if [ "$ADDED" -gt "$BUDGET" ]; then
  echo "loc-budget: $ADDED lines added exceeds budget of $BUDGET — split the task" >&2
  exit 2
fi

exit 0
