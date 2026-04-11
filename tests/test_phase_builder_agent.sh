#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

FILE=".claude/agents/phase-builder.md"

assert_file_exists "$FILE" "phase-builder.md exists"

if ! python3 tests/validate_frontmatter.py "$FILE" name description tools isolation maxTurns skills 2>/dev/null; then
  TESTS_RUN=$((TESTS_RUN + 1))
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: phase-builder.md frontmatter missing required keys" >&2
fi

assert_file_contains "$FILE" "phase-builder"                "name is phase-builder"
assert_file_contains "$FILE" "worktree"                     "isolation is worktree"
assert_file_contains "$FILE" "maxTurns: 30"                 "maxTurns is 30"
assert_file_contains "$FILE" "simplicity-guard"             "preloads simplicity-guard skill"
assert_file_contains "$FILE" "pre-loaded"                   "mentions pre-loaded dispatch"
assert_file_contains "$FILE" "Must-haves"                   "mentions Must-haves"
assert_file_contains "$FILE" "### Files modified"           "required summary format: Files modified"
assert_file_contains "$FILE" "### Decisions"                "required summary format: Decisions"
assert_file_contains "$FILE" "### Verification output"      "required summary format: Verification output"

test_summary
