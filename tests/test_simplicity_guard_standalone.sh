#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

DIR="simplicity-guard"

# --- Structure checks ---
assert_file_exists "$DIR/SKILL.md" "standalone SKILL.md exists"
assert_file_exists "$DIR/gemini-extension.json" "gemini-extension.json exists"
assert_file_exists "$DIR/references/anti-patterns.md" "anti-patterns.md exists"
assert_file_exists "$DIR/references/gotchas.md" "gotchas.md exists"
assert_file_exists "$DIR/scripts/loc-budget.sh" "loc-budget.sh exists"
assert_file_exists "$DIR/README.md" "README.md exists"

# --- JSON validity ---
TESTS_RUN=$((TESTS_RUN + 1))
if ! jq . "$DIR/gemini-extension.json" > /dev/null 2>&1; then
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: gemini-extension.json is not valid JSON" >&2
fi

# --- SKILL.md has no megalos-specific paths ---
assert_file_not_contains "$DIR/SKILL.md" ".megalos/STATE.md" "no .megalos/STATE.md in standalone SKILL.md"
assert_file_not_contains "$DIR/SKILL.md" "/plan-slice" "no /plan-slice in standalone SKILL.md"
assert_file_not_contains "$DIR/SKILL.md" ".claude/" "no .claude/ paths in standalone SKILL.md"

# --- SKILL.md has required content ---
assert_file_contains "$DIR/SKILL.md" "anti-patterns.md" "references anti-patterns.md"
assert_file_contains "$DIR/SKILL.md" "gotchas.md" "references gotchas.md"
assert_file_contains "$DIR/SKILL.md" "loc-budget.sh" "references loc-budget.sh"
assert_file_contains "$DIR/SKILL.md" "iron rule" "mentions iron rule"
assert_file_contains "$DIR/SKILL.md" "MEGALOS_LOC_BUDGET" "mentions env var"

# --- loc-budget.sh is self-contained (no .claude/ imports) ---
assert_file_not_contains "$DIR/scripts/loc-budget.sh" ".claude/" "loc-budget.sh has no .claude/ imports"

# --- loc-budget.sh reads env var ---
assert_file_contains "$DIR/scripts/loc-budget.sh" "MEGALOS_LOC_BUDGET" "loc-budget.sh reads MEGALOS_LOC_BUDGET"

# --- loc-budget.sh falls back to .megalos/STATE.md ---
assert_file_contains "$DIR/scripts/loc-budget.sh" ".megalos/STATE.md" "loc-budget.sh falls back to STATE.md"

# --- loc-budget.sh works with env var (functional test) ---
TESTS_RUN=$((TESTS_RUN + 1))
# Budget of 99999 should always pass
if ! MEGALOS_LOC_BUDGET=99999 bash "$DIR/scripts/loc-budget.sh"; then
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: loc-budget.sh should exit 0 with large budget" >&2
fi

# --- anti-patterns.md has no megalos-specific references ---
assert_file_not_contains "$DIR/references/anti-patterns.md" ".megalos/" "anti-patterns.md has no .megalos/ paths"
assert_file_not_contains "$DIR/references/anti-patterns.md" "/plan-slice" "anti-patterns.md has no /plan-slice"

# --- Thin wrapper check ---
assert_file_exists ".claude/skills/simplicity-guard/SKILL.md" "wrapper SKILL.md exists"
assert_file_contains ".claude/skills/simplicity-guard/SKILL.md" "simplicity-guard" "wrapper has skill name"

test_summary
