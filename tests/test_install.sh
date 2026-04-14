#!/usr/bin/env bash
# Integration test: run install.sh against a throwaway target.
# Scenario 1: with stubbed claude and docmancer on PATH.
# Scenario 2: with NO optional tools on PATH (silent skip).
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

HERE="$PWD"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# === Scenario 1: tools present (stubbed) ===

TARGET="$WORK/target1"
mkdir -p "$TARGET"

# Stub claude and docmancer so install.sh can run without the real tools.
mkdir -p "$WORK/bin"
cat > "$WORK/bin/claude" <<'STUBEOF'
#!/usr/bin/env bash
echo "stub-claude: $*" >> "$WORK_BIN_LOG"
exit 0
STUBEOF
cat > "$WORK/bin/docmancer" <<'STUBEOF'
#!/usr/bin/env bash
echo "stub-docmancer: $*" >> "$WORK_BIN_LOG"
exit 0
STUBEOF
chmod +x "$WORK/bin/claude" "$WORK/bin/docmancer"

export WORK_BIN_LOG="$WORK/bin.log"
: > "$WORK_BIN_LOG"

PATH="$WORK/bin:$PATH" bash "$HERE/install.sh" "$TARGET"

# --- Assertions: expected files landed in target
assert_file_exists "$TARGET/CLAUDE.md"                                            "CLAUDE.md copied"
assert_file_exists "$TARGET/.mcp.json"                                            ".mcp.json copied"
assert_file_exists "$TARGET/.claude/settings.json"                                "settings.json copied"
assert_file_exists "$TARGET/.claude/skills/simplicity-guard/SKILL.md"             "simplicity-guard SKILL.md copied"
assert_file_exists "$TARGET/.claude/skills/simplicity-guard/references/anti-patterns.md" "anti-patterns.md copied"
assert_file_exists "$TARGET/.claude/agents/phase-builder.md"                      "phase-builder.md copied"
assert_file_exists "$TARGET/.claude/hooks/session-start.sh"                       "session-start.sh copied"
assert_file_exists "$TARGET/.claude/hooks/post-edit.sh"                           "post-edit.sh copied"
assert_file_exists "$TARGET/.claude/hooks/pre-tool-use.sh"                        "pre-tool-use.sh copied"
assert_file_exists "$TARGET/.claude/commands/discuss.md"                          "/discuss command copied"
assert_file_exists "$TARGET/.claude/commands/plan-slice.md"                       "/plan-slice command copied"
assert_file_exists "$TARGET/.claude/commands/execute-task.md"                     "/execute-task command copied"
assert_file_exists "$TARGET/.claude/commands/sniff-test.md"                       "/sniff-test command copied"
assert_file_exists "$TARGET/.claude/commands/compress.md"                         "/compress command copied"
assert_file_exists "$TARGET/.megalos/STATE.md"                                     "STATE.md seeded"
assert_file_exists "$TARGET/.megalos/PROJECT.md"                                   "PROJECT.md seeded"
assert_file_exists "$TARGET/.megalos/DECISIONS.md"                                 "DECISIONS.md seeded"
assert_file_exists "$TARGET/.megalos/config"                                       ".megalos/config seeded"
assert_file_exists "$TARGET/.megalos/templates/T-PLAN.md.tmpl"                     "templates dir copied"
assert_file_exists "$TARGET/.megalos/templates/config.tmpl"                        "config template copied"
assert_file_exists "$TARGET/.claude/lib/caveman-phase.sh"                         "caveman-phase.sh helper copied"
assert_file_contains "$TARGET/.megalos/config" "caveman_mode=on"                   "seeded config has caveman_mode"
assert_file_contains "$TARGET/.megalos/config" "caveman_phases=execute-task,sniff-test,compress" \
    "seeded config has default phases"

# --- Assertions: caveman + docmancer install calls happened (tools present)
assert_file_contains "$WORK_BIN_LOG" "plugin marketplace add JuliusBrussee/caveman" "caveman marketplace call"
assert_file_contains "$WORK_BIN_LOG" "plugin install caveman@caveman"                "caveman install call"
assert_file_contains "$WORK_BIN_LOG" "install claude-code"                           "docmancer install call"
assert_file_contains "$WORK_BIN_LOG" "/caveman:compress"                             "caveman-compress on CLAUDE.md"

# === Scenario 2: NO optional tools on PATH ===

TARGET2="$WORK/target2"
mkdir -p "$TARGET2"

# Use a PATH with no claude or docmancer — only essential system tools.
MINIMAL_PATH="/usr/bin:/bin"
OUTPUT="$(PATH="$MINIMAL_PATH" bash "$HERE/install.sh" "$TARGET2" 2>&1)"

# --- Files still land correctly without any tools
assert_file_exists "$TARGET2/CLAUDE.md"                                           "no-tools: CLAUDE.md copied"
assert_file_exists "$TARGET2/.claude/settings.json"                               "no-tools: settings.json copied"
assert_file_exists "$TARGET2/.megalos/STATE.md"                                    "no-tools: STATE.md seeded"
assert_file_exists "$TARGET2/.megalos/config"                                      "no-tools: config seeded"
assert_file_exists "$TARGET2/.claude/agents/phase-builder.md"                     "no-tools: phase-builder.md copied"

# --- No warning messages printed for absent tools
TESTS_RUN=$((TESTS_RUN + 1))
if echo "$OUTPUT" | grep -qi "skipping\|not on PATH\|warning"; then
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: no-tools run printed warning about absent tools" >&2
  echo "  output: $OUTPUT" >&2
else
  echo "PASS: no-tools run produced no warnings for absent tools"
fi

test_summary
