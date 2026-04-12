# mikrós

> μικρός (ancient Greek: *small*)

A portable Claude Code workflow template that prevents AI over-engineering and controls token cost. Drop it into any project via `install.sh` and you get five slash commands, one subagent, two hooks, one anti-bloat skill, and two installed dependencies (caveman + docmancer). No harness, no TypeScript, no build step.

## The thesis

AI over-engineering is not a discipline problem. It's an absence-of-feedback problem. *"Give Claude a way to verify its output, and it will iterate until the result is great."* — Boris Cherny, 2026.

mikrós gives Claude Code three kinds of feedback:

1. **simplicity-guard** — an in-skill rule list of explicit anti-defaults (not generic "prefer simple" noise) plus a LOC budget hook that blocks edits exceeding the task budget.
2. **caveman** — the [`JuliusBrussee/caveman`](https://github.com/JuliusBrussee/caveman) Claude Code plugin. Cuts ~75% of output tokens by making Claude speak tersely, and ~46% of input tokens via `caveman-compress` on `CLAUDE.md`.
3. **docmancer** — the [`docmancer/docmancer`](https://github.com/docmancer/docmancer) local doc indexer. Kills hallucinated APIs by grounding Claude in real, version-specific documentation retrieved from a local vector store.

## Install

```bash
git clone https://github.com/diegomarono/mikros.git
cd mikros
./install.sh /path/to/your/project
```

The installer copies the template into the target, seeds `.mikros/` state from templates, installs the caveman plugin and docmancer skill, and runs `/caveman:compress` on `CLAUDE.md` (preserving `CLAUDE.original.md` as the human-readable backup).

## Prerequisites on the target machine

- `claude` CLI (Claude Code)
- `docmancer` CLI (install via `pipx install docmancer`)
- `bash` 4.0+, `git`, `python3` 3.8+, `jq`
- `ruff` and `mypy` (required by the post-edit hook for Python files; `brew install ruff mypy`)

## The workflow

Every mikrós project follows the same five steps per slice:

1. `/discuss <topic>` — capture intent before code.
2. `/plan-slice <S##>` — decompose into tasks with must-haves. Enforces the iron rule.
3. `/execute-task <T##>` — run one task in a fresh `phase-builder` subagent with pre-loaded context.
4. `/sniff-test <id>` — mechanical must-haves check + human review gate. Squash-merges on approval.
5. `/compress <S##>` — deletion-first compression pass after merge.

State lives on disk under `.mikros/`:

```
.mikros/
├── STATE.md              # current pointer, always read first
├── PROJECT.md            # living project doc
├── DECISIONS.md          # append-only ADR register
└── plans/
    └── M001/
        ├── ROADMAP.md
        ├── CONTEXT.md
        ├── S01-PLAN.md
        └── S01/
            ├── T01-PLAN.md
            └── T01-SUMMARY.md
```

Every command reads state from disk, writes atomically via `tmp file + mv`, and never keeps state in conversation. This is how mikrós survives long sessions without context rot.

## The iron rule

**A task must fit in one context window. If it can't, it's two tasks.**

This is the single operational test for task granularity. `/plan-slice` refuses to emit a plan that violates it.

## Running the test suite

```bash
bash tests/run_all.sh
```

All tests are plain bash + python3 — no bats, no pytest, no build step. Every command, skill, hook, and template has a structural test.

## Design spec

Full design lives at `docs/superpowers/specs/2026-04-11-mikros-workflow-design.md` (planning artefact; not shipped in this repo).

## License

MIT. See `LICENSE`.

## Acknowledgements

- [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) — output token reduction, install dependency.
- [docmancer/docmancer](https://github.com/docmancer/docmancer) — local doc retrieval, install dependency.
- [gsd-build/gsd-2](https://github.com/gsd-build/gsd-2) — Milestone/Slice/Task hierarchy, iron rule, pre-loaded dispatch, must-haves format, VISION.md anti-pattern list (borrowed verbatim with attribution). MIT-licensed.
- [shanraisshan/claude-code-best-practice](https://github.com/shanraisshan/claude-code-best-practice) — Command → Agent → Skill orchestration, CLAUDE.md monorepo loading semantics, the "< 200 lines per CLAUDE.md" constraint, the Thariq/Boris best-practice tips.
