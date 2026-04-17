# Branch protection — operator reference

> **Effective as of main SHA `842ba7e`** (PR #1, merged 2026-04-17) — the commit that landed `.github/workflows/ci.yml`. The 8 status-check names below are discoverable via `gh pr checks <any-new-pr#>` or `gh run view --json jobs` on any CI run from `842ba7e` onward.

## Purpose

After M002/S02 landed CI, the final step to enforce the four gates (test, lint, typecheck, coverage) on main is configuring GitHub branch protection. This is a manual, **one-time operator action** — nothing in the repo can configure it for you; GitHub's API surface for branch protection lives outside what a PR can change. Once configured, PRs cannot merge into main until all 8 named status checks pass.

## Why 8 checks, not 4

`.github/workflows/ci.yml` defines **4 top-level job names:** `test`, `lint`, `typecheck`, `coverage`. Each job matrixes over `python-version: ['3.10', '3.12']`. GitHub surfaces each matrix cell as a **distinct status-check row** in the branch-protection dropdown → 4 × 2 = **8 rows**, not duplicates.

**Picking only the 4 unversioned names will not work.** Names like `test` (without `(3.10)` or `(3.12)`) do not exist in the dropdown — only the matrix-expanded 8 do. If you select only 4 assuming they're equivalent, branch protection will accept them, but they'll sit unmatched and PRs will merge with zero enforcement because the actual check names never match.

## The 8 required check names

Verbatim, as they appear in the GitHub branch-protection search box:

- `test (3.10)`
- `test (3.12)`
- `lint (3.10)`
- `lint (3.12)`
- `typecheck (3.10)`
- `typecheck (3.12)`
- `coverage (3.10)`
- `coverage (3.12)`

If you want to verify these names against your own repo before clicking, run:

```bash
gh pr checks <any-open-pr#>
```

or

```bash
gh run view <any-ci-run-id> --json jobs -q '.jobs[].name'
```

Both will print the 8 names as they appear in the UI. The first-party source for this list is always the live workflow — this doc is a snapshot.

## Operator click-path

1. GitHub repo → **Settings** → **Branches** (left sidebar).
2. Under "Branch protection rules," click **Add branch protection rule** (or **Edit** if one exists).
3. **Branch name pattern:** `main`
4. Check **Require a pull request before merging**.
5. Check **Require status checks to pass before merging**.
6. Check **Require branches to be up to date before merging** (nested under the above — makes sure the PR branch has merged main before the checks run).
7. In the status-check **search box**, type each of the 8 names from the list above and select it. All 8 must appear in the "selected" list before you save.
8. Click **Save** (or **Create** on first rule).

## What NOT to do

- **Do not require the umbrella `CI` status.** GitHub exposes a workflow-level status that resolves as soon as the workflow *starts*, not when its jobs *pass*. Requiring only `CI` means PRs can merge the instant a workflow dispatches, even if every job fails afterward. Always require the **8 job-level checks** explicitly.
- **Do not enable "Require conversation resolution" before at least one reviewer is set up** — unrelated to CI but trips up empty repos.
- **Do not exempt yourself from the rule** ("Do not allow bypassing the above settings" should stay checked unless you have a specific operational reason). Self-bypass on a solo repo defeats the gate.

## Maintenance rule

When `.github/workflows/ci.yml` changes — adding or removing a job, changing the Python-version matrix, renaming a job — **this doc must be updated in the same PR that edits the workflow**. GitHub does not auto-track workflow changes in branch protection. The required-check list in the GitHub UI must also be **manually** updated by an operator after that PR merges; there is no sync step.

The "Why 8 checks" section above is the key maintenance checkpoint: if the matrix changes from `['3.10', '3.12']` to (say) `['3.10', '3.11', '3.12']`, the count becomes 4 × 3 = 12, and this doc + branch protection must reflect that.

## References

- CI workflow: [.github/workflows/ci.yml](../.github/workflows/ci.yml)
- M002/S02 plan: `.gsd/milestones/M002/slices/S02/S02-PLAN.md` (local, gitignored)
- PR that landed CI: [#1](https://github.com/agora-creations/megalos/pull/1) — merged as `842ba7e`
- Workflow run on `842ba7e` (main): [24559727414](https://github.com/agora-creations/megalos/actions/runs/24559727414) — 8/8 green in 56s, the first on-main run proving the `on: push: branches: [main]` trigger
