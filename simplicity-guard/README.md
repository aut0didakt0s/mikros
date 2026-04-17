# simplicity-guard

A skill that enforces anti-bloat rules (KISS, DRY, YAGNI) for Claude Code.

## What it does

- Blocks enterprise over-engineering patterns before they land
- Enforces a LOC budget per task (configurable via env var or state file)
- Maintains a living gotchas file that captures failure modes over time

## Installation

Copy this directory into your project:

```
cp -r simplicity-guard/ .claude/skills/simplicity-guard/
```

Claude Code auto-discovers skills in `.claude/skills/`.

## Configuration

Set the LOC budget via environment variable:

```
export MEGALOS_LOC_BUDGET=200
```

Default is 300 lines.

## Files

- `SKILL.md` — skill definition (with frontmatter)
- `references/anti-patterns.md` — the rule list
- `references/gotchas.md` — learned failure modes (starts empty)
- `scripts/loc-budget.sh` — LOC budget enforcement script
