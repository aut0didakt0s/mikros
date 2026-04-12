#!/usr/bin/env bash
# Integration test: run install.sh against a throwaway target with stubbed
# claude and docmancer binaries. Verify the expected files land in the target.
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

TARGET="$WORK/target"
mkdir -p "$TARGET"

# Stub claude and docmancer so install.sh can run without the real tools.
mkdir -p "$WORK/bin"
cat > "$WORK/bin/claude" <<'EOF'
#!/usr/bin/env bash
# Accept any args, record them, and exit 0.
echo "stub-claude: $*" >> "$WORK_BIN_LOG"
exit 0
EOF
cat > "$WORK/bin/docmancer" <<'EOF'
#!/usr/bin/env bash
echo "stub-docmancer: $*" >> "$WORK_BIN_LOG"
exit 0
EOF
chmod +x "$WORK/bin/claude" "$WORK/bin/docmancer"

export WORK_BIN_LOG="$WORK/bin.log"
: > "$WORK_BIN_LOG"

# Run install.sh.
HERE="$PWD"
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
assert_file_exists "$TARGET/.mikros/STATE.md"                                     "STATE.md seeded"
assert_file_exists "$TARGET/.mikros/PROJECT.md"                                   "PROJECT.md seeded"
assert_file_exists "$TARGET/.mikros/DECISIONS.md"                                 "DECISIONS.md seeded"
assert_file_exists "$TARGET/.mikros/templates/T-PLAN.md.tmpl"                     "templates dir copied"

# --- Assertions: caveman + docmancer install calls happened
assert_file_contains "$WORK_BIN_LOG" "plugin marketplace add JuliusBrussee/caveman" "caveman marketplace call"
assert_file_contains "$WORK_BIN_LOG" "plugin install caveman@caveman"                "caveman install call"
assert_file_contains "$WORK_BIN_LOG" "install claude-code"                           "docmancer install call"
assert_file_contains "$WORK_BIN_LOG" "/caveman:compress"                             "caveman-compress on CLAUDE.md"

test_summary
