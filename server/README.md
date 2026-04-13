# mikros MCP Server

Workflow-driven MCP server for coding and essay assistance. Built with FastMCP, deployed to Prefect Horizon.

## Quick Start (2 minutes)

1. Go to [claude.ai](https://claude.ai) > Settings > Connectors > Add custom connector
2. Paste the server URL:
   ```
   https://mikros-mcp.fastmcp.app/mcp
   ```
3. Done. Try one of these prompts:

**Coding:** "I want to build a CLI tool that converts CSV files to JSON"

**Essay:** "I want to write an essay about why remote work is better for deep work"

Claude will guide you through a structured workflow, one step at a time.

## How It Works

The server exposes 5 MCP tools: `start_workflow`, `submit_step`, `get_state`, `get_guidelines`, `generate_artifact`.

When you start a workflow, Claude walks you through each step in order. Tool responses include explicit directives telling Claude what to do (and what NOT to do) at each step.

## Workflows

| Workflow | Steps | Output |
|----------|-------|--------|
| coding   | 6 (discuss, plan, execute, review, iterate, deliver) | code |
| essay    | 6 (explore, commit, structure, draft, revise, polish) | text |

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

## Docker

```bash
docker build -t mikros-mcp .
docker run -p 8000:8000 mikros-mcp
```

## Deploy to Prefect Horizon

[Horizon](https://horizon.prefect.io) is managed MCP hosting with a free personal tier.

1. Push this repo to GitHub.
2. Visit [horizon.prefect.io](https://horizon.prefect.io), sign in with GitHub.
3. Select your repo.
4. Set **Entrypoint** to `server/main.py:mcp`, **Authentication** off.
5. Click **Deploy Server** (deploys in under 60 seconds).
6. Server URL: `https://mikros-mcp.fastmcp.app/mcp`

Auto-redeploys on push to `main`. Run `./deploy.sh --inspect` to validate before pushing.

## Compliance Test Protocol (SC5)

**Purpose:** Verify that Claude follows all workflow steps in order without skipping, at least 8 out of 10 times.

**What to check for each run:**
- Did Claude call `start_workflow` first?
- Did Claude complete ALL steps via `submit_step` in order?
- Did Claude skip any steps?
- Did Claude produce final output before the last step?
- Did Claude follow the directives at each step (e.g., not writing code during "discuss")?

**Pass criteria:** 8 or more of 10 runs must complete all steps in order.

### 10-Run Checklist

| Run | Workflow | Prompt | All Steps Followed? | Notes |
|-----|----------|--------|---------------------|-------|
| 1 | coding | "Build a CLI that converts CSV to JSON" | | |
| 2 | coding | "Create a Python script that monitors a folder for new files" | | |
| 3 | coding | "Build a REST API for a todo list with SQLite" | | |
| 4 | coding | "Write a markdown-to-HTML converter" | | |
| 5 | coding | "Create a command-line password generator" | | |
| 6 | essay | "Why remote work is better for deep work" | | |
| 7 | essay | "The case for learning a second language as an adult" | | |
| 8 | essay | "How open source software changed the tech industry" | | |
| 9 | essay | "Why writing by hand improves memory retention" | | |
| 10 | essay | "The hidden costs of multitasking in knowledge work" | | |
