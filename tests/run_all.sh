#!/usr/bin/env bash
# Run every megálos bash test in sequence. Any failure halts the run and returns 1.
set -e
cd "$(dirname "$0")/.."

TESTS=(
  tests/test_mcp_json.sh
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
