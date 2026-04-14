#!/usr/bin/env bash
# Unit test for .claude/lib/caveman-phase.sh — per-phase caveman decision.
# Runs the helper against a sandbox working directory so we can stage
# different .megalos/config files without touching the real repo.
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

HELPER="$PWD/.claude/lib/caveman-phase.sh"
assert_file_exists "$HELPER" "caveman-phase.sh exists"

if [ ! -x "$HELPER" ]; then
  TESTS_RUN=$((TESTS_RUN + 1))
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: caveman-phase.sh is not executable" >&2
fi

SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
mkdir -p "$SANDBOX/.megalos"

run_helper() {
  ( cd "$SANDBOX" && bash "$HELPER" active "$1" )
}

assert_helper() {
  local phase="$1"
  local expected="$2"
  local msg="$3"
  local got
  got="$(run_helper "$phase")"
  assert_eq "$expected" "$got" "$msg"
}

# --- No config file: defaults. mode=on, phases=execute-task,sniff-test,compress
rm -f "$SANDBOX/.megalos/config"

assert_helper "execute-task" "true"  "default: execute-task active"
assert_helper "sniff-test"   "true"  "default: sniff-test active"
assert_helper "compress"     "true"  "default: compress active"
assert_helper "discuss"      "false" "default: discuss not active (spec phase)"
assert_helper "plan-slice"   "false" "default: plan-slice not active (spec phase)"
assert_helper "nonsense"     "false" "default: unknown phase not active"

# --- Master switch off: everything false, even default phases.
cat > "$SANDBOX/.megalos/config" <<'EOF'
caveman_mode=off
caveman_phases=execute-task,sniff-test,compress
EOF
assert_helper "execute-task" "false" "mode=off: execute-task not active"
assert_helper "sniff-test"   "false" "mode=off: sniff-test not active"
assert_helper "discuss"      "false" "mode=off: discuss not active"

# --- Custom phase list: only compress active.
cat > "$SANDBOX/.megalos/config" <<'EOF'
caveman_mode=on
caveman_phases=compress
EOF
assert_helper "compress"     "true"  "custom phases: compress active"
assert_helper "execute-task" "false" "custom phases: execute-task dropped"
assert_helper "sniff-test"   "false" "custom phases: sniff-test dropped"

# --- Custom phase list: include discuss (user opts in explicitly).
cat > "$SANDBOX/.megalos/config" <<'EOF'
caveman_mode=on
caveman_phases=discuss,execute-task
EOF
assert_helper "discuss"      "true"  "opt-in: discuss active"
assert_helper "execute-task" "true"  "opt-in: execute-task active"
assert_helper "sniff-test"   "false" "opt-in: sniff-test dropped from list"

# --- Config only mode, no phases line → default phases still apply.
cat > "$SANDBOX/.megalos/config" <<'EOF'
caveman_mode=on
EOF
assert_helper "execute-task" "true"  "mode-only config: default phases apply"
assert_helper "discuss"      "false" "mode-only config: discuss still off"

# --- Empty config file (zero bytes): defaults apply (mode=on, default phases).
: > "$SANDBOX/.megalos/config"
assert_helper "execute-task" "true"  "empty config: execute-task active (defaults)"
assert_helper "discuss"      "false" "empty config: discuss not active (defaults)"
assert_helper "compress"     "true"  "empty config: compress active (defaults)"

# --- Missing .megalos directory entirely: defaults apply.
rm -rf "$SANDBOX/.megalos"
assert_helper "execute-task" "true"  "no .megalos dir: execute-task active (defaults)"
assert_helper "discuss"      "false" "no .megalos dir: discuss not active (defaults)"
mkdir -p "$SANDBOX/.megalos"

# --- Missing action or phase → exit 2.
set +e
( cd "$SANDBOX" && bash "$HELPER" 2>/dev/null )
CODE=$?
set -e
assert_eq "2" "$CODE" "no args → exit 2"

set +e
( cd "$SANDBOX" && bash "$HELPER" active 2>/dev/null )
CODE=$?
set -e
assert_eq "2" "$CODE" "missing phase → exit 2"

test_summary
