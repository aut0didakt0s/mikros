#!/usr/bin/env bash
# Fail if the working tree's diff exceeds the LOC budget.
# Budget source (in priority order):
#   1. MEGALOS_LOC_BUDGET environment variable
#   2. loc_budget field in .megalos/STATE.md
#   3. Default of 300
# Exit 0 = within budget or no budget to enforce.
# Exit 2 = budget exceeded (blocks the tool call when used as a hook).
set -e

# 1. Try environment variable first.
BUDGET="${MEGALOS_LOC_BUDGET:-}"

# 2. Fall back to .megalos/STATE.md if env var not set.
if [ -z "$BUDGET" ]; then
  STATE_FILE=".megalos/STATE.md"
  if [ -f "$STATE_FILE" ]; then
    BUDGET=$(grep -E '^loc_budget:' "$STATE_FILE" 2>/dev/null | head -n 1 | sed 's/loc_budget:[[:space:]]*//')
  fi
fi

# 3. Default to 300.
BUDGET="${BUDGET:-300}"

# Count insertions from git diff shortstat.
SHORTSTAT=$(git diff --shortstat 2>/dev/null || true)
ADDED=$(echo "$SHORTSTAT" | grep -oE '[0-9]+ insertions?' | awk '{print $1}')
ADDED=${ADDED:-0}

if [ "$ADDED" -gt "$BUDGET" ]; then
  echo "loc-budget: $ADDED lines added exceeds budget of $BUDGET — split the task" >&2
  exit 2
fi

exit 0
