#!/usr/bin/env bash
# megalos SessionStart hook: print active workflow state so the model knows
# which milestone/slice/task it is working on, and remind it of the iron rule.
# Silent when not in a megalos-managed project.

if [ ! -f .megalos/STATE.md ]; then
  exit 0
fi

echo "=== megalos session ==="
cat .megalos/STATE.md
echo
echo "Anti-patterns: .claude/skills/simplicity-guard/references/anti-patterns.md"
echo "Gotchas: .claude/skills/simplicity-guard/references/gotchas.md"
echo "Iron rule: A task must fit in one context window. If it can't, split it."
