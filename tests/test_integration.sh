#!/usr/bin/env bash
# Integration test: fresh install -> state files exist -> megalos.py gate works
# Tests all four runtime detection scenarios via PATH manipulation.
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

HERE="$PWD"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Minimal PATH with only system essentials (no claude/gemini/docmancer)
BARE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"

# --- Scenario A: neither runtime on PATH --------------------------------
TARGET_A="$WORK/target-a"
mkdir -p "$TARGET_A"
PATH="$BARE_PATH" bash "$HERE/install.sh" "$TARGET_A" 2>/dev/null

assert_file_exists "$TARGET_A/.megalos/STATE.md"       "A: STATE.md seeded"
assert_file_exists "$TARGET_A/.megalos/PROJECT.md"     "A: PROJECT.md seeded"
assert_file_exists "$TARGET_A/.megalos/DECISIONS.md"   "A: DECISIONS.md seeded"
assert_file_exists "$TARGET_A/.megalos/config"         "A: config seeded"
assert_file_exists "$TARGET_A/.claude/settings.json"  "A: .claude copied"
assert_file_exists "$TARGET_A/CLAUDE.md"              "A: both -> CLAUDE.md"
assert_file_exists "$TARGET_A/GEMINI.md"              "A: both -> GEMINI.md"
assert_file_exists "$TARGET_A/megalos.py"              "A: megalos.py copied"

# Verify megalos.py gate discuss exits 0
( cd "$TARGET_A" && python3 megalos.py gate discuss )
RC=$?
assert_exit_code "0" "$RC" "A: gate discuss exits 0"

# Verify STATE.md has correct defaults
assert_file_contains "$TARGET_A/.megalos/STATE.md" "active_milestone:" "A: STATE has active_milestone"
assert_file_contains "$TARGET_A/.megalos/STATE.md" "active_slice:"     "A: STATE has active_slice"
assert_file_contains "$TARGET_A/.megalos/STATE.md" "loc_budget:"       "A: STATE has loc_budget"

# --- Scenario B: claude only on PATH ------------------------------------
STUB_B="$WORK/stub-b"
mkdir -p "$STUB_B"
printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB_B/claude"
chmod +x "$STUB_B/claude"

TARGET_B="$WORK/target-b"
mkdir -p "$TARGET_B"
PATH="$STUB_B:$BARE_PATH" bash "$HERE/install.sh" "$TARGET_B" 2>/dev/null

assert_file_exists "$TARGET_B/CLAUDE.md"              "B: claude -> CLAUDE.md"
# gemini should NOT be installed when only claude is on PATH
TESTS_RUN=$((TESTS_RUN + 1))
if [ -f "$TARGET_B/GEMINI.md" ]; then
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL [B: claude-only -> no GEMINI.md]: GEMINI.md should not exist" >&2
fi

# --- Scenario C: gemini only on PATH ------------------------------------
STUB_C="$WORK/stub-c"
mkdir -p "$STUB_C"
printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB_C/gemini"
chmod +x "$STUB_C/gemini"

TARGET_C="$WORK/target-c"
mkdir -p "$TARGET_C"
PATH="$STUB_C:$BARE_PATH" bash "$HERE/install.sh" "$TARGET_C" 2>/dev/null

assert_file_exists "$TARGET_C/GEMINI.md"              "C: gemini -> GEMINI.md"
assert_file_exists "$TARGET_C/.gemini/settings.json"  "C: gemini -> .gemini/settings.json"
assert_file_exists "$TARGET_C/gemini-extension.json"  "C: gemini -> gemini-extension.json"
# claude should NOT be installed when only gemini is on PATH
TESTS_RUN=$((TESTS_RUN + 1))
if [ -f "$TARGET_C/CLAUDE.md" ]; then
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL [C: gemini-only -> no CLAUDE.md]: CLAUDE.md should not exist" >&2
fi

# --- Scenario D: both on PATH -------------------------------------------
STUB_D="$WORK/stub-d"
mkdir -p "$STUB_D"
printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB_D/claude"
printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB_D/gemini"
chmod +x "$STUB_D/claude" "$STUB_D/gemini"

TARGET_D="$WORK/target-d"
mkdir -p "$TARGET_D"
PATH="$STUB_D:$BARE_PATH" bash "$HERE/install.sh" "$TARGET_D" 2>/dev/null

assert_file_exists "$TARGET_D/CLAUDE.md"              "D: both -> CLAUDE.md"
assert_file_exists "$TARGET_D/GEMINI.md"              "D: both -> GEMINI.md"
assert_file_exists "$TARGET_D/.gemini/settings.json"  "D: both -> .gemini/settings.json"

test_summary
