#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

SCRIPT=".claude/skills/simplicity-guard/scripts/loc-budget.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- Test 1: No .megalos/STATE.md → exit 0 (no active task, no enforcement)
( cd "$WORK" && bash "$OLDPWD/$SCRIPT" )
assert_exit_code 0 $? "no STATE.md → exit 0"

# --- Test 2: STATE.md with small budget and small diff → exit 0
mkdir -p "$WORK/.megalos"
cat > "$WORK/.megalos/STATE.md" <<'EOF'
loc_budget: 300
EOF
# Stub git so "git diff --shortstat" returns a small diff
mkdir -p "$WORK/bin"
cat > "$WORK/bin/git" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "diff" ] && [ "$2" = "--shortstat" ]; then
  echo " 2 files changed, 100 insertions(+), 10 deletions(-)"
fi
EOF
chmod +x "$WORK/bin/git"
PATH="$WORK/bin:$PATH" bash -c "cd '$WORK' && bash '$OLDPWD/$SCRIPT'"
assert_exit_code 0 $? "100 insertions < 300 budget → exit 0"

# --- Test 3: Large diff exceeds budget → exit 2
cat > "$WORK/bin/git" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "diff" ] && [ "$2" = "--shortstat" ]; then
  echo " 5 files changed, 500 insertions(+), 20 deletions(-)"
fi
EOF
chmod +x "$WORK/bin/git"
EC=0
PATH="$WORK/bin:$PATH" bash -c "cd '$WORK' && bash '$OLDPWD/$SCRIPT'" 2>/dev/null || EC=$?
assert_exit_code 2 "$EC" "500 insertions > 300 budget → exit 2"

# --- Test 4: Default budget when STATE.md has no loc_budget line
cat > "$WORK/.megalos/STATE.md" <<'EOF'
active_task: T01
EOF
cat > "$WORK/bin/git" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "diff" ] && [ "$2" = "--shortstat" ]; then
  echo " 1 file changed, 250 insertions(+), 5 deletions(-)"
fi
EOF
chmod +x "$WORK/bin/git"
PATH="$WORK/bin:$PATH" bash -c "cd '$WORK' && bash '$OLDPWD/$SCRIPT'"
assert_exit_code 0 $? "250 insertions < 300 default budget → exit 0"

test_summary
