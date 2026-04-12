---
name: compress
description: Post-slice deletion-first compression pass. Invokes the bundled /simplify skill then runs a targeted deletion prompt. Requires human approval before applying deletions.
argument-hint: "<slice-id, e.g. S01>"
---

# /compress — deletion-first compression pass

You run this command after `/sniff-test S##` merges a slice to main. Its job is to prevent accumulated bloat by aggressively deleting unused or over-engineered code from the slice's merged work.

## Step 0 — Caveman mode for this phase

Run `bash .claude/lib/caveman-phase.sh active compress` and capture the result as `CAVEMAN_ACTIVE`.

- If `true`: compress your own status reports, hand-off text, and internal reasoning using caveman-speak — drop articles, filler, pleasantries; fragments fine; pattern `[thing] [action] [reason]`. **Do not** apply caveman to files you write (specs, plans, code, DECISIONS.md entries), to the numbered approval list (structured output the user reads item-by-item), to the git commit message, or to error text quoted from tools.
- If `false`: normal prose for the entire command. Override any session-wide caveman default — this command produces human-reviewed artifacts.

## Step 1 — Diff the slice

Read `.mikros/STATE.md` to get the merge commit for the slice. Run:

```
git diff <pre-slice-commit>..HEAD --stat
git diff <pre-slice-commit>..HEAD
```

Capture the full diff. You will reason over it in the next two steps.

## Step 2 — Invoke the bundled /simplify skill

Run `/simplify` on the current working tree. The bundled `simplify` skill reviews changed code for reuse, quality, and efficiency and refactors to eliminate duplication. Let it run to completion. Collect its proposed refactors.

## Step 3 — Deletion-first pass

Run this prompt yourself, reasoning over the diff from Step 1:

> "Review this diff. What can be **deleted** without losing any of the slice's must-haves? For each proposed deletion:
>
> 1. Point to the exact lines or file.
> 2. Explain why the code is dead, redundant, or over-engineered relative to the must-haves.
> 3. Estimate the LOC saved.
>
> Return a bulleted list. Do not propose edits, only deletions. If nothing can be deleted, say so explicitly."

Be aggressive. A slice that adds zero net LOC or goes net-negative is a win. A slice that only adds LOC should be suspicious to you.

## Step 4 — Present to the user for approval

Print the combined proposals (simplify refactors + your deletions) as a numbered list. Ask:

```
Proposed changes:
  [1] Delete unused helper src/foo/bar.py (-42 LOC)
  [2] Inline the single-use adapter class in src/auth.py (-18 LOC)
  [3] Remove dead branch in src/cli.py (-7 LOC)
  ...

Approve which? (comma-separated numbers, "all", or "none")
```

Wait for the user's answer. Do NOT auto-apply.

## Step 5 — Apply approved changes

For each approved item:

1. Apply the deletion or refactor.
2. Run the project's fast-guard verification (same commands as `/sniff-test` Step 2c).
3. If verification fails for any reason, stop — do not commit. Tell the user which item failed and leave the working tree in the failing state for manual fix.

On success, commit all applied changes as a single follow-up commit:

```
git add -u
git commit -m "refactor(<slice-id>): deletion-first compression pass"
```

## Step 6 — Update gotchas.md if you learned something

If the compression pass revealed a failure mode that should be prevented next time (e.g., the planner created an interface with a single implementation and you had to inline it), append an entry to `.claude/skills/simplicity-guard/references/gotchas.md`. This is how mikrós improves — every compression pass is a feedback opportunity.

## Step 7 — Hand off

Print the final LOC delta for the slice (net added/removed) and tell the user the next command is `/plan-slice S<next>` or `/discuss` for a new milestone.
