#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

# .mcp.json must be valid JSON.
assert_file_exists ".mcp.json" ".mcp.json exists"
if ! jq -e . .mcp.json >/dev/null 2>&1; then
  TESTS_RUN=$((TESTS_RUN + 1))
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: .mcp.json is not valid JSON" >&2
fi

# .claude/settings.json must be valid JSON and wire both hooks.
SETTINGS=".claude/settings.json"
assert_file_exists "$SETTINGS" "settings.json exists"
if ! jq -e . "$SETTINGS" >/dev/null 2>&1; then
  TESTS_RUN=$((TESTS_RUN + 1))
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: settings.json is not valid JSON" >&2
fi

TESTS_RUN=$((TESTS_RUN + 1))
if ! jq -e '.hooks.SessionStart' "$SETTINGS" >/dev/null 2>&1; then
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: settings.json missing hooks.SessionStart" >&2
fi

TESTS_RUN=$((TESTS_RUN + 1))
if ! jq -e '.hooks.PostToolUse' "$SETTINGS" >/dev/null 2>&1; then
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: settings.json missing hooks.PostToolUse" >&2
fi

TESTS_RUN=$((TESTS_RUN + 1))
if ! jq -e '.hooks.PreToolUse' "$SETTINGS" >/dev/null 2>&1; then
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: settings.json missing hooks.PreToolUse" >&2
fi

TESTS_RUN=$((TESTS_RUN + 1))
if ! jq -e '.hooks.PreToolUse[0].hooks[0].command | test("pre-tool-use.sh")' "$SETTINGS" >/dev/null 2>&1; then
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: settings.json PreToolUse does not invoke pre-tool-use.sh" >&2
fi

test_summary
