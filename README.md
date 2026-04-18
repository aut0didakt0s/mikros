# megálos

> μεγάλος (ancient Greek: *big*, *great*)

**A platform for authoring deterministic AI conversations.** YAML is the source code. An MCP server is the runtime. The LLM is a replaceable text engine that gets constrained, not unleashed.

## Contents

- [The thesis](#the-thesis)
- [Architecture — three layers](#architecture--three-layers)
- [The YAML schema](#the-yaml-schema)
- [The MCP server runtime](#the-mcp-server-runtime)
  - [Public API](#public-api)
  - [MCP tools (10)](#mcp-tools-10)
  - [Mechanical enforcement](#mechanical-enforcement)
  - [Session state](#session-state)
- [MCP servers](#mcp-servers)
- [Authoring a new domain repo](#authoring-a-new-domain-repo)
- [simplicity-guard](#simplicity-guard)
- [mikrós — future agent-skills library](#mikrós--future-agent-skills-library)
- [The iron rule](#the-iron-rule)
- [Optional plugins](#optional-plugins)
- [License](#license)
- [Acknowledgements](#acknowledgements)

## The thesis

LLMs are more useful when constrained through authored conversational programs than when left to free-associate. The value isn't in making LLMs do *more* — it's in making them do *exactly what a workflow author intended*, step by step, with mechanical enforcement at each transition.

Determinism comes from gates in the runtime, not from prompt engineering. The MCP server rejects out-of-order step submissions, invalidates downstream data on revision, caps active sessions, and injects do-not rules into every directive. Any LLM that can read English and call MCP tools can execute the workflow — no provider adapter, no per-LLM prompt translation.

## Architecture — three layers

| Layer | What it is | Status |
|-------|------------|--------|
| **megálos** (this repo) | YAML schema, MCP server runtime (`megalos-server` package), validation tooling, simplicity-guard | live |
| **megálos-{domain}** | Per-domain MCP servers built on the runtime | live: writing, analysis, professional |
| **agora-creations** | Bring-your-own-key chat client (BYOK) | future |

## The YAML schema

The schema is the API surface. One YAML file = one workflow. Add a workflow by adding a file, not by writing code. Full reference in [`megalos_server/SCHEMA.md`](megalos_server/SCHEMA.md).

Top level: `schema_version` (optional, defaults to `0.1`), `name`, `description`, `category`, `output_format`, `steps`, optional `guardrails`.
Per step: `id`, `title`, `directive_template`, `gates`, `anti_patterns`, optional `output_schema`, `inject_context`, `directives`, `branches`, `intermediate_artifacts`.

Validate any workflow file:

```bash
python -m megalos_server.validate path/to/workflow.yaml
```

## The MCP server runtime

Distribution: `megalos-server`. Import: `megalos_server`. Two runtime dependencies: `fastmcp` and `pyyaml` (plus `jsonschema` for output validation).

### Public API

```python
from megalos_server import create_app

mcp = create_app(workflow_dir="./workflows")  # defaults to bundled example.yaml
mcp.run(transport="streamable-http")
```

Domain repos and downstream consumers depend on the runtime via a pinned git URL:

```toml
[project]
dependencies = [
    "megalos-server @ git+https://github.com/agora-creations/megalos.git@v0.3.0",
]
```

Local development override: `pip install -e ../megalos`.

## MCP servers

Workflows are grouped by **category**, and each category lives in its own MCP server (a thin wrapper around `megalos-server` that exposes the workflows for that category). Mix and match — connect to one server, several, or all, depending on the kinds of work you want structured:

| Server | Category | Workflows | Remote |
|--------|----------|-----------|--------|
| `megalos-writing` | writing & communication | essay, blog | [github.com/agora-creations/megalos-writing](https://github.com/agora-creations/megalos-writing) |
| `megalos-analysis` | analysis & decision | research, decision | [github.com/agora-creations/megalos-analysis](https://github.com/agora-creations/megalos-analysis) |
| `megalos-professional` | professional | coding | [github.com/agora-creations/megalos-professional](https://github.com/agora-creations/megalos-professional) |

This repo itself bundles only `megalos_server/workflows/example.yaml` as a reference workflow plus `tests/fixtures/workflows/` (one canonical 3-step framework fixture plus seven demo fixtures). Production workflows live exclusively in their category-specific repos.

### MCP tools

`list_workflows`, `start_workflow`, `get_state`, `get_guidelines`, `submit_step`, `revise_step`, `enter_sub_workflow`, `list_sessions`, `delete_session`, `generate_artifact`.

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

## Authoring a new domain repo

The pattern is mechanical. Replicate one of the existing domain repos and:

1. Create an empty GitHub repo.
2. Clone, populate with `pyproject.toml` (pin to `megalos-server@vX.Y.Z`), flat `main.py` calling `create_app(workflow_dir=Path(__file__).parent / "workflows")`, your YAML files under `workflows/`, tests under `tests/` (with their own conftest constructing an mcp via `create_app(workflow_dir=...)`).
3. `pip install -e ".[test]" && pytest && python main.py` to verify.
4. Push.

A future authoring guide will document this end to end.

## simplicity-guard

The anti-bloat skill at `simplicity-guard/`. Copy into your project at `.claude/skills/simplicity-guard/` for Claude Code to auto-discover.

Enforces explicit anti-defaults: no enterprise patterns, no premature abstractions (three-strikes rule), no dataclass for internal data, flat over nested config, boring beats clever, and the iron rule.

## mikrós — future agent-skills library

mikrós (ancient Greek: *small*) is a separate, future project: a lightweight agent-skills library for coding agents, inspired by [RasaHQ/rasa-agent-skills](https://github.com/RasaHQ/rasa-agent-skills). It will be a collection of markdown skill files that teach a coding agent how to author, test, and deploy megálos workflows. It is not a package, not a runtime, and not a deployment — just knowledge in the shape an agent can read. This repo (megálos) is the platform; mikrós is the way agents learn to use it.

## The iron rule

**A task must fit in one context window. If it can't, it's two tasks.**

Operational test for task granularity. `/plan-slice` refuses to emit a plan that violates it. Compression is for *output*, not *task scope* — when a task feels too big, split it; never compress the surrounding context to fit.

## License

MIT. See `LICENSE`.

## Acknowledgements

- [Rasa CALM](rasa.com/docs/learn/concepts/calm/) — Closest architectural analog. The central thesis is the same: "The LLM interprets what the user wants. The logic decides what happens next." YAML-defined flows, deterministic execution, separation of concerns. Enterprise-grade, proprietary runtime.
- [Anthropic, "Code execution with MCP"](https://www.anthropic.com/engineering/code-execution-with-mcp) (November 2025) — source for progressive tool disclosure architecture, taken as a design constraint for the future autodidaktós client.
