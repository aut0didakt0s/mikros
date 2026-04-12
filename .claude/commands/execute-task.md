---
name: execute-task
description: Run one task to completion in a fresh phase-builder subagent with pre-loaded context. Dispatches a subagent rather than working inline. Writes T##-SUMMARY.md on return.
argument-hint: "<task-id, e.g. T01>"
---

# /execute-task — pre-loaded dispatch to phase-builder

You are the dispatcher for one mikrós task. You do **not** write code yourself — you build a pre-loaded dispatch prompt and hand it to the `phase-builder` subagent.

## Step 1 — Verify gates

Read `.mikros/STATE.md`. Verify:

- An active milestone, slice, and task are set.
- The argument you received matches `active_task` (if not, stop and ask the user to update `STATE.md` or re-run).

## Step 2 — Gather context for pre-loading

Build your dispatch prompt by **inlining** all of the following content directly into the prompt text. The subagent must not need to read any file that is already inlined here.

1. **The task plan.** Read `.mikros/plans/M###/S##/T##-PLAN.md` in full. Inline it verbatim.
2. **Prior task summaries from the same slice.** Read `T##-SUMMARY.md` for every completed task `T0X` where `X < current`. Inline each verbatim.
3. **Architectural decisions.** Read `.mikros/DECISIONS.md` in full. Inline it verbatim.
4. **Source files listed in the task's Artifacts section.** For each file path the task plan names, read the current content and inline it. Annotate with `<!-- CURRENT CONTENT OF path/to/file.py -->` markers.
5. **Relevant reference files from `simplicity-guard`.** Inline `anti-patterns.md` and any non-empty `gotchas.md`.

Your dispatch prompt will be long. That is the point. Every token the subagent would have spent on orientation is now free for actual work.

## Step 3 — Dispatch the subagent

Call the `Agent` tool with:

```
Agent(
  subagent_type="phase-builder",
  description="Execute <task-id>: <task title>",
  prompt="<the full pre-loaded dispatch prompt from Step 2, followed by the task's must-haves and your contract>"
)
```

The `phase-builder` subagent is defined with `isolation: worktree`, `maxTurns: 30`, and `simplicity-guard` preloaded, so every task runs in its own fresh git worktree with the iron rule and anti-patterns in scope.

## Step 4 — Write the task summary atomically

The subagent will return a summary in the required format (see `.claude/agents/phase-builder.md`). Save it to disk:

- Write `.mikros/plans/M###/S##/T##-SUMMARY.md` via atomic write-then-rename (tmp file, `mv`).
- Parse the `### Worktree` section of the summary and extract `branch` and `path`. Write both to `.mikros/STATE.md`:
  - `active_worktree:` ← branch name
  - `active_worktree_path:` ← absolute worktree path
  (Atomic write-then-rename as usual.)
- If the summary's `### Decisions` section is non-empty, append each decision (with today's date header) to `.mikros/DECISIONS.md` atomically.
- If the summary includes a `### Gotchas` section (optional; appears only when the subagent hit a failure mode), append those to `.claude/skills/simplicity-guard/references/gotchas.md`.

## Step 5 — Update STATE.md

Atomically update `.mikros/STATE.md`:
- Mark `T##` as complete.
- Advance `active_task` to the next task in the slice (read from `S##-PLAN.md`).
- Update `loc_budget` to the next task's LOC budget (read from its entry in `S##-PLAN.md`).
- If this was the last task, clear `active_task` and `loc_budget` and tell the user the slice is ready for `/sniff-test`.

## Step 6 — Hand off

Print a one-line summary:
- ✅ if all must-haves were verified
- ❌ if any must-have failed

Tell the user the next command: another `/execute-task` if more tasks remain, or `/sniff-test S##` if the slice is complete.

## Non-interactive runs

The `phase-builder` subagent uses `Bash(git:*)` to commit its work inside the worktree. A PreToolUse hook (`.claude/hooks/pre-tool-use.sh`) auto-approves `git add` and `git commit` when the Bash tool call is rooted inside a linked worktree (i.e. the cwd's `.git` is a file, not a directory). This unblocks `claude -p --permission-mode acceptEdits` end-to-end without relaxing interactive safety in the main repo — the main repo's `.git` is a directory, so git commands issued there still prompt normally. The hook is surgical: only `add` and `commit`, no chaining, no `push`/`reset`/`checkout`/`rm`/`clean`.

## Error handling

If the subagent returns with an iron-rule violation error ("task does not fit in one context window"), stop. Do NOT write a summary. Tell the user to run `/plan-slice` again to split the task, and do not update `STATE.md`.
