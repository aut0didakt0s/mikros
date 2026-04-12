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

# Per-phase caveman injection (Cavekit-style). Every mikrós command runs the
# helper in a "Step 0 — Caveman mode for this phase" section and forwards
# the decision into its own behavior. /execute-task must also propagate the
# flag to the phase-builder subagent via CAVEMAN_MODE in the dispatch prompt.
for cmd in discuss plan-slice execute-task sniff-test compress; do
  file=".claude/commands/${cmd}.md"
  assert_file_contains "$file" "## Step 0 — Caveman mode for this phase" \
    "${cmd}: Step 0 caveman section present"
  assert_file_contains "$file" "bash .claude/lib/caveman-phase.sh active ${cmd}" \
    "${cmd}: invokes caveman-phase.sh with correct phase name"
  assert_file_contains "$file" "CAVEMAN_ACTIVE" \
    "${cmd}: captures CAVEMAN_ACTIVE result"
done

assert_file_contains ".claude/commands/execute-task.md" "CAVEMAN_MODE: on" \
  "/execute-task forwards CAVEMAN_MODE: on to dispatch prompt"
assert_file_contains ".claude/commands/execute-task.md" "CAVEMAN_MODE: off" \
  "/execute-task forwards CAVEMAN_MODE: off to dispatch prompt"

test_summary
