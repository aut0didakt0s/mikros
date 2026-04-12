#!/usr/bin/env bash
# Run every mikrós test file in sequence. Any failure halts the run and returns 1.
set -e
cd "$(dirname "$0")/.."

TESTS=(
  tests/test_anti_patterns.sh
  tests/test_simplicity_guard_skill.sh
  tests/test_loc_budget.sh
  tests/test_phase_builder_agent.sh
  tests/test_session_start.sh
  tests/test_post_edit.sh
  tests/test_pre_tool_use.sh
  tests/test_commands.sh
  tests/test_templates.sh
  tests/test_claude_md.sh
  tests/test_settings.sh
  tests/test_install.sh
)

FAILED=0
for t in "${TESTS[@]}"; do
  echo "=== $t ==="
  if bash "$t"; then
    echo "PASS: $t"
  else
    echo "FAIL: $t"
    FAILED=$((FAILED + 1))
  fi
  echo
done

echo "==============="
if [ "$FAILED" -gt 0 ]; then
  echo "$FAILED test file(s) failed"
  exit 1
fi
echo "All test files passed."
