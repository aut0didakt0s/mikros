#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

FILE=".claude/skills/simplicity-guard/SKILL.md"

assert_file_exists "$FILE" "SKILL.md exists"

# Validate frontmatter has required keys
if ! python3 tests/validate_frontmatter.py "$FILE" name description 2>/dev/null; then
  TESTS_RUN=$((TESTS_RUN + 1))
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: SKILL.md frontmatter missing name or description" >&2
fi

assert_file_contains "$FILE" "simplicity-guard" "name is simplicity-guard"
assert_file_contains "$FILE" "anti-patterns.md" "references anti-patterns.md"
assert_file_contains "$FILE" "gotchas.md"       "references gotchas.md"
assert_file_contains "$FILE" "iron rule"        "mentions iron rule"
assert_file_contains "$FILE" "loc-budget.sh"    "references loc-budget.sh"

test_summary
