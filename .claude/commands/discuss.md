---
name: discuss
description: Capture user decisions for a new milestone before any planning happens. Writes to DECISIONS.md and M###-CONTEXT.md. The first mikrós command in any new project.
argument-hint: "<topic or milestone ID>"
---

# /discuss — the discussion gate

You are running the **discussion gate** for mikrós. No planning, no code, no slice decomposition until this gate passes.

## Step 1 — Load current state

Read these files into your working context in this order:

1. `.mikros/STATE.md` — determine whether a milestone is already active. If so, mention it to the user; ask whether this discussion is for the same milestone or a new one.
2. `.mikros/DECISIONS.md` — the append-only register of prior decisions. Every decision captured today will be appended here.
3. If a milestone is already active: `.mikros/plans/M###/CONTEXT.md` — prior captured decisions for this milestone.

If any of these files do not exist, it means the project has not been initialized yet. In that case, create `.mikros/STATE.md` and `.mikros/DECISIONS.md` from the templates in `.mikros/templates/` first.

## Step 2 — Ask questions, one at a time

You will interview the user about intent, constraints, and success criteria. The rule is **one question at a time**. Do not batch questions. Do not ask multi-part questions. Wait for the user's answer before asking the next one.

Topics to cover, in roughly this order:

1. **Goal.** What does the user want to build at a high level? What does "done" look like?
2. **Constraints.** Any hard limits (budget, time, language, platform, dependencies that must or must not be used)?
3. **Non-goals.** What is explicitly out of scope for this milestone? Deferring things to later milestones is normal and expected.
4. **Reference material.** Are there existing codebases, papers, or tools that this should draw inspiration from? Capture the URLs.
5. **Success criteria.** How will we know the milestone succeeded? What's the observable outcome?

Stop asking when the user says they are done, or when you have enough information to decompose into slices without guessing.

## Step 3 — Write outputs atomically

After the interview, write (or append to) the following files using atomic write-then-rename:

1. `.mikros/plans/M###/CONTEXT.md` — full captured answers, in a structured format (see template `.mikros/templates/`).
2. `.mikros/DECISIONS.md` — append one entry per captured decision, each with today's date and a one-paragraph rationale.
3. `.mikros/STATE.md` — update the `active_milestone` field to the new milestone ID.

Write every file via:

```
tmp="$(mktemp <file>.XXXXXX.tmp)"
# write contents to "$tmp"
mv "$tmp" <file>
```

Do NOT write files in place. A crash during a direct write corrupts the real file; a crash during temp-then-rename leaves a detectable orphan.

## Step 4 — Hand off

Print a one-line summary of what was captured and tell the user the next command is `/plan-slice S01` to decompose the first slice.
