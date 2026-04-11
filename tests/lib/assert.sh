#!/usr/bin/env bash
# Shared assertion helpers. Source at the top of each test file.
# Every assertion is silent on success and prints "FAIL: ..." + exits 1 on failure.

TESTS_RUN=0
TESTS_FAILED=0

assert_eq() {
  local expected="$1"
  local actual="$2"
  local msg="${3:-equality check}"
  TESTS_RUN=$((TESTS_RUN + 1))
  if [ "$expected" != "$actual" ]; then
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo "FAIL [$msg]: expected '$expected', got '$actual'" >&2
    return 1
  fi
}

assert_exit_code() {
  local expected="$1"
  local actual="$2"
  local msg="${3:-exit code}"
  assert_eq "$expected" "$actual" "$msg"
}

assert_file_exists() {
  local path="$1"
  local msg="${2:-file exists}"
  TESTS_RUN=$((TESTS_RUN + 1))
  if [ ! -f "$path" ]; then
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo "FAIL [$msg]: file does not exist: $path" >&2
    return 1
  fi
}

assert_file_contains() {
  local path="$1"
  local pattern="$2"
  local msg="${3:-file contains pattern}"
  TESTS_RUN=$((TESTS_RUN + 1))
  if ! grep -q "$pattern" "$path" 2>/dev/null; then
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo "FAIL [$msg]: file '$path' does not contain '$pattern'" >&2
    return 1
  fi
}

assert_file_not_contains() {
  local path="$1"
  local pattern="$2"
  local msg="${3:-file does not contain pattern}"
  TESTS_RUN=$((TESTS_RUN + 1))
  if grep -q "$pattern" "$path" 2>/dev/null; then
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo "FAIL [$msg]: file '$path' contains forbidden '$pattern'" >&2
    return 1
  fi
}

test_summary() {
  local passed=$((TESTS_RUN - TESTS_FAILED))
  echo "---"
  echo "Tests: $TESTS_RUN run, $passed passed, $TESTS_FAILED failed"
  if [ "$TESTS_FAILED" -gt 0 ]; then
    exit 1
  fi
}
