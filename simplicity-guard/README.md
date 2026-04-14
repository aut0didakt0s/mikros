# simplicity-guard

A skill/extension that enforces anti-bloat rules (KISS, DRY, YAGNI) for AI coding assistants. Works with both Claude Code and Gemini CLI.

## What it does

- Blocks enterprise over-engineering patterns before they land
- Enforces a LOC budget per task (configurable via env var or state file)
- Maintains a living gotchas file that captures failure modes over time

## Installation

### Claude Code

Copy this directory into your project:

```
cp -r simplicity-guard/ .claude/skills/simplicity-guard/
```

Claude Code auto-discovers skills in `.claude/skills/`.

### Gemini CLI

Copy this directory into your project and reference it in `.gemini/settings.json`:

```json
{
  "extensions": ["simplicity-guard/gemini-extension.json"]
}
```

## Configuration

Set the LOC budget via environment variable:

```
export MEGALOS_LOC_BUDGET=200
```

Or let the script read `loc_budget:` from `.megalos/STATE.md` if present. Default is 300 lines.

## Files

- `SKILL.md` — skill definition (Claude Code format with frontmatter)
- `gemini-extension.json` — extension declaration (Gemini CLI format)
- `references/anti-patterns.md` — the rule list
- `references/gotchas.md` — learned failure modes (starts empty)
- `scripts/loc-budget.sh` — LOC budget enforcement script
