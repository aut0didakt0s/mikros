#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

HOOK=".claude/hooks/pre-tool-use.sh"
HOOK_ABS="$PWD/$HOOK"

assert_file_exists "$HOOK" "pre-tool-use hook exists"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- Mock a linked worktree: .git is a file, not a directory ----------------
WT="$WORK/fake-worktree"
mkdir -p "$WT"
echo "gitdir: /some/main/.git/worktrees/fake" > "$WT/.git"

# --- Mock a main repo: .git is a directory ---------------------------------
MAIN="$WORK/fake-main"
mkdir -p "$MAIN/.git"

# --- Test 1: non-Bash tool → no-opinion (empty stdout, exit 0) -------------
OUT=$(echo '{"tool_name":"Write","tool_input":{"command":"git commit -m foo"},"cwd":"'"$WT"'"}' \
  | bash "$HOOK_ABS")
assert_exit_code 0 $? "non-Bash tool → exit 0"
assert_eq "" "$OUT" "non-Bash tool → no approval output"

# --- Test 2: git commit in a worktree → auto-approved ---------------------
OUT=$(echo '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"feat: foo\""},"cwd":"'"$WT"'"}' \
  | bash "$HOOK_ABS")
assert_exit_code 0 $? "git commit in worktree → exit 0"
TESTS_RUN=$((TESTS_RUN + 1))
if ! echo "$OUT" | grep -q '"permissionDecision":"allow"'; then
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: git commit in worktree should be approved, got: $OUT" >&2
fi

# --- Test 3: git add in a worktree → auto-approved ------------------------
OUT=$(echo '{"tool_name":"Bash","tool_input":{"command":"git add ."},"cwd":"'"$WT"'"}' \
  | bash "$HOOK_ABS")
TESTS_RUN=$((TESTS_RUN + 1))
if ! echo "$OUT" | grep -q '"permissionDecision":"allow"'; then
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: git add in worktree should be approved, got: $OUT" >&2
fi

# --- Test 4: git commit in the main repo → no-opinion (prompt falls through) -
OUT=$(echo '{"tool_name":"Bash","tool_input":{"command":"git commit -m foo"},"cwd":"'"$MAIN"'"}' \
  | bash "$HOOK_ABS")
assert_exit_code 0 $? "git commit in main repo → exit 0"
assert_eq "" "$OUT" "git commit in main repo → no approval output"

# --- Test 5: destructive git ops in a worktree → NOT approved --------------
for DESTRUCTIVE in "git push" "git push origin main" "git reset --hard HEAD" "git checkout main" "git rm file.py" "git clean -fd"; do
  OUT=$(echo '{"tool_name":"Bash","tool_input":{"command":"'"$DESTRUCTIVE"'"},"cwd":"'"$WT"'"}' \
    | bash "$HOOK_ABS")
  TESTS_RUN=$((TESTS_RUN + 1))
  if [ -n "$OUT" ]; then
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo "FAIL: '$DESTRUCTIVE' in worktree should NOT be auto-approved, got: $OUT" >&2
  fi
done

# --- Test 6: command chaining blocked even for git commit ------------------
for CHAIN in \
  "git commit -m foo && git push" \
  "git commit -m foo ; rm -rf /" \
  "git commit -m foo | tee log" \
  "git commit -m \"\$(whoami)\"" \
  "git commit -m \`id\`" ; do
  OUT=$(echo '{"tool_name":"Bash","tool_input":{"command":"'"$(printf '%s' "$CHAIN" | sed 's/\\/\\\\/g; s/"/\\"/g')"'"},"cwd":"'"$WT"'"}' \
    | bash "$HOOK_ABS")
  TESTS_RUN=$((TESTS_RUN + 1))
  if [ -n "$OUT" ]; then
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo "FAIL: chained command '$CHAIN' should NOT be auto-approved, got: $OUT" >&2
  fi
done

# --- Test 7: non-git Bash command in worktree → no-opinion ----------------
OUT=$(echo '{"tool_name":"Bash","tool_input":{"command":"ls -la"},"cwd":"'"$WT"'"}' \
  | bash "$HOOK_ABS")
assert_eq "" "$OUT" "ls in worktree → no approval output"

# --- Test 8: missing cwd → no-opinion (can't verify worktree) -------------
OUT=$(echo '{"tool_name":"Bash","tool_input":{"command":"git commit -m foo"}}' \
  | bash "$HOOK_ABS")
assert_eq "" "$OUT" "missing cwd → no approval output"

# --- Test 9: empty JSON → no-opinion, exit 0 ------------------------------
echo '{}' | bash "$HOOK_ABS" >/dev/null
assert_exit_code 0 $? "empty JSON → exit 0"

test_summary
