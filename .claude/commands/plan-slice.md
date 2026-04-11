---
name: plan-slice
description: Decompose a slice into 1-7 tasks, each sized to fit in one context window. Writes S##-PLAN.md with must-haves per task. Gated on /discuss having run for the current milestone.
argument-hint: "<slice-id, e.g. S01>"
---

# /plan-slice — decompose a slice into tasks

You are planning a slice for mikrós. A slice is a demoable vertical capability that contains 1–7 tasks.

## Step 1 — Verify gates

Read `.mikros/STATE.md` and verify:

- An active milestone exists (`active_milestone` is set).
- That milestone has a `CONTEXT.md` file — i.e., `/discuss` has run.

If either gate fails, stop and tell the user to run `/discuss` first.

## Step 2 — Inline context into your working memory

Read these files and hold them in your working context for the rest of the command. **Do not defer reading them.**

- `.mikros/STATE.md`
- `.mikros/DECISIONS.md`
- `.mikros/plans/M###/ROADMAP.md`
- `.mikros/plans/M###/CONTEXT.md`
- Any `T##-SUMMARY.md` files from prior completed tasks in this milestone (they contain lessons and decisions that constrain this slice).

## Step 3 — Research the codebase via the Explore subagent

Dispatch the `Explore` subagent (read-only, Haiku) to map the relevant parts of the codebase for this slice. Example:

```
Agent(subagent_type="Explore", description="Map the files relevant to <slice-goal>", prompt="Find files related to X, Y, Z. Report file paths, key types, and any existing patterns used in similar code.")
```

Do not grep or read the whole codebase yourself. Use Explore — it's cheaper and keeps your own context clean for planning.

## Step 4 — Decompose into tasks

Write `.mikros/plans/M###/S##-PLAN.md` containing:

1. **Slice goal** — one sentence.
2. **Demoable outcome** — what the user should be able to see/run after this slice completes.
3. **Task list** — 1 to 7 tasks, each with:
   - **ID** — `T01`, `T02`, …
   - **Title** — short imperative phrase ("Add the LOC budget script with tests").
   - **Files** — exact paths to create or modify.
   - **Must-haves** — three categories:
     - **Truths:** observable behaviors ("User can sign up with email and receive a confirmation token").
     - **Artifacts:** files that must exist with real implementation. Not stubs.
     - **Key Links:** imports/wiring between artifacts ("`src/auth.py` must import `src/models/user.py`").
   - **LOC budget** — the maximum net lines added for this task.

## Step 5 — The iron rule

**A task must fit in one context window. If it can't, it's two tasks.**

Before emitting the plan, self-check every task: could it realistically be completed by a subagent with `maxTurns: 30` in one fresh context? Factors:

- Number of files touched (> 3 is a yellow flag).
- Estimated LOC (> 300 is a red flag).
- Cross-cutting changes (refactors that touch unrelated areas are always too big).

If any task fails the iron rule, split it into two tasks and rename. Do NOT emit a plan you know is wrong.

## Step 6 — Write outputs atomically

Write the new `S##-PLAN.md` via the atomic write-then-rename pattern (see `/discuss` Step 3).

Then update `.mikros/STATE.md`:
- `active_slice: S##`
- `active_task: T01`
- `loc_budget:` copy from the first task

And atomically update `.mikros/plans/M###/ROADMAP.md` to check off the slice as "planned".

## Step 7 — Hand off

Print the task list as a bulleted summary and tell the user the next command is `/execute-task T01`.
