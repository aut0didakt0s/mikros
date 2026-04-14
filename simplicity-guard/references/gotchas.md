# Gotchas

Append failure modes here: `## YYYY-MM-DD — title`, then **Failure:**, **Root cause:**, **Rule added:**.

## Entries

## 2026-04-14 — Shared module-level WORKFLOWS dict pollutes negative test assertions

**Failure:** `test_list_workflows.test_list_all` initially tried to assert that demo workflows were absent from the MCP `list_workflows` response. The assertion failed because other test modules inject demos into the module-level `WORKFLOWS` dict (imported from `mikros_server.main`) during their `setup_function`/`setup_method`, and pytest shares the process across modules. Alphabetical test ordering ran the demo-injecting modules before `test_list_workflows`, so by the time `test_list_all` ran the shared dict already had the injected demos.

**Root cause:** The `WORKFLOWS` dict is a single shared mutable object that the MCP tool closures observe by reference (this is the documented `mcp._mikros_workflows` escape-hatch pattern from M006). Any test that mutates it leaks state into every later test in the same pytest session.

**Rule added:** Negative assertions ("X is NOT in the workflow set") cannot be made by querying the MCP tool surface. Use filesystem layout checks (`os.listdir(workflows_dir)`) or `BUILT_IN_NAMES`-style constants in `test_create_app.py` instead. If a test must mutate `WORKFLOWS`, document the mutation as a session-wide side effect — do not assume it gets undone between tests.

## 2026-04-14 — Phase-builder cross-repo work needs absolute `git -C <path>` form

**Failure:** During M007/T02 (which spans the mikros worktree AND a sibling domain repo `/Users/diegomarono/mikros-writing/`), the phase-builder agent issued `cd /Users/diegomarono/mikros` followed by `rm` to delete migrated production YAMLs. The `cd` jumped from the worktree (`/Users/diegomarono/mikros/.claude/worktrees/agent-aa9a13ee/`) into the main repo. The `rm` deleted essay.yaml, blog.yaml, test_essay_workflow.py, test_blog_workflow.py from MAIN, not from the worktree. The agent's worktree commit was empty (no migration). Main repo had unwanted dirty deletions; the slice's squash-merge would have missed the deletions entirely.

**Root cause:** Bare `cd <path>` in shell commands silently changes the agent's view of "where am I" without changing how it reasons about which repo it's modifying. The phase-builder operates on `cwd` semantics by default, so any `rm`/`git` command runs against whatever directory `cd` last landed in.

**Rule added:** When a task touches more than one git working tree (e.g., a worktree plus a sibling repo), the phase-builder MUST use absolute `git -C <absolute-path>` form for every git operation and absolute paths for every `rm`/`mv`. Do NOT chain `cd <path> && <op>`. The cross-repo ground truth is the absolute path, not the shell's cwd. After every multi-repo operation, verify with `git -C <expected-path> status` before proceeding.

## 2026-04-14 — Phase-builder summaries can over-report scope

**Failure:** M007/T01 phase-builder summary listed `tests/test_create_app.py` and `tests/test_validate_cli.py` as "modified" with `BUILT_IN_NAMES = {"example"}` and similar updates. The actual T01 commit `3f598db` (verified via `git show --stat`) shows neither file was touched. The dispatcher accepted the summary at face value, advanced state, and the gap surfaced only when T02's deletions broke the un-retargeted assertions.

**Root cause:** Subagent summaries are self-reported; agents can confuse "I edited this in a transient buffer" with "I committed this". No automated cross-check verifies summary claims against the actual commit.

**Rule added:** The `/execute-task` dispatcher must run `git -C <worktree-path> show --stat HEAD` after the subagent returns and reconcile the file list against the summary's "modified" section. Discrepancies fail the task gate. Until that's automated, manually verify any T## summary that claims to retarget framework code before advancing STATE.md.

## 2026-04-14 — plan-slice file lists under-count mechanical rename scope

**Failure:** M009/S01/T02's plan listed three target files (`.mikros/`, `mikros.py`, `install.sh`). At commit time the grep audit found eleven additional live code references (hooks, lib helpers, gitignore, seven shell tests) that move lockstep with T02's renames and would have broken the build on merge if deferred to T06. The phase-builder honored the stated scope and hit its turn limit trying to resolve the shell-test gap mid-execution; the dispatcher had to land a second commit to close the gap.

**Root cause:** The plan-slice inventory relies on an Explore-subagent grep pass, which scans for the token being renamed but doesn't disambiguate "prose mention" from "live reference that breaks if not updated lockstep." Shell tests of a renamed script, sandbox-path assertions in caveman/install/loc-budget tests, and hook files that read the renamed state dir were absent from the plan because the inventory pass didn't model "this test exercises code I'm about to rename."

**Rule added:** When plan-slice produces a rename inventory, the dispatcher should grep once more for the rename target across `tests/`, `.claude/hooks/`, `.claude/lib/`, `.gitignore`, and the standalone simplicity-guard tree — *before* finalizing the task plan. Any test whose assertions or sandbox paths mention the rename target is a lockstep dependency and must be in the same task's file list. If a test's target file moves to a later task, defer the test's references to the later task too. The anti-pattern is "rename in task N, break tests in N, hope T06 catches it" — T06 is a verification gate, not a repair tool.
