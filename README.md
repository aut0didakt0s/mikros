# mikrós

> μικρός (ancient Greek: *small*)

**A platform for authoring deterministic AI conversations.** YAML is the source code. An MCP server is the runtime. The LLM is a replaceable text engine that gets constrained, not unleashed.

## Contents

- [Installation](#installation)
- [The thesis](#the-thesis)
- [Architecture — three layers](#architecture--three-layers)
- [The YAML schema](#the-yaml-schema)
- [The MCP server runtime](#the-mcp-server-runtime)
  - [Public API](#public-api)
  - [MCP tools (9)](#mcp-tools-9)
  - [Mechanical enforcement](#mechanical-enforcement)
  - [Session state](#session-state)
- [MCP servers](#mcp-servers)
- [Authoring a new domain repo](#authoring-a-new-domain-repo)
- [simplicity-guard](#simplicity-guard)
- [The iron rule](#the-iron-rule)
- [Optional plugins](#optional-plugins)
- [License](#license)
- [Acknowledgements](#acknowledgements)

## Installation

Add the mikrós MCP server as a connector in any MCP-compatible client — no install or local setup needed.

**Claude web**
```
https://Mikros.fastmcp.app/mcp
```

**Claude desktop**
```
https://Mikros.fastmcp.app/manifest.dxt?v=642267e3-c18f-42ae-b301-f18b234668a2
```

**Claude Code**
```
claude mcp add --scope local --transport http Mikros https://Mikros.fastmcp.app/mcp
```

**Codex**
```
codex mcp add --url https://Mikros.fastmcp.app/mcp Mikros
```

**Gemini CLI**
```
gemini mcp add Mikros https://Mikros.fastmcp.app/mcp --transport http
```

**Cursor**
```
cursor://anysphere.cursor-deeplink/mcp/install?name=Mikros&config=eyJ1cmwiOiJodHRwczovL01pa3Jvcy5mYXN0bWNwLmFwcC9tY3AifQ%3D%3D
```

**VS Code**
```
code --add-mcp "{\"name\":\"Mikros\",\"type\":\"http\",\"url\":\"https://Mikros.fastmcp.app/mcp\"}"
```

## The thesis

LLMs are more useful when constrained through authored conversational programs than when left to free-associate. The value isn't in making LLMs do *more* — it's in making them do *exactly what a workflow author intended*, step by step, with mechanical enforcement at each transition.

Determinism comes from gates in the runtime, not from prompt engineering. The MCP server rejects out-of-order step submissions, invalidates downstream data on revision, caps active sessions, and injects do-not rules into every directive. Any LLM that can read English and call MCP tools can execute the workflow — no provider adapter, no per-LLM prompt translation.

## Architecture — three layers

| Layer | What it is | Status |
|-------|------------|--------|
| **mikrós** (this repo) | YAML schema, MCP server runtime (`mikros-server` package), validation tooling, simplicity-guard | live |
| **mikrós-{domain}** | Per-domain MCP servers built on the runtime | live: writing, analysis, professional |
| **real-agora** | Bring-your-own-key chat client (web first, mobile later) | future |

## The YAML schema

The schema is the API surface. One YAML file = one workflow. Add a workflow by adding a file, not by writing code. Full reference in [`mikros_server/SCHEMA.md`](mikros_server/SCHEMA.md).

Top level: `schema_version` (optional, defaults to `0.1`), `name`, `description`, `category`, `output_format`, `steps`, optional `guardrails`.
Per step: `id`, `title`, `directive_template`, `gates`, `anti_patterns`, optional `output_schema`, `inject_context`, `directives`, `branches`, `intermediate_artifacts`.

Validate any workflow file:

```bash
python -m mikros_server.validate path/to/workflow.yaml
```

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
    "mikros-server @ git+https://github.com/real-agora/mikros.git@v0.1.0",
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

## MCP servers

Workflows are grouped by **category**, and each category lives in its own MCP server (a thin wrapper around `mikros-server` that exposes the workflows for that category). Mix and match — connect to one server, several, or all, depending on the kinds of work you want structured:

| Server | Category | Workflows | Remote |
|--------|----------|-----------|--------|
| `mikros-writing` | writing & communication | essay, blog | [github.com/real-agora/mikros-writing](https://github.com/real-agora/mikros-writing) |
| `mikros-analysis` | analysis & decision | research, decision | [github.com/real-agora/mikros-analysis](https://github.com/real-agora/mikros-analysis) |
| `mikros-professional` | professional | coding | [github.com/real-agora/mikros-professional](https://github.com/real-agora/mikros-professional) |

This repo itself bundles only `mikros_server/workflows/example.yaml` as a reference workflow plus `tests/fixtures/workflows/` (one canonical 3-step framework fixture plus seven demo fixtures exercising M004/M005 features). Production workflows live exclusively in their category-specific repos.

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
