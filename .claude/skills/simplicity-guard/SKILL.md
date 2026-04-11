---
name: simplicity-guard
description: Enforces mikrós anti-bloat rules. PROACTIVELY invoke this skill when writing or reviewing code in a mikrós-managed project. Reads anti-patterns.md and gotchas.md before any edit.
allowed-tools: Read, Bash(git:*), Bash(ruff:*), Bash(mypy:*)
---

# simplicity-guard

You are guarding against AI over-engineering. Before you write any code, read both of the following files:

1. `references/anti-patterns.md` — the rule list. These override your defaults.
2. `references/gotchas.md` — known failure modes from prior mikrós sessions.

## The iron rule

**A task must fit in one context window. If it can't, split the task — don't compress the context.**

If you realize the task you were given exceeds one context window, stop and return an error asking `/plan-slice` to split it. Do not attempt to finish.

## The LOC budget

After any `Write` or `Edit`, run:

```
bash .claude/skills/simplicity-guard/scripts/loc-budget.sh
```

It exits 2 if the task's LOC budget (from `.mikros/STATE.md`) has been exceeded. On exit 2, **stop writing code** and return with a split request — the post-edit hook will already have blocked the edit.

## The three-strikes rule

Do not create an abstraction until there are three concrete uses of the pattern. Duplication of two is acceptable; three is the signal. If in doubt, inline.

## Boring is a feature

Clever code loses to boring code that the next reader (human or AI) can understand in ten seconds. Optimize for ten-second comprehension, not for elegance.

## When to update `gotchas.md`

If you hit a failure mode during execution — a pattern that led you astray, a rule that was too loose — before returning, append an entry to `references/gotchas.md` describing the failure and the rule you would have wanted. This is how mikrós learns over time.
