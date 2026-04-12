#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

FILE="CLAUDE.md"

assert_file_exists "$FILE" "CLAUDE.md exists"

LINE_COUNT=$(wc -l < "$FILE")
TESTS_RUN=$((TESTS_RUN + 1))
if [ "$LINE_COUNT" -gt 200 ]; then
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: CLAUDE.md has $LINE_COUNT lines (> 200 soft limit)" >&2
fi

assert_file_contains "$FILE" "Iron rule"              "has iron rule"
assert_file_contains "$FILE" "anti-patterns.md"       "references anti-patterns.md"
assert_file_contains "$FILE" "veteran"                "has veteran-engineer framing"
assert_file_contains "$FILE" "KISS"                   "mentions KISS"
assert_file_contains "$FILE" "/discuss"               "lists /discuss in workflow"
assert_file_contains "$FILE" "/plan-slice"            "lists /plan-slice in workflow"
assert_file_contains "$FILE" "/execute-task"          "lists /execute-task in workflow"
assert_file_contains "$FILE" "/sniff-test"            "lists /sniff-test in workflow"
assert_file_contains "$FILE" "/compress"              "lists /compress in workflow"
assert_file_contains "$FILE" "Fresh context"          "explains fresh-context-per-task"

test_summary
