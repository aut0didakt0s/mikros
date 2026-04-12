# mikros

> mikros (ancient Greek: *small*)

A portable workflow template for AI coding assistants that prevents over-engineering and controls token cost. Works with **Claude Code** and **Gemini CLI**. Drop it into any project via `install.sh` and you get five slash commands, one subagent, two hooks, one anti-bloat skill, and a Python state machine. No harness, no TypeScript, no build step.

## The thesis

AI over-engineering is not a discipline problem. It's an absence-of-feedback problem.

mikros gives your AI assistant three kinds of feedback:

1. **simplicity-guard** — an anti-bloat skill with explicit anti-defaults (not generic "prefer simple" noise) plus a LOC budget hook that blocks edits exceeding the task budget. Distributed as a standalone package installable by either runtime.
2. **Hooks** — runtime-agnostic pre-tool-use and post-edit hooks that enforce lint, type-check, and LOC budget on every edit. Shared scripts work with both Claude Code and Gemini CLI.
3. **mikros.py** — a stdlib-only Python state machine that validates workflow transitions, advances tasks atomically, and writes summaries with crash safety (temp file + mv).

Optional plugins for additional feedback:
- [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) — output token reduction (~75% savings).
- [docmancer/docmancer](https://github.com/docmancer/docmancer) — local doc retrieval to ground the AI in version-specific APIs.

Both are fully optional. Their absence is a clean no-op.

## Install

```bash
git clone https://github.com/aut0didakt0s/mikros.git
cd mikros
./install.sh /path/to/your/project
```

The installer detects which runtimes are on PATH (`claude`, `gemini`, or both) and installs the appropriate config files. If neither is found, it installs both so you can pick later.

What it does:
- Copies `.claude/`, `.mikros/` templates, and `mikros.py` to the target
- Installs `CLAUDE.md` and/or `GEMINI.md` (runtime-specific project context)
- Installs `.gemini/settings.json` and `gemini-extension.json` if Gemini CLI is detected
- Seeds `.mikros/STATE.md`, `DECISIONS.md`, and config from templates
- Copies `simplicity-guard/` standalone directory
- Optionally installs caveman plugin and docmancer skill if available

## Prerequisites

- `bash` 4.0+, `git`, `python3` 3.8+, `jq`
- One of: `claude` CLI (Claude Code) or `gemini` CLI (Gemini CLI)
- `ruff` and `mypy` (used by the post-edit hook for Python files)

## The workflow

Every mikros project follows the same five steps per slice:

1. `/discuss <topic>` — capture intent before code. Writes `DECISIONS.md` and milestone context.
2. `/plan-slice <S##>` — decompose into tasks with must-haves. Enforces the iron rule.
3. `/execute-task <T##>` — run one task in a fresh `phase-builder` subagent with pre-loaded context.
4. `/sniff-test <id>` — mechanical must-haves check + human review gate. Squash-merges on approval.
5. `/compress <S##>` — deletion-first compression pass after merge.

State lives on disk under `.mikros/` and is managed by `mikros.py`:

```
.mikros/
├── STATE.md              # current pointer (milestone/slice/task/worktree)
├── PROJECT.md            # living project doc
├── DECISIONS.md          # append-only decision register
└── plans/
    └── M001/
        ├── CONTEXT.md
        ├── S01-PLAN.md
        └── S01/
            └── T01-SUMMARY.md
```

Every command reads state from disk, writes atomically via `tmp file + mv`, and never keeps state in conversation context. Each task runs in an isolated git worktree. This is how mikros survives long sessions without context rot.

## simplicity-guard

The anti-bloat skill is distributed as a standalone directory that works with both runtimes:

- **Claude Code:** Copy `simplicity-guard/` into `.claude/skills/`
- **Gemini CLI:** Reference `simplicity-guard/gemini-extension.json` in your settings

It enforces: no enterprise patterns, no premature abstractions (three-strikes rule), no deep nesting, LOC budgets, and the iron rule.

## The iron rule

**A task must fit in one context window. If it can't, it's two tasks.**

This is the single operational test for task granularity. `/plan-slice` refuses to emit a plan that violates it.

## License

MIT. See `LICENSE`.

## Acknowledgements

- [gsd-build/gsd-2](https://github.com/gsd-build/gsd-2) — Milestone/Slice/Task hierarchy, iron rule, pre-loaded dispatch, must-haves format, anti-pattern list (borrowed verbatim with attribution). MIT-licensed.
- [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) — optional output token reduction plugin.
- [docmancer/docmancer](https://github.com/docmancer/docmancer) — optional local doc retrieval plugin.
