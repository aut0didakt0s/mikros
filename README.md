# mikrós

> μικρός (ancient Greek: *small*)

**A platform for authoring deterministic AI conversations.** YAML is the source code. An MCP server is the runtime. The LLM is a replaceable text engine that gets constrained, not unleashed.

## The thesis

LLMs are more useful when constrained through authored conversational programs than when left to free-associate. The value isn't in making LLMs do *more* — it's in making them do *exactly what a workflow author intended*, step by step, with mechanical enforcement at each transition.

Determinism comes from gates in the runtime, not from prompt engineering. The MCP server rejects out-of-order step submissions, invalidates downstream data on revision, caps active sessions, and injects do-not rules into every directive. Any LLM that can read English and call MCP tools can execute the workflow — no provider adapter, no per-LLM prompt translation.

## Architecture — four layers

mikrós is the **platform layer**. The full picture, from the [autodidaktós vision spec](2026-04-13-autodidaktos-vision-and-refactor.md):

| Layer | What it is | Status |
|-------|------------|--------|
| **mikrós** (this repo) | YAML schema, MCP server runtime (`mikros-server` package), validation tooling, simplicity-guard | live |
| **mikrós-{domain}** | Per-domain MCP servers built on the runtime | live: writing, analysis, professional |
| **autodidaktós** | Bring-your-own-key chat client (web first, mobile later) | future |
| **megálos** | Terminal-first developer harness for structured coding workflows | future (currently embedded in this repo, slated for extraction in Phase F) |

## The YAML schema

The schema is the API surface. One YAML file = one workflow. Add a workflow by adding a file, not by writing code. Full reference in [`mikros_server/SCHEMA.md`](mikros_server/SCHEMA.md).

Top level: `schema_version` (optional, defaults to `0.1`), `name`, `description`, `category`, `output_format`, `steps`, optional `guardrails`.
Per step: `id`, `title`, `directive_template`, `gates`, `anti_patterns`, optional `output_schema`, `inject_context`, `directives`, `branches`, `intermediate_artifacts`.

Validate any workflow file:

```bash
python -m mikros_server.validate path/to/workflow.yaml
```

Cap on schema growth: it must NOT become Turing-complete. The moment it needs a debugger, it's failed. Target: a domain expert who knows YAML can author a workflow in under 30 minutes.

## The MCP server runtime

Distribution: `mikros-server`. Import: `mikros_server`. Two runtime dependencies: `fastmcp` and `pyyaml` (plus `jsonschema` for output validation).

### Public API

```python
from mikros_server import create_app

mcp = create_app(workflow_dir="./workflows")  # defaults to bundled example.yaml
mcp.run(transport="streamable-http")
```

Domain repos and downstream consumers depend on the runtime via a pinned git URL:

```toml
[project]
dependencies = [
    "mikros-server @ git+https://github.com/aut0didakt0s/mikros.git@v0.1.0",
]
```

Local development override: `pip install -e ../mikros`.

### MCP tools (9)

`list_workflows`, `start_workflow`, `get_state`, `get_guidelines`, `submit_step`, `revise_step`, `list_sessions`, `delete_session`, `generate_artifact`.

The server never calls any LLM. Zero LLM imports, zero provider references. Tool responses are plain dicts (`directive`, `gates`, `anti_patterns`, plus 7 global `_DO_NOT_RULES`).

### Mechanical enforcement

- `submit_step` rejects out-of-order submissions.
- `revise_step` invalidates all downstream `step_data`.
- `start_workflow` caps active sessions at 5 with TTL expiration.
- Step `output_schema` validates submission content via `jsonschema` with a configurable retry budget.
- Top-level `guardrails` evaluate keyword/count/revisit triggers and can `warn`, `force_branch`, or `escalate` (irrecoverable).
- `intermediate_artifacts` allow multi-checkpoint validation within a single step.
- `branches` let an LLM select a non-linear next step from a declared option set.

### Session state

In-memory dict store. Sessions have `session_id`, `workflow_type`, `current_step`, `step_data`, timestamps. Cap of 5 active sessions, TTL-based expiration. No SQLite, no external state store — keeps the runtime trivially deployable.

### Deployment

The repo ships with a `Dockerfile` and `deploy.sh` for [Prefect Horizon](https://horizon.prefect.io). Today's example deployment exposes mikros at `Mikros.fastmcp.app/mcp`. Each domain repo deploys independently the same way.

## Live domain servers

Three independent repos, each a thin wrapper around `mikros-server` that points at its own `workflows/` directory:

| Repo | Workflows | Remote |
|------|-----------|--------|
| `mikros-writing` | essay, blog | [github.com/aut0didakt0s/mikros-writing](https://github.com/aut0didakt0s/mikros-writing) |
| `mikros-analysis` | research, decision | [github.com/aut0didakt0s/mikros-analysis](https://github.com/aut0didakt0s/mikros-analysis) |
| `mikros-professional` | coding | [github.com/aut0didakt0s/mikros-professional](https://github.com/aut0didakt0s/mikros-professional) |

Each is structured identically: flat `main.py` at repo root, own `pyproject.toml` with the pinned mikros-server git dep, own `tests/`, own `Dockerfile` and `deploy.sh`. New domain repos follow the same template.

This repo bundles only `mikros_server/workflows/example.yaml` as a reference workflow and `tests/fixtures/workflows/` (one canonical 3-step framework fixture plus seven demo fixtures exercising M004/M005 features). Production workflows live exclusively in their domain repos.

## Authoring a new domain repo

The pattern is mechanical. Replicate one of the existing domain repos and:

1. Create an empty GitHub repo.
2. Clone, populate with `pyproject.toml` (pin to `mikros-server@vX.Y.Z`), flat `main.py` calling `create_app(workflow_dir=Path(__file__).parent / "workflows")`, your YAML files under `workflows/`, tests under `tests/` (with their own conftest constructing an mcp via `create_app(workflow_dir=...)`).
3. `pip install -e ".[test]" && pytest && python main.py` to verify.
4. Push.

A future authoring guide (Phase E in the vision spec) will document this end to end.

## simplicity-guard

The anti-bloat skill at `simplicity-guard/`. Standalone — works with both Claude Code and Gemini CLI:

- **Claude Code:** Copy `simplicity-guard/` into `.claude/skills/`
- **Gemini CLI:** Reference `simplicity-guard/gemini-extension.json` in your settings

Enforces explicit anti-defaults: no enterprise patterns, no premature abstractions (three-strikes rule), no dataclass for internal data, flat over nested config, boring beats clever, and the iron rule.

## The iron rule

**A task must fit in one context window. If it can't, it's two tasks.**

Operational test for task granularity. `/plan-slice` refuses to emit a plan that violates it. Compression is for *output*, not *task scope* — when a task feels too big, split it; never compress the surrounding context to fit.

## Optional plugins

- [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) — output token reduction (~75% savings).
- [docmancer/docmancer](https://github.com/docmancer/docmancer) — local doc retrieval to ground the AI in version-specific APIs.

Both are fully optional; their absence is a clean no-op.

## License

MIT. See `LICENSE`.

## Acknowledgements

- [gsd-build/gsd-2](https://github.com/gsd-build/gsd-2) — Milestone/Slice/Task hierarchy, iron rule, pre-loaded dispatch, must-haves format, anti-pattern list (borrowed verbatim with attribution). MIT-licensed.
- [Anthropic, "Code execution with MCP"](https://www.anthropic.com/engineering/code-execution-with-mcp) (November 2025) — source for progressive tool disclosure architecture, cited in the vision spec as a design constraint for the future autodidaktós client.
- [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) — optional output token reduction plugin.
- [docmancer/docmancer](https://github.com/docmancer/docmancer) — optional local doc retrieval plugin.
