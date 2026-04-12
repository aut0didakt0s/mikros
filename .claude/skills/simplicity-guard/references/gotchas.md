# Gotchas

This file starts empty on purpose.

When you hit a failure mode while using mikrós — a prompt pattern that led Claude down the wrong path, a rule that was too loose, a check that missed an obvious bug — append an entry here with:

- The date
- A one-sentence description of the failure
- The rule or check you added to prevent it next time

## Format

```
## 2026-04-12 — <short title>

**Failure:** <what went wrong in one paragraph>

**Root cause:** <what was missing from anti-patterns.md, hooks, or commands>

**Rule added:** <exact text added to anti-patterns.md or elsewhere, with file path>
```

## Entries

## 2026-04-12 — PostToolUse hook reads stdin JSON, not env vars

**Failure:** `post-edit.sh` tried to read the edited file path from env vars (`CLAUDE_HOOK_TOOL_INPUT_file_path`, `CLAUDE_TOOL_INPUT_file_path`, `CLAUDE_FILE_PATH`). None of these exist. Claude Code PostToolUse hooks receive input via stdin as JSON: `{"tool_name":"Write","tool_input":{"file_path":"..."},...}`.

**Root cause:** Spec open question 7 was never resolved before shipping. The hook was written with three fallback candidates and a note to "collapse once verified."

**Rule added:** Always read hook input via `jq -r '.tool_input.file_path // empty'` from stdin. No env var fallback. Updated `.claude/hooks/post-edit.sh` and `tests/test_post_edit.sh`.

---

## 2026-04-12 — install.sh caveman-compress blocked by Write permission prompt

**Failure:** `install.sh` runs `claude -p "/caveman:compress CLAUDE.md"` in the target. Without `--permission-mode acceptEdits`, the Write tool is blocked by a permission dialog and `CLAUDE.original.md` is never created. Install appears to succeed but silently skips compression.

**Root cause:** S5 (open question) was never tested. `-p` mode skips workspace trust but does not auto-approve tool calls.

**Rule added:** Use `claude --permission-mode acceptEdits -p "..."` in `install.sh` whenever the subprocess must write files non-interactively. Updated `install.sh`.

---

## 2026-04-12 — loc_budget:N (no space) silently defaults to 300

**Failure:** `loc-budget.sh` used `awk '{print $2}'` to parse the budget value. `loc_budget:300` (no space after colon) produces empty `$2`, silently falling back to 300 regardless of the actual budget.

**Root cause:** Parser assumed a space after the colon. `plan-slice` could emit either form.

**Rule added:** Use `sed 's/loc_budget:[[:space:]]*//'` to strip the key prefix, handling both forms. Updated `.claude/skills/simplicity-guard/scripts/loc-budget.sh`.

---

## 2026-04-12 — T-SUMMARY.md.tmpl missing Worktree section and using wrong heading depth

**Failure:** Template used H2 section headings and had no `### Worktree` section. `execute-task` parses `### Worktree` (H3) from the summary to extract `branch` and `path` for STATE.md. Had a summary been written from the template directly, the parse would silently fail and `active_worktree` would never be set, breaking `/sniff-test`.

**Root cause:** Template was never aligned with `phase-builder.md`'s required summary format (I3).

**Rule added:** Template now mirrors phase-builder's format: `## T## — <title>` H2, H3 subsections including `### Worktree`. Updated `.mikros/templates/T-SUMMARY.md.tmpl`.

---

## 2026-04-12 — phase-builder git commit blocked in acceptEdits permission mode

**Failure:** With `--permission-mode acceptEdits`, the phase-builder subagent could edit files but not run `git add` / `git commit`. Worktree had modified files but no commit. `git merge --squash <branch>` on an empty-commit branch is a no-op; squash-merge silently merges nothing.

**Root cause:** `acceptEdits` only approves Write/Edit tools. Bash(git:*) requires separate approval. The interactive workflow is the expected path for end users — git approvals can be granted one-by-one.

**Rule added:** Document in `execute-task.md` that non-interactive runs with `acceptEdits` will block git commits in the phase-builder. Interactive mode is required for full end-to-end flow. Users must approve git operations when prompted.
