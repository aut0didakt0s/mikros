---
name: sniff-test
description: Mechanical must-haves verification plus human review gate for a completed slice or task. Squash-merges the worktree to main on approval.
argument-hint: "<slice-id or task-id, e.g. S01 or T01>"
---

# /sniff-test — mechanical + human verification gate

You are the verification gate. Nothing merges without passing this command.

## Step 0 — Caveman mode for this phase

Run `bash .claude/lib/caveman-phase.sh active sniff-test` and capture the result as `CAVEMAN_ACTIVE`.

- If `true`: compress your own status reports, hand-off text, and internal reasoning using caveman-speak — drop articles, filler, pleasantries; fragments fine; pattern `[thing] [action] [reason]`. **Do not** apply caveman to files you write (specs, plans, code, DECISIONS.md entries), to the mechanical-summary table (the ✅/❌/⚠️ rows and their column headings must stay verbatim), to the squash-merge commit message, or to error text quoted from tools.
- If `false`: normal prose for the entire command. Override any session-wide caveman default — this command produces human-reviewed artifacts.

## Step 1 — Identify what to verify

Parse the argument:
- `S##` → verify the entire slice (all its completed tasks).
- `T##` → verify one task.

Read the corresponding plan file(s) and collect all **Must-haves** (Truths, Artifacts, Key Links).

## Step 2 — Mechanical checks

Run each of these and collect results:

### 2a. Artifacts exist with real implementation

For every file path listed in any task's `Artifacts`:
- Assert the file exists.
- Assert the file is not empty.
- Assert the file does not contain the strings `TODO`, `FIXME`, `raise NotImplementedError`, `pass  # stub`, or `...` as a sole function body. Stubs are not artifacts.

### 2b. Key links resolve

For every key link listed in any task:
- Parse it into a pair `(consumer, provider)` (e.g., "`bar.py` imports `foo.py`").
- Grep the consumer for an import of the provider. Fail if not found.

### 2c. Fast-guard verification commands

Run the project's configured verification commands. In v0 this is hard-coded to:

- Python: `ruff check .` and `mypy .` if either tool is on `PATH` and `.py` files changed.
- TypeScript: `npx tsc --noEmit` if `tsconfig.json` exists and `.ts`/`.tsx` files changed.
- Tests: if a `tests/` or `test/` directory exists and `pytest` or `vitest` is available, run the test suite.

Capture stdout/stderr and exit codes.

### 2d. Truths have tests

For every Truth in the task plan, grep the project's test files for a test that references it (by title, key phrase, or explicit annotation). Warn (not fail) if a Truth has no corresponding test — tell the user which ones are untested.

## Step 3 — Print a clean summary

Print a markdown table listing every must-have with ✅ or ❌:

```
## sniff-test S01 — summary

### Artifacts
- ✅ src/auth.py
- ❌ src/models/user.py  — file is empty

### Key Links
- ✅ src/auth.py imports src/models/user.py (import found)

### Truths
- ✅ User can sign up with email  — test_signup_email.py::test_signup_with_email
- ⚠️  Confirmation token is single-use  — no test found, consider adding one

### Verification commands
- ruff check . → passed
- mypy . → passed
- pytest → 14 passed, 0 failed

### Verdict
READY FOR HUMAN REVIEW
```

If any ❌ is present, the verdict is `FAILED — FIX REQUIRED` and you stop here. Do not proceed to Step 4.

## Step 4 — Human gate

If mechanical checks pass (only ✅ and ⚠️), print:

```
Mechanical checks passed. Ready to merge <id> to main?

Review the diff with: git diff main
Approve with: y
Reject with: n  (no merge happens; you fix and re-run sniff-test)
```

Wait for the user's one-character answer. Do not auto-advance.

## Step 5 — Squash-merge on approval

If the user answered `y`:

1. Get the worktree branch name from `.mikros/STATE.md` (`active_worktree`).
2. Run:
   ```
   git checkout main
   git merge --squash <active_worktree>
   git commit -m "feat(<id>): <slice or task title>"
   git worktree remove <active_worktree_path> --force
   ```
3. Atomically update `.mikros/STATE.md` to clear the active worktree and advance the roadmap checkbox for `<id>`.

## Step 6 — Hand off

Print a one-line summary:
- On approval: `Merged <id> to main; next up: <next command>`.
- On rejection: `Not merged; fix issues and re-run /sniff-test <id>`.
