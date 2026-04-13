# mikros MCP Server

Workflow-driven MCP server for structured workflows. Built with FastMCP, deployed to Prefect Horizon.

## Quick Start (2 minutes)

1. Go to [claude.ai](https://claude.ai) > Settings > Connectors > Add custom connector
2. Paste the server URL:
   ```
   https://Mikros.fastmcp.app/mcp
   ```
3. Done. Try one of these prompts:

**Coding:** "I want to build a CLI tool that converts CSV files to JSON"

**Essay:** "I want to write an essay about why remote work is better for deep work"

Claude will guide you through a structured workflow, one step at a time.

## How It Works

The server exposes 8 MCP tools: `list_workflows`, `start_workflow`, `get_state`, `get_guidelines`, `submit_step`, `list_sessions`, `delete_session`, `generate_artifact`.

When you start a workflow, Claude walks you through each step in order. Tool responses include explicit directives telling Claude what to do (and what NOT to do) at each step.

## Workflows

| Workflow | Category | Steps | Output |
|----------|----------|-------|--------|
| coding   | professional | 6 (discuss, plan, execute, review, iterate, deliver) | code |
| essay    | writing_communication | 6 (explore, commit, structure, draft, revise, polish) | text |
| blog     | writing_communication | 6 (angle, audience, outline, draft, revise, polish) | text |
| research | analysis_decision | 6 (frame, gather, evaluate, synthesize, structure, refine) | text |
| decision | analysis_decision | 6 (frame, options, tradeoffs, stress_test, decide, document) | text |

### Coding Workflow Steps

1. **Discuss** — Capture intent: goal, constraints, definition of done
2. **Plan** — Decompose into components and file structure
3. **Execute** — Write the implementation code
4. **Review** — Review code for correctness and simplicity
5. **Iterate** — Refine based on review feedback
6. **Deliver** — Produce the final artifact

### Essay Workflow Steps

1. **Explore** — Brainstorm and research the topic
2. **Commit** — Lock in a thesis and angle
3. **Structure** — Create an outline
4. **Draft** — Write the first draft
5. **Revise** — Improve clarity and argument strength
6. **Polish** — Final editing pass

## Try It

### Coding Example

You: "I want to build a CLI that converts CSV to JSON"

Claude will guide you through all 6 coding steps — from capturing your intent to delivering the final code.

### Essay Example

You: "I want to write about why remote work is better for deep work"

Claude will guide you through all 6 essay steps — from exploring angles to polishing the final draft.

## Local Development

```bash
uv sync
uv run python -m server.main          # streamable HTTP on port 8000
uv run fastmcp inspect server/main.py:mcp  # verify tools
```

Reads `FASTMCP_HOST` (default `127.0.0.1`) and `FASTMCP_PORT` (default `8000`) from env.

## Rate Limiting

For production deployments, configure rate limiting at the hosting/reverse-proxy layer:

- **Recommended:** 60 requests per minute per IP.
- This prevents runaway loops from LLM clients while allowing normal interactive use.
- Configure in your reverse proxy (nginx, Caddy, Cloudflare) rather than in the application.

## Docker

```bash
docker build -t mikros-mcp .
docker run -p 8000:8000 mikros-mcp
```
