#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

HOOK=".claude/hooks/post-edit.sh"

assert_file_exists "$HOOK" "hook exists"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Stub git and ruff and mypy in WORK/bin so the hook can be driven without tools.
mkdir -p "$WORK/bin"
cat > "$WORK/bin/git"  <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "diff" ] && [ "$2" = "--shortstat" ]; then
  echo " 1 file changed, 5 insertions(+), 0 deletions(-)"
fi
EOF
cat > "$WORK/bin/ruff" <<'EOF'
#!/usr/bin/env bash
# Pass by default.
exit 0
EOF
cat > "$WORK/bin/mypy" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$WORK/bin/git" "$WORK/bin/ruff" "$WORK/bin/mypy"

mkdir -p "$WORK/.claude/skills/simplicity-guard/scripts"
cp .claude/skills/simplicity-guard/scripts/loc-budget.sh \
   "$WORK/.claude/skills/simplicity-guard/scripts/loc-budget.sh"
mkdir -p "$WORK/.megalos"
echo "loc_budget: 300" > "$WORK/.megalos/STATE.md"
touch "$WORK/foo.py"

# --- Test 1: No file path in stdin JSON → silent exit 0
echo '{"tool_name":"Write","tool_input":{}}' \
  | ( cd "$WORK" && PATH="$WORK/bin:$PATH" bash "$OLDPWD/$HOOK" )
assert_exit_code 0 $? "no file_path in JSON → exit 0"

# --- Test 2: Python file, ruff + mypy pass → exit 0
echo '{"tool_name":"Write","tool_input":{"file_path":"foo.py"}}' \
  | ( cd "$WORK" && PATH="$WORK/bin:$PATH" bash "$OLDPWD/$HOOK" )
assert_exit_code 0 $? "python file, clean → exit 0"

# --- Test 3: Python file, ruff fails → exit 2
cat > "$WORK/bin/ruff" <<'EOF'
#!/usr/bin/env bash
echo "ruff: error"
exit 1
EOF
chmod +x "$WORK/bin/ruff"
EC=0
echo '{"tool_name":"Write","tool_input":{"file_path":"foo.py"}}' \
  | ( cd "$WORK" && PATH="$WORK/bin:$PATH" bash "$OLDPWD/$HOOK" 2>/dev/null ) || EC=$?
assert_exit_code 2 "$EC" "python file, ruff fails → exit 2"

# --- Test 4: Non-python file → skips lint, still runs loc-budget → exit 0
cat > "$WORK/bin/ruff" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$WORK/bin/ruff"
touch "$WORK/foo.md"
echo '{"tool_name":"Write","tool_input":{"file_path":"foo.md"}}' \
  | ( cd "$WORK" && PATH="$WORK/bin:$PATH" bash "$OLDPWD/$HOOK" )
assert_exit_code 0 $? "markdown file → skip lint, exit 0"

# --- Test 5 (Gemini CLI): tool_args.file_path → exit 0
echo '{"tool_name":"Write","tool_args":{"file_path":"foo.py"}}' \
  | ( cd "$WORK" && PATH="$WORK/bin:$PATH" bash "$OLDPWD/$HOOK" )
assert_exit_code 0 $? "Gemini CLI tool_args.file_path → exit 0"

# --- Test 6 (Gemini CLI): tool_args with no file_path → exit 0
echo '{"tool_name":"Write","tool_args":{}}' \
  | ( cd "$WORK" && PATH="$WORK/bin:$PATH" bash "$OLDPWD/$HOOK" )
assert_exit_code 0 $? "Gemini CLI tool_args no file_path → exit 0"

test_summary
