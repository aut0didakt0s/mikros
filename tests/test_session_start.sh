#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

HOOK=".claude/hooks/session-start.sh"

assert_file_exists "$HOOK" "hook exists"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- Test 1: No .megalos/STATE.md → prints nothing (silent)
OUT=$(cd "$WORK" && bash "$OLDPWD/$HOOK" 2>&1)
assert_eq "" "$OUT" "no STATE.md → silent"

# --- Test 2: STATE.md present → prints banner + state contents + iron-rule reminder
mkdir -p "$WORK/.megalos"
echo "active_task: T01" > "$WORK/.megalos/STATE.md"
OUT=$(cd "$WORK" && bash "$OLDPWD/$HOOK" 2>&1)
case "$OUT" in
  *"megalos session"*)
    ;;
  *)
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo "FAIL: output missing 'megalos session' banner" >&2
    ;;
esac
case "$OUT" in
  *"active_task: T01"*)
    ;;
  *)
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo "FAIL: output missing STATE.md contents" >&2
    ;;
esac
case "$OUT" in
  *"Iron rule"*|*"iron rule"*)
    ;;
  *)
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo "FAIL: output missing iron rule reminder" >&2
    ;;
esac

test_summary
