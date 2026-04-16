# megalos MCP Server

Workflow-driven MCP server runtime. Built with FastMCP. This repo ships the runtime library (`megalos-server`) plus a single reference workflow (`example.yaml`). The production workflows live in separate domain repos — see below.

## Using the production workflows

This runtime has no hosted endpoint of its own. To use the production workflows, add one of the three domain-repo MCP servers as a connector:

| Domain | Workflows | MCP endpoint |
|--------|-----------|--------------|
| [megalos-writing](https://github.com/agora-creations/megalos-writing) | essay, blog | `https://megalos-writing.fastmcp.app/mcp` |
| [megalos-analysis](https://github.com/agora-creations/megalos-analysis) | research, decision | `https://megalos-analysis.fastmcp.app/mcp` |
| [megalos-professional](https://github.com/agora-creations/megalos-professional) | coding | `https://megalos-professional.fastmcp.app/mcp` |

Each domain repo's README has the exact connector-add commands for Claude web, Claude desktop, Claude Code, and Codex.

## How It Works

The server exposes 9 MCP tools: `list_workflows`, `start_workflow`, `get_state`, `get_guidelines`, `submit_step`, `revise_step`, `list_sessions`, `delete_session`, `generate_artifact`.

When you start a workflow, Claude walks you through each step in order. Tool responses include explicit directives telling Claude what to do (and what NOT to do) at each step.

## Workflows

The production workflows below are **not bundled with this repo** — each lives in its own domain repo listed in the "Using the production workflows" table.

| Workflow | Lives in | Category | Steps | Output |
|----------|----------|----------|-------|--------|
| coding   | megalos-professional | professional | 6 (discuss, plan, execute, review, iterate, deliver) | code |
| essay    | megalos-writing | writing_communication | 6 (explore, commit, structure, draft, revise, polish) | text |
| blog     | megalos-writing | writing_communication | 6 (angle, audience, outline, draft, revise, polish) | text |
| research | megalos-analysis | analysis_decision | 6 (frame, gather, evaluate, synthesize, structure, refine) | text |
| decision | megalos-analysis | analysis_decision | 6 (frame, options, tradeoffs, stress_test, decide, document) | text |

### Reference workflow: `example.yaml`

The only workflow bundled in this repo is [`megalos_server/workflows/example.yaml`](workflows/example.yaml) — a minimal 2-step reference (`clarify` → `respond`) used by the framework tests. It demonstrates the core schema features in the smallest viable form: required top-level fields, two step shapes, a `collect: true` step with an `output_schema`, and `step_description` on each step.

It is intentionally not production-grade. Authoring your own workflow? Read [`SCHEMA.md`](SCHEMA.md) for the YAML spec, then copy `example.yaml` as a starting shape and grow from there.

## Local Development

```bash
uv sync
uv run python -m megalos_server.main              # streamable HTTP on port 8000
uv run fastmcp inspect megalos_server/main.py:mcp # verify tools
```

Reads `FASTMCP_HOST` (default `127.0.0.1`) and `FASTMCP_PORT` (default `8000`) from env.

## Rate Limiting

For production deployments, configure rate limiting at the hosting/reverse-proxy layer:

- **Recommended:** 60 requests per minute per IP.
- This prevents runaway loops from LLM clients while allowing normal interactive use.
- Configure in your reverse proxy (nginx, Caddy, Cloudflare) rather than in the application.

## Docker

```bash
docker build -t megalos-mcp .
docker run -p 8000:8000 megalos-mcp
```
