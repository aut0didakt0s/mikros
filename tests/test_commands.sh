#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

check_command() {
  local name="$1"
  local file=".claude/commands/${name}.md"
  assert_file_exists "$file" "${name}.md exists"
  python3 tests/validate_frontmatter.py "$file" description 2>/dev/null || {
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo "FAIL: ${name}.md missing frontmatter description" >&2
  }
}

# Check each command one-by-one.
check_command "discuss"
assert_file_contains ".claude/commands/discuss.md" "DECISIONS.md"   "/discuss references DECISIONS.md"
assert_file_contains ".claude/commands/discuss.md" "CONTEXT.md"     "/discuss references CONTEXT.md"
assert_file_contains ".claude/commands/discuss.md" "STATE.md"       "/discuss references STATE.md"
assert_file_contains ".claude/commands/discuss.md" "one question at a time" "/discuss enforces single-question rule"

check_command "plan-slice"
assert_file_contains ".claude/commands/plan-slice.md" "Truths"    "/plan-slice requires Truths"
assert_file_contains ".claude/commands/plan-slice.md" "Artifacts" "/plan-slice requires Artifacts"
assert_file_contains ".claude/commands/plan-slice.md" "Key Links" "/plan-slice requires Key Links"
assert_file_contains ".claude/commands/plan-slice.md" "iron rule" "/plan-slice enforces iron rule"

check_command "execute-task"
assert_file_contains ".claude/commands/execute-task.md" "phase-builder" "/execute-task dispatches phase-builder"
assert_file_contains ".claude/commands/execute-task.md" "pre-loaded"    "/execute-task pre-loads context"
assert_file_contains ".claude/commands/execute-task.md" "T##-SUMMARY.md" "/execute-task writes T##-SUMMARY.md"

check_command "sniff-test"
assert_file_contains ".claude/commands/sniff-test.md" "Must-haves" "/sniff-test checks must-haves"
assert_file_contains ".claude/commands/sniff-test.md" "human"       "/sniff-test has human gate"
assert_file_contains ".claude/commands/sniff-test.md" "squash"      "/sniff-test squash-merges"

check_command "compress"
assert_file_contains ".claude/commands/compress.md" "simplify"      "/compress invokes simplify"
assert_file_contains ".claude/commands/compress.md" "deletion"      "/compress runs deletion pass"

test_summary
