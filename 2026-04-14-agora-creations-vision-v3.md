# real-agorà — Vision, Architecture & Execution Plan (v3)

**Date:** 2026-04-14
**Status:** Strategic spec — **Phases A, B, C, D shipped** (D: 4/6 criteria passed, 2/6 BLOCKED on Horizon paid-tier); D.5–H pending
**Author:** Diego Marono
**Repo:** `github.com/agora-creations/megalos`

> **Execution status (2026-04-14):** Phases A, B, and C of §6 are complete and merged to `main`. The runtime package is distributed as `megalos-server` (not `mikros-engine`). The three domain repos (`megalos-writing`, `megalos-analysis`, `megalos-professional`) are live under `github.com/agora-creations/`. Domain MCP servers deployed at `https://horizon.prefect.io/alvy-singer/servers`. Remaining: Phase D.5 (Horizon remix deployment), Phase D.5 (CALM-informed schema enrichment), Phase E (authoring guide), Phase F (megálos extraction), Phases G–H (agora-creations client).

> **What changed during development:** Discovery of Rasa CALM as closest architectural analog. CALM validates megálos's core thesis ("LLM interprets, code enforces") and provides concrete patterns worth adopting: `collect` step semantics, default conversation repair behaviors, and flow-description best practices. A new Phase D.5 is inserted between deployment and the authoring guide to absorb these learnings. No architectural refactoring required — the changes are additive. Competitive positioning updated. Reference material expanded.

> **What changed in this revision (v3):** FastMCP Client validated as the MCP protocol layer for the agora-creations client (Phase G). FastMCP Client is the programmatic MCP client from the same team (Prefect) that builds the server framework mikrós already depends on and the Horizon platform mikrós deploys to. Assessment grounded in official documentation at `gofastmcp.com`. Decision §9.7 updated: Phase G builds on FastMCP Client + LLM provider SDKs, not raw Messages API. Decision §9.8 added. §3.3, §4.5, Phase G updated.

-----

## 1. The Thesis

LLMs are more useful when constrained through authored conversational programs than when left to free-associate. The value isn't in making LLMs do *more* — it's in making them do *exactly what a workflow author intended*, step by step, with mechanical enforcement at each transition.

This project builds a **platform for authoring deterministic AI conversations**. YAML is the source code. An MCP server is the runtime. The LLM is a replaceable text engine that gets constrained, not unleashed.

**Validation from the field:** Rasa's CALM architecture (Conversational AI with Language Models), shipping in Rasa Pro since 2024, independently arrived at the same thesis: "The LLM interprets what the user wants. The logic decides what happens next. That separation keeps agents fast and predictable." CALM defines conversation programs as YAML flows, uses an LLM only for dialogue understanding, and executes business logic deterministically. mikrós and CALM share the thesis; they differ on delivery (MCP-native vs. proprietary runtime), complexity (2-dependency vs. enterprise platform), and target author (domain expert vs. conversation designer with a no-code studio).

-----

## 2. Current Codebase — Ground Truth

The following is the actual state of the repo at the latest commit.

### 2.1 What exists today

**MCP Server** (`server/`):

- `server/main.py` — FastMCP entry point. Loads all YAML files from `server/workflows/` at startup, registers tools via `register_tools(mcp, WORKFLOWS)`. Runs streamable HTTP transport.
- `server/tools.py` — 9 MCP tools: `list_workflows`, `start_workflow`, `get_state`, `get_guidelines`, `submit_step`, `revise_step`, `list_sessions`, `delete_session`, `generate_artifact`. The server **never calls any LLM** — it only manages state and returns directives. This is the key architectural fact.
- `server/state.py` — In-memory session store (dict-based). Sessions have: `session_id`, `workflow_type`, `current_step`, `step_data`, timestamps. Cap of 5 active sessions. TTL-based expiration.
- `server/schema.py` — YAML parser + validator. Enforces top-level keys (`name`, `description`, `category`, `output_format`, `steps`, optional `schema_version`, `guardrails`) and per-step keys (`id`, `title`, `directive_template`, `gates`, `anti_patterns`, optional `output_schema`, `branches`, `inject_context`, `directives`, `intermediate_artifacts`).
- `server/validate.py` — CLI for offline YAML validation.

**Workflow YAMLs** (migrated to domain repos):

- `megalos-writing`: essay (6 steps), blog (6 steps). Category: `writing_communication`.
- `megalos-analysis`: research (6 steps), decision (6 steps). Category: `analysis_decision`.
- `megalos-professional`: coding (6 steps). Category: `professional`.
- Core repo retains a single `example.yaml` as reference.

**Terminal Runtime** (project root, future megálos):

- `megalos.py` — 357-line stdlib-only Python state machine for terminal workflows (Claude Code / Gemini CLI). Manages `.megalos/STATE.md`, `DECISIONS.md`, task advancement, atomic writes.
- `CLAUDE.md` — Project memory template for Claude Code sessions.
- `GEMINI.md` — Equivalent for Gemini CLI.
- `install.sh` — Copies templates into target projects, detects available runtimes.

**simplicity-guard** (`simplicity-guard/`):

- `SKILL.md` — Anti-bloat skill: iron rule, LOC budget, three-strikes rule, boring-is-a-feature.
- `references/anti-patterns.md` — 14 explicit anti-defaults. Attributed to GSD-2 VISION.md.
- `references/gotchas.md` — Append-only learning file for failure modes.

**Infrastructure:**

- `Dockerfile` — Python 3.12-slim + uv, exposes port 8000.
- `pyproject.toml` — 3 runtime dependencies: `fastmcp>=3.2.3`, `pyyaml>=6.0`, `jsonschema>=4.23.0`.
- Deployed to Prefect Horizon at `Mikros.fastmcp.app/mcp`.
- 25+ test files covering tools, workflows, sessions, integration, hooks, and commands.

### 2.2 What the code proves

1. **The MCP server is provider-agnostic by design.** `tools.py` has zero LLM imports, zero provider references, zero API calls. Any LLM that can read directives and call MCP tools can execute the workflow.
2. **Determinism comes from mechanical enforcement, not prompt engineering.** `submit_step` rejects out-of-order submissions. `revise_step` invalidates downstream data. `start_workflow` caps sessions. These are code gates, not suggestions.
3. **The 7 global `_DO_NOT_RULES` are provider-agnostic.** Plain English instructions injected into every step response. Any LLM follows them.
4. **The YAML schema is already the API surface.** `SCHEMA.md` documents it. `validate.py` enforces it. New workflows are added by creating a YAML file, not by writing code.
5. **The MCP tool vocabulary is the command vocabulary.** Unlike Rasa CALM, which parses LLM output into structured commands (`start flow`, `set slot`), mikrós uses MCP tool calls directly as its command interface. `submit_step`, `revise_step`, `get_state` — these are the commands. No parsing layer needed.

-----

## 3. Product Architecture — Four Layers

### 3.1 mikrós (this repo) — The Platform

The YAML schema, the MCP server runtime, the validation tooling, and the simplicity-guard philosophy. This is the core that everything else builds on.

**What stays in this repo:**

- `server/` — the generic workflow engine (main, tools, state, schema, validate)
- `server/SCHEMA.md` — the schema spec (versioned, this is the API surface)
- `simplicity-guard/` — the anti-bloat skill (standalone, works with any runtime)
- `pyproject.toml`, `Dockerfile`, `deploy.sh` — infrastructure
- `tests/` — framework-level tests
- **Authoring Guide** — documentation teaching anyone to write a workflow YAML from scratch (Phase E)

**What moves out:**

- `megalos.py`, `CLAUDE.md`, `GEMINI.md`, `install.sh` → megálos repo (Phase F)
- Workflow-specific tests → already moved with domain repos (Phase C, done)

### 3.2 megálos-{domain} (separate repos) — Domain MCP Servers

Each repo contains workflow YAMLs for a specific domain, plus a thin `main.py` that imports the mikrós runtime and points it at the local `workflows/` directory.

| Repo | Workflows (current + planned) | Status |
|------|-------------------------------|--------|
| `megalos-writing` | essay, blog, academic paper, technical documentation, grant proposal, creative fiction | essay + blog shipped |
| `megalos-analysis` | research, decision, competitive analysis, financial analysis | research + decision shipped |
| `megalos-strategy` | business plan, product spec/PRD, project roadmap, marketing campaign | All new — validate before committing |
| `megalos-learning` | concept tutorial, study guide, interview prep, skill assessment | All new — strong structural case |
| `megalos-creative` | brainstorming/ideation, worldbuilding, design brief | Borderline — validate with users first |
| `megalos-professional` | coding, cover letter/resume, consulting deliverable | coding shipped |

**Key filter:** Does a structured multi-step workflow add value over free-form chat? If the answer is no, don't build it.

**Dropped from scope** (free-form chat is sufficient): email, meeting agenda, recipe, travel itinerary, event planning, newsletter, speech scripts.

**Each domain repo structure:**

```
megalos-writing/
├── workflows/
│   ├── essay.yaml
│   ├── blog.yaml
│   └── ...
├── main.py          # imports megalos runtime, points to ./workflows/
├── pyproject.toml   # depends on megalos-server @ git+...
├── Dockerfile
├── deploy.sh
├── README.md
└── tests/
```

**Deployment:** Each repo deploys independently to Prefect Horizon. Horizon Remix composes them into governed domain bundles.

### 3.3 agora-creations (proprietary client) — FUTURE

A purpose-built chat interface optimized for megálos workflow consumption. BYOK (bring your own key) only — users provide their own LLM API keys. The platform never pays for LLM inference.

**Architecture (updated v3):** Three layers, one framework.

| Layer | Component | Source |
|-------|-----------|--------|
| **MCP protocol** | FastMCP Client | `fastmcp` (already a dependency — same package as the server runtime) |
| **LLM routing** | Provider SDKs via FastMCP optional extras | `pip install fastmcp[anthropic]`, `fastmcp[openai]`, `fastmcp[gemini]` |
| **UI** | Thin web client (Phase 1) / React Native (Phase 2) | Custom |

**Why FastMCP Client:** See §4.5 for the full grounded assessment.

**Phase 1 — Web client** (after traction): Lightweight web app. FastMCP Client handles all MCP protocol plumbing (multi-server composition, transport negotiation, tool namespacing, connection lifecycle). The user provides their API key; FastMCP's built-in sampling handlers route to their chosen provider. The web layer is just chat UI + key vault + workflow discovery.

**Phase 2 — Mobile app** (after web validation): React Native / Expo for iOS + Android.

#### 3.3.1 Progressive Tool Disclosure — a founding design principle

As the number of megálos domain servers grows, the agora-creations client will connect to multiple Horizon endpoints simultaneously. This creates the token-efficiency problem documented by Anthropic's engineering team in their November 2025 article *"Code execution with MCP: Building more efficient agents"*.

**Design principle:** Progressive tool disclosure from day one. The client must not load all tool definitions upfront.

**Implementation approach (updated v3 — grounded in FastMCP docs):**

1. **Discovery-first via CodeMode.** FastMCP 3.x ships a CodeMode transform (`gofastmcp.com/servers/transforms/code-mode`) that replaces the full tool catalog with meta-tools: `search` (find tools by keyword), `get_schema` (inspect a specific tool), and `execute` (run a script that chains tool calls in a sandbox). This is exactly the progressive disclosure pattern from Anthropic's November 2025 article, built into the framework we already depend on. Per FastMCP docs: "Instead of seeing your entire tool catalog, the LLM gets meta-tools for discovering what's available."
2. **Tag-based filtering via multi-server config.** FastMCP Client's multi-server configuration supports `include_tags` and `exclude_tags` at the server level (`gofastmcp.com/clients/transports#tool-transformations`). Domain servers can tag tools by workflow phase (e.g., `discovery`, `execution`), and the client loads only the discovery-phase tools at cold start.
3. **One-shot workflow loading.** Once the LLM identifies a domain (e.g., "essay"), the client reconfigures to load that domain's full tool chain. FastMCP Client's tool namespacing (`weather_get_forecast`, `assistant_answer_question`) keeps tools from different servers unambiguous.
4. **Context-efficient tool results.** mikrós tool responses are already small (~500 tokens per step). External MCP server results must be filtered before entering context.

**When this matters:** Phase G (web client) and Phase H (mobile app). Not relevant to Phases A–F, where users consume megálos through existing MCP-compatible clients. But the architecture must be designed now, even if implementation is deferred.

### 3.4 mikrós (separate repo) — FUTURE

> μικρός (ancient Greek: *small*)

A lightweight agent-skills library that teaches AI coding agents how to author, test, validate, and deploy megálos workflows. Inspired directly by `RasaHQ/rasa-agent-skills` — versioned, markdown-packaged instructions following the Agent Skills format that any coding agent (Claude Code, Codex, Gemini CLI) can consume.

mikrós is not a runtime, not a framework, not a CLI. mikrós is the set of skills that make the platform learnable by machines.

**Primary reference:** `github.com/RasaHQ/rasa-agent-skills` — Rasa's collection of skills for AI coding agents working with Rasa CALM. Each skill has semver versioning and a `rasa_version` compatibility field. mikrós follows the same pattern: versioned skills with a `megalos_version` compatibility field tracking which platform version the skill applies to.

**What mikrós contains:**

- **Agent skills** (the core product): `authoring-workflows` (how to write a megálos YAML from scratch), `validating-workflows` (how to run the validator and fix errors), `deploying-workflows` (how to create a domain repo and deploy to Horizon), `testing-workflows` (how to write pytest tests using `create_app()`), and `simplicity-guard` (the anti-bloat skill: iron rule, LOC budget, three-strikes, anti-patterns).
- **Terminal integration glue** (migrated from the megálos repo): `mikros.py` (the 357-line state machine), `CLAUDE.md`/`GEMINI.md` templates, `install.sh`, slash commands (`/discuss`, `/plan-slice`, `/execute-task`, `/sniff-test`, `/compress`), and terminal-specific tests. These are small, coding-agent-oriented tools — they belong in the small repo.

**What mikrós inherits from megálos:** The YAML schema (mikrós teaches it, megálos defines it), the MCP server runtime (mikrós teaches how to deploy it, megálos implements it), the simplicity-guard philosophy (mikrós carries it forward as an agent skill).

**What mikrós does NOT do:** Run workflows. Manage sessions. Enforce gates. Validate schemas at runtime. That's all megálos. mikrós is documentation-as-code — skills that an AI reads and acts on, not software that executes.

**Competitive positioning:** No direct competitor exists. `RasaHQ/rasa-agent-skills` is the closest analog but teaches Rasa CALM specifically. mikrós would be the first agent-skills library for a generic YAML-to-MCP workflow platform. The terminal integration (slash commands, worktree isolation, GSD-2 hierarchy) gives it a secondary identity as a coding-workflow tool, but the primary product is the skills themselves.

**When to build:** After the megálos platform is stable and the Authoring Guide (Phase E) is written. The authoring guide validates the content as a human-readable document; mikrós repackages it for agent consumption. The terminal pieces move in the same phase since they're already built — it's a migration, not new development.

-----

## 4. The Core Architecture — No Provider Adapter

### 4.1 Why no adapter layer

The MCP server already is the deterministic layer. Determinism comes from mechanical gate enforcement in `submit_step`, not from controlling how the LLM is prompted.

What the current code does:

- `submit_step` rejects out-of-order submissions: `if step_id != current: return error`
- `revise_step` invalidates all downstream `step_data` entries
- `start_workflow` caps active sessions at 5
- Every tool response includes `directive`, `gates`, `anti_patterns`, and 7 global `_DO_NOT_RULES`

These are code-level gates. They work regardless of which LLM generates the text. A provider adapter would be over-engineering.

### 4.2 How it actually works (grounded in code)

```
User's device (web browser / mobile / terminal)
        │
        │  1. User picks a workflow and sends a message
        │
        ▼
┌──────────────────────────────────────────────────┐
│              LLM Provider (user's key)            │
│         Claude / GPT / Gemini / local model       │
│                                                    │
│  Receives: directive_template + gates +            │
│            anti_patterns + do_not rules +          │
│            user's message                          │
│                                                    │
│  Returns: natural language response                │
│           (constrained by directives)              │
└───────────────────────┬──────────────────────────┘
                        │
                        │  2. LLM calls submit_step
                        │     via MCP tool use
                        │
                        ▼
┌──────────────────────────────────────────────────┐
│         megálos MCP Server (Horizon)               │
│         megalos.fastmcp.app/mcp                    │
│                                                    │
│  server/tools.py:                                  │
│    submit_step(session_id, step_id, content)       │
│      → if step_id != current: REJECT               │
│      → if output_schema: validate via jsonschema   │
│      → if valid: advance to next step              │
│      → return next directive + gates + do_not      │
│                                                    │
│  server/state.py:                                  │
│    In-memory session store                         │
│    Atomic state transitions                        │
│    TTL-based expiration                            │
│                                                    │
│  server/workflows/*.yaml:                          │
│    The conversational programs                     │
│    (YAML is the source code)                       │
└──────────────────────────────────────────────────┘
```

The MCP server never touches the LLM. The LLM never touches the state machine. They're completely decoupled. Enforcement happens at the gate, not at the prompt.

### 4.3 BYOK only — always

The platform never pays for LLM inference. Users provide their own API keys. Zero LLM cost for the operator. No vendor lock-in. No subscription complexity.

### 4.4 Comparison with Rasa CALM

megálos and CALM solve the same problem with different trade-offs:

| Concern | megálos | Rasa CALM |
|---------|--------|-----------|
| **Delivery** | MCP server (any client) | Proprietary runtime (Rasa infrastructure) |
| **Flow definition** | YAML (5 top-level fields, 5+ per-step fields) | YAML (flows.yml with collect, action, set_slots, branching, linking) |
| **LLM ↔ Logic interface** | MCP tool calls (submit_step, revise_step, etc.) | Structured command parsing (start flow, set slot, cancel flow) |
| **Conversation repair** | Not yet — planned for Phase D.5 | Built-in patterns (digressions, corrections, clarifications, cancellations) |
| **Gate enforcement** | Server-side: ordering, schema validation, session caps | Server-side: FlowPolicy, deterministic dialogue management |
| **Target author** | Domain expert who knows YAML (30-min onboarding) | Conversation designer with Rasa Studio (no-code) or YAML |
| **Dependencies** | 3 (fastmcp, pyyaml, jsonschema) | Full enterprise platform (NLU pipeline, action server, LLM providers, Qdrant, etc.) |
| **Provider lock-in** | None (any MCP client, any LLM) | LLM-agnostic but Rasa-runtime-locked |
| **Open source** | Yes (core) | Partially (open source framework, Pro is proprietary) |

**What to adopt from CALM:** `collect` step semantics, default conversation repair patterns, flow-description best practices for the authoring guide.

**What NOT to adopt from CALM:** Full flow syntax complexity (`collect`, `action`, `set_slots`, `link`, `call`, autonomous steps), NLU pipeline, no-code studio, enterprise deployment model. Simplicity is mikrós's competitive advantage — don't erode it.

### 4.5 FastMCP Client — Assessment for Phase G (NEW in v3)

**Question:** What serves as the MCP protocol layer in the agora-creations client? Three options were evaluated: (a) build a raw MCP client from scratch using the low-level `mcp` Python SDK, (b) adopt a third-party open-source chat client (LibreChat, Open WebUI, AnythingLLM) and connect mikrós as a backend, (c) use FastMCP Client from the same framework the server already runs on.

**Decision: FastMCP Client (option c).** Rationale grounded in documentation follows.

#### 4.5.1 Why FastMCP Client fits

**Zero new dependencies.** megálos already depends on `fastmcp>=3.2.3` for the server runtime. The Client class ships in the same package — `from fastmcp import Client`. No new dependency added to `pyproject.toml`. The optional LLM provider extras (`pip install fastmcp[anthropic,openai,gemini]`) are additive and user-selected.

**Designed for deterministic, controlled interactions.** The FastMCP docs state explicitly: "The FastMCP Client is designed for deterministic, controlled interactions rather than autonomous behavior, making it ideal for [...] building deterministic applications that need reliable MCP interactions, and creating the foundation for agentic or LLM-based clients with structured, type-safe operations." (`gofastmcp.com/clients/client`). This is the mikrós philosophy: constrain, don't unleash.

**Multi-server composition is native.** Since v2.4.0, the Client accepts a configuration dictionary with multiple named servers. Tools are automatically namespaced by server name (e.g., `writing_submit_step`, `analysis_list_workflows`). This directly replaces the Horizon Remix server that was BLOCKED on the paid tier — the client composes servers itself, no Remix needed.

```python
# Exact pattern from FastMCP docs (gofastmcp.com/clients/transports)
config = {
    "mcpServers": {
        "writing": {"url": "https://megalos-writing.fastmcp.app/mcp"},
        "analysis": {"url": "https://megalos-analysis.fastmcp.app/mcp"},
        "professional": {"url": "https://megalos-professional.fastmcp.app/mcp"}
    }
}
client = Client(config)

async with client:
    tools = await client.list_tools()
    # Returns: writing_list_workflows, writing_submit_step, ...
    #          analysis_list_workflows, analysis_submit_step, ...
    result = await client.call_tool("writing_start_workflow", {"workflow_type": "essay"})
```

**Progressive tool disclosure via CodeMode.** FastMCP 3.x ships CodeMode (`gofastmcp.com/servers/transforms/code-mode`), which replaces the full tool catalog with three meta-tools: `search`, `get_schema`, `execute`. Per the docs: "Instead of seeing your entire tool catalog, the LLM gets meta-tools for discovering what's available and for writing and executing code that calls the tools it needs. It discovers on demand, writes a script that chains tool calls in a sandbox, and gets back only the final answer." This is the same progressive disclosure approach described in Anthropic's November 2025 article and in §3.3.1 of this spec — now a built-in framework feature rather than custom engineering.

**Tag-based tool filtering.** Multi-server configuration supports `include_tags` and `exclude_tags` at the server level. This enables the one-shot workflow loading pattern from §3.3.1: start with discovery-tagged tools only, then load a domain's full set once identified.

```python
# From FastMCP docs (gofastmcp.com/clients/transports#tool-transformations)
config = {
    "mcpServers": {
        "writing": {
            "url": "https://megalos-writing.fastmcp.app/mcp",
            "include_tags": ["discovery"]
        }
    }
}
```

**Tool transformations for schema control.** Tools can be renamed, have descriptions overridden, and have arguments hidden or defaulted — all from configuration, no code changes to the server. This enables the client to present a cleaner, more LLM-friendly interface over raw mikrós tools without touching `tools.py`.

**Built-in LLM sampling handlers.** FastMCP provides built-in sampling handlers for Anthropic, OpenAI, and Gemini APIs that support the full sampling API including tool use (`gofastmcp.com/clients/sampling`). This means provider routing is handled by the framework, not custom code. Users install the extra for their provider; the handler converts between MCP sampling protocol and the provider's native API.

**Same team, same platform.** FastMCP is maintained by Prefect (`github.com/PrefectHQ/fastmcp`). Horizon is Prefect's deployment platform. megálos deploys on Horizon. The entire stack — server framework, client framework, deployment platform — comes from one organization. Ecosystem coherence without vendor lock-in (FastMCP is Apache 2.0).

#### 4.5.2 What FastMCP Client does NOT provide

FastMCP Client is a programmatic Python SDK, not a finished product. The agora-creations client still needs to build:

- **Chat UI** — web interface for conversations (React/Next.js or similar)
- **API key vault** — secure local storage and routing for user-provided LLM keys
- **Workflow discovery UX** — onboarding flow that helps users find and start workflows
- **Conversation history** — persistence of chat sessions (FastMCP Client manages MCP session state, not chat history)
- **Agent loop** — the orchestration that reads LLM responses, detects tool calls, routes them through FastMCP Client, and feeds results back to the LLM

These are the thin-client responsibilities from §3.3. FastMCP Client eliminates the MCP protocol layer; the UI and agent loop remain custom work.

#### 4.5.3 Alternatives considered and rejected

**Claude Managed Agents** (`platform.claude.com/docs/en/managed-agents/overview`). Anthropic's hosted agent runtime with multi-MCP composition and managed sandbox containers. Rejected because: (a) inference bills through the agent owner's Anthropic account, contradicting §4.3 BYOK thesis, (b) locks to Claude as the LLM provider, contradicting provider-agnostic principle, (c) designed for long-running autonomous agent sessions, which is not the mikrós use case — mikrós workflows are short, deterministic, step-by-step conversations.

**LibreChat / Open WebUI / AnythingLLM.** Open-source chat clients with BYOK and MCP support. Rejected as primary architecture because: (a) no control over progressive tool disclosure — these clients load all tools upfront, (b) their MCP implementations vary in maturity (Open WebUI requires a separate proxy server), (c) adopting a full chat platform as a dependency creates coupling that conflicts with simplicity-guard philosophy. However, these remain valid as interim validation vehicles before Phase G ships.

**Raw MCP Python SDK** (`github.com/modelcontextprotocol/python-sdk`). The low-level SDK from which FastMCP Client is built. Rejected because: (a) requires manual transport negotiation, connection lifecycle, and protocol handling that FastMCP Client abstracts, (b) no built-in multi-server composition, (c) no built-in tool transformations or tag filtering. Using the raw SDK when FastMCP Client exists is unnecessary complexity.

-----

## 5. YAML Schema — The API Surface

The schema is the product. Documented in `server/SCHEMA.md`, enforced by `server/schema.py`, validated by `server/validate.py`.

### 5.1 Current schema (v0.1, shipped with Phase A)

**Top-level fields:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string | yes | Unique workflow identifier |
| `description` | string | yes | Plain-English purpose statement |
| `category` | string | yes | Domain grouping |
| `output_format` | string | yes | `text` or `structured_code` |
| `steps` | list | yes | Ordered list of step mappings (≥1) |
| `schema_version` | string | no | Defaults to `0.1` |
| `guardrails` | list | no | Top-level rules: keyword match, step count, loop detection, escalation |

**Per-step fields:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | yes | Unique identifier, used by `submit_step` for ordering enforcement |
| `title` | string | yes | Human-readable name (hidden from user per `_DO_NOT_RULES`) |
| `directive_template` | string | yes | Plain-English instructions sent to the LLM. Provider-agnostic. |
| `gates` | list[string] | yes | Conditions for step completion. LLM-interpreted, not programmatically enforced. |
| `anti_patterns` | list[string] | yes | Behaviors the LLM must avoid. Returned per step via `get_guidelines`. |
| `output_schema` | object | no | JSON Schema validation on `submit_step`. Server rejects non-conforming submissions. |
| `branches` | list | no | Adaptive branching: LLM selects next step from declared options. |
| `inject_context` | list | no | Server-assembled context from prior steps, pushed at transition. |
| `directives` | object | no | Structured behavioral shaping: `tone`, `strategy`, `constraints`, `persona`. |
| `intermediate_artifacts` | list | no | Multi-checkpoint validation within a single step. |

**Server-side enforcement** (mechanical, not advisory):

- `submit_step` rejects out-of-order submissions
- `submit_step` validates `output_schema` via `jsonschema` when present
- `revise_step` invalidates all downstream step data
- `start_workflow` caps active sessions at 5
- Sessions have TTL-based expiration
- 7 global `_DO_NOT_RULES` injected into every step response

### 5.2 Schema evolution — planned additions

**v0.2 additions (Phase D.5 — CALM-informed enrichment):**

- `collect` flag per step — marks a step as "gathering a specific piece of information." When `collect: true`, `output_schema` becomes mandatory. The runtime provides richer, field-level error messages on validation failure. This is the single most useful pattern from Rasa CALM: making "ask the user for X" a first-class operation.
- `conversation_repair` top-level — default behaviors for handling conversational disruptions. Baked into the runtime as defaults, overridable per workflow:
  - `on_go_back`: "Use `revise_step` to return to a previous step."
  - `on_cancel`: "Use `delete_session` to end the workflow."
  - `on_digression`: "Acknowledge the user's topic change, then redirect back to the current step."
  - `on_clarification`: "Re-explain the current step's directive in simpler terms."
- `step_description` per step — a concise, action-oriented summary of the step's purpose (distinct from `directive_template`, which contains full instructions). Used by the LLM for flow navigation and by the authoring guide as a best practice. Inspired by CALM's flow descriptions, which are critical for the LLM to understand when to trigger a flow.

**v1 additions (Phase E and beyond — already planned, unchanged):**

- `input_type` per step — hints for the client about expected input (free text, choice, structured)
- `optional` flag per step — allows skipping based on context
- `condition` per step — simple branching (if step X produced Y, skip this step)
- Sub-workflows (invoke another YAML as a step) — v2, deferred

**Expressiveness ceiling (unchanged):** The schema must NOT become Turing-complete. The moment it needs a debugger, it's failed. Target: a domain expert who knows YAML can author a workflow in under 30 minutes.

-----

## 6. Execution Plan

### Phase A — Schema stabilization ✅ (shipped M004–M006, 2026-04-13 → 2026-04-14)

- [x] Version the schema explicitly (`schema_version` optional, defaults to `0.1`)
- [x] Document the 7 global `_DO_NOT_RULES` as part of the schema
- [x] Decided: `gates` remain LLM-interpreted text; sequencing is mechanical via `submit_step`
- [x] Added adaptive branching, guardrails (warn/force_branch/escalate), intermediate artifacts, output_schema validation via jsonschema

### Phase B — Runtime extraction ✅ (shipped M006/S01, 2026-04-14)

- [x] Runtime distributed as `megalos-server` (import: `megalos_server`)
- [x] `create_app(workflow_dir=...)` factory replaces hardcoded path
- [x] Three runtime deps: `fastmcp`, `pyyaml`, `jsonschema`
- [x] Built-in workflows reduced to single reference `example.yaml`

### Phase C — Repo split ✅ (shipped M007/S01, 2026-04-14)

- [x] `megalos-writing` live at `github.com/agora-creations/megalos-writing` (essay, blog)
- [x] `megalos-analysis` live at `github.com/agora-creations/megalos-analysis` (research, decision)
- [x] `megals-professional` live at `github.com/agora-creations/megalos-professional` (coding)
- [x] Each domain repo: own pyproject.toml, Dockerfile, deploy.sh, tests with conftest constructing its own mcp
- [x] Domain repos depend on runtime via pinned git URL

### Phase D — Horizon deployment ✅ (shipped M008/S01, 2026-04-14; 4/6 criteria passed, 2/6 BLOCKED on paid-tier)

- [x] Deploy `megalos-writing` to Horizon as first standalone domain server (T04)
- [x] Deploy `megalos-analysis` as second domain server (T04; `megalos-professional` also deployed)
- [ ] ❌ BLOCKED Create a Horizon Remix server composing writing + analysis — Remix is paid-only on Horizon free tier (T05; see `.megalos/DECISIONS.md` 2026-04-14). **Superseded:** FastMCP Client multi-server composition (§4.5.1) replaces Remix for the agora-creations client. Remix remains optional for Horizon-native users on paid tier.
- [x] Test with non-technical users through Horizon Agents web interface — ran end-to-end via Claude Desktop against the Horizon endpoint (Fork B, post-T05 block); essay workflow completed without server error (T06).
- [x] Document the deployment workflow for future domain repos (T02; `M008/T02` deploy how-to)
- [ ] ❌ BLOCKED Test provider agnosticism: run the essay workflow through Claude AND GPT without code changes — Horizon free tier does not expose a backing-LLM swap (T06; deferred to paid-tier upgrade or non-Horizon host).

**Executed in:** M008/S01 (T01–T07). Task summaries: `.megalos/plans/M008/S01/T0{1..7}-SUMMARY.md`. Compression pass: commit e20c4d8. Smoke-test script: 6074eb1. Deploy how-to: 857e2a7.

### Phase D.5 — CALM-informed schema enrichment (NEW)

**Purpose:** Absorb validated patterns from Rasa CALM into the megálos schema and runtime without adding architectural complexity. Three targeted additions, each informed by CALM's production experience.

**Task 1 — Add `collect` step semantics:**

- [ ] Add `collect: true` as an optional boolean flag on step definitions
- [ ] When `collect: true`, the validator enforces that `output_schema` is also present (schema-level enforcement, not just convention)
- [ ] Modify `submit_step` to return richer, field-level error messages when `output_schema` validation fails on a `collect` step (e.g., "Field 'thesis_statement' is required but missing" instead of generic schema error)
- [ ] Update `example.yaml` to demonstrate a `collect` step
- [ ] This makes "gather specific information from the user" a first-class operation, not something encoded implicitly in `directive_template` prose

**Task 2 — Add default conversation repair behaviors:**

- [ ] Add `conversation_repair` as an optional top-level field in workflow YAML, with four keys: `on_go_back`, `on_cancel`, `on_digression`, `on_clarification`
- [ ] Implement sensible defaults in the runtime (hardcoded, like `_DO_NOT_RULES`), so workflows that don't specify `conversation_repair` still get default behaviors
- [ ] Defaults: `on_go_back` → "Guide the user to use revise_step"; `on_cancel` → "Confirm cancellation, then use delete_session"; `on_digression` → "Acknowledge, then redirect to current step"; `on_clarification` → "Re-explain the current step's directive more simply"
- [ ] These defaults are injected into every step response alongside `_DO_NOT_RULES`
- [ ] Workflow authors can override any default per workflow for domain-specific repair behaviors

**Task 3 — Improve directive quality using CALM's description best practices:**

- [ ] Add `step_description` as an optional per-step field: a concise, action-oriented summary (1 sentence) distinct from the full `directive_template`
- [ ] Audit and rewrite `directive_template` content in all shipped workflows (essay, blog, research, decision, coding) to follow CALM's flow-description principles: concise, specific, action-oriented, avoiding vague language
- [ ] Document the pattern in SCHEMA.md: "A good `directive_template` tells the LLM what to accomplish and how to behave. A good `step_description` tells the LLM (and human readers) what this step is for in one sentence."
- [ ] This is a content quality improvement, not a code change to the runtime

**Reference material for this phase:**

- Rasa CALM documentation: `rasa.com/docs/learn/concepts/calm/` (architecture), `rasa.com/docs/pro/build/writing-flows/` (YAML syntax), `rasa.com/docs/reference/primitives/flows/flow-steps/` (step type catalog)
- `github.com/RasaHQ/rasa-agent-skills` — skill packaging patterns, YAML syntax examples
- `github.com/RasaHQ/rasa-calm-demo` — complete working YAML flows for banking use cases (contacts, transactions) with domain definitions, custom actions, pattern overrides, and end-to-end tests
- Rasa's command generator documentation: `rasa.com/docs/rasa-pro/concepts/components/llm-command-generators/` (command vocabulary: `start flow`, `set slot`, `cancel flow`, `search and reply`)

**What NOT to adopt from CALM in this phase:**

- Full flow syntax (`collect` as a step type with slot semantics, `action`, `set_slots`, `link`, `call`). mikrós uses `collect` as a *flag*, not a step type — this preserves the existing step structure.
- NLU pipeline or intent classification. mikrós has no NLU layer; the LLM handles understanding directly.
- Autonomous steps (CALM's mechanism for letting the LLM decide logic within a step). mikrós's `branches` mechanism already handles this more simply.
- No-code studio. The YAML file is the authoring surface. Period.

### Phase E — Authoring Guide (CALM-informed)

- [ ] Write "How to author a megálos workflow" — the definitive document enabling anyone to write a new YAML
- [ ] Structure: (1) When does a workflow add value over free-form chat? (2) Schema reference with all fields. (3) Design principles (collect patterns, directive writing, gate design). (4) Step-by-step example: build a workflow from scratch. (5) Common mistakes and how to avoid them. (6) Validation workflow: write YAML → run `python3 -m server.validate` → deploy.
- [ ] Include CALM-informed guidance on writing good directives: concise, specific, action-oriented. Reference Rasa's flow-description principles as inspiration.
- [ ] Include guidance on `collect` steps: when to use them, how to write `output_schema` that the LLM can satisfy, how to handle validation failures gracefully.
- [ ] Include conversation repair guidance: when to override defaults, domain-specific repair examples.
- [ ] Include the "expressiveness ceiling" warning: if you need a debugger, the workflow is too complex. Split it.

### Phase F — mikrós creation (future, after platform is stable)

**What mikrós is:** A lightweight agent-skills library that teaches AI coding agents how to author, test, validate, and deploy megálos workflows. Inspired directly by `RasaHQ/rasa-agent-skills` — packaged instructions following the Agent Skills format that guide agents through working with the megálos platform.

**What mikrós is NOT:** A runtime, a framework, or a CLI. megálos is the platform. mikrós is the instruction manual that coding agents consume.

**Primary reference:** `github.com/RasaHQ/rasa-agent-skills` — a collection of versioned, markdown-packaged skills that teach coding agents how to build Rasa CALM bots. Each skill has semver versioning and a `rasa_version` compatibility field. mikrós follows the same pattern: versioned skills with a `megalos_version` compatibility field.

**What lives in the mikrós repo:**

- **Agent skills** (the core product):
  - `skills/authoring-workflows/` — teaches a coding agent how to write a megálos YAML workflow from scratch: schema reference, step design patterns, `collect` semantics, gate design, directive writing, common mistakes
  - `skills/validating-workflows/` — teaches a coding agent how to run `python3 -m megalos_server.validate`, interpret errors, fix common schema violations
  - `skills/deploying-workflows/` — teaches a coding agent how to create a domain repo, write the `main.py` + `pyproject.toml` + `Dockerfile` + `deploy.sh`, deploy to Horizon
  - `skills/testing-workflows/` — teaches a coding agent how to write `conftest.py` with `create_app()`, write pytest tests that call MCP tools, verify step ordering and schema validation
  - `skills/simplicity-guard/` — the anti-bloat skill (migrated from the megálos repo): iron rule, LOC budget, three-strikes, anti-patterns list, gotchas learning file

- **Terminal integration glue** (migrated from the megálos repo):
  - `mikros.py` — the 357-line stdlib-only state machine for terminal workflows
  - `CLAUDE.md` — project memory template for Claude Code sessions
  - `GEMINI.md` — equivalent for Gemini CLI
  - `install.sh` — drop-in setup for any coding project
  - Terminal-specific tests (`test_commands.sh`, `test_pre_tool_use.sh`, `test_post_edit.sh`, etc.)
  - `.claude/commands/` — the slash commands (`/discuss`, `/plan-slice`, `/execute-task`, `/sniff-test`, `/compress`)

**Skill versioning** (adopted from rasa-agent-skills):

```yaml
# Each skill's frontmatter
---
name: authoring-workflows
version: "0.1.0"              # skill package version (semver)
megalos_version: ">=0.2.0"    # minimum megálos platform version required
---
```

When a skill is updated to cover a feature from a newer megálos release, bump `megalos_version`. If a feature is removed, add an upper bound (e.g., `>=0.2.0,<1.0.0`).

**Repo structure:**

```
mikros/
├── skills/
│   ├── authoring-workflows/
│   │   └── SKILL.md
│   ├── validating-workflows/
│   │   └── SKILL.md
│   ├── deploying-workflows/
│   │   └── SKILL.md
│   ├── testing-workflows/
│   │   └── SKILL.md
│   └── simplicity-guard/
│       ├── SKILL.md
│       ├── references/anti-patterns.md
│       └── references/gotchas.md
├── terminal/
│   ├── mikros.py
│   ├── CLAUDE.md
│   ├── GEMINI.md
│   ├── install.sh
│   └── .claude/commands/
│       ├── discuss.md
│       ├── plan-slice.md
│       ├── execute-task.md
│       ├── sniff-test.md
│       └── compress.md
├── tests/
├── README.md
└── pyproject.toml    # no runtime deps — skills are markdown, not code
```

**Tasks:**

- [ ] Create `agora-creations/mikros` repo
- [ ] Write `skills/authoring-workflows/SKILL.md` — the most important skill. This is essentially the Authoring Guide (Phase E) repackaged as an agent skill. If Phase E is done first, this skill is a reformatting of that guide into the Agent Skills format.
- [ ] Write `skills/validating-workflows/SKILL.md`
- [ ] Write `skills/deploying-workflows/SKILL.md` — codifies the deployment how-to from M008/T02 into a skill
- [ ] Write `skills/testing-workflows/SKILL.md` — codifies the testing patterns from the domain repos' `conftest.py`
- [ ] Move `simplicity-guard/` from the megálos repo
- [ ] Move `mikros.py`, `CLAUDE.md`, `GEMINI.md`, `install.sh`, `.claude/commands/`, terminal test files from the megálos repo
- [ ] Update terminal files: `.mikros/` path references stay as `.mikros/` (they're mikrós's own state directory now — no rename needed within this repo)
- [ ] Update `CLAUDE.md` and command files to reference megálos as the platform they work with
- [ ] Verify: a Claude Code session can consume `skills/authoring-workflows/SKILL.md` and successfully produce a valid megálos YAML workflow from scratch without any other documentation

**Relationship to Phase E:** Phase E (Authoring Guide) and Phase F (mikrós skills) overlap significantly. Two valid execution orders: (a) Write the Authoring Guide first as a standalone doc in the megálos repo, then repackage it as a mikrós agent skill. (b) Write the agent skill directly, skip the standalone guide. Option (a) is safer — you validate the content as a human-readable document before reformatting it for agent consumption.

**Competitive positioning for mikrós:** No direct competitor exists. `RasaHQ/rasa-agent-skills` is the closest analog but teaches Rasa CALM specifically. mikrós would be the first agent-skills library for a generic YAML-to-MCP workflow platform. The skills are open source (Apache 2.0), the platform they teach (megálos) is also open source — this is the community flywheel.

### Phase G — agora-creations web client (future, after traction) — UPDATED in v3

- [ ] **MCP protocol layer:** FastMCP Client with multi-server config pointing to all deployed Horizon domain servers
- [ ] **LLM routing:** FastMCP's built-in sampling handlers (`fastmcp[anthropic]`, `fastmcp[openai]`, `fastmcp[gemini]`); user selects provider and provides their own key
- [ ] **Progressive tool disclosure:** CodeMode transform for meta-tool discovery; tag-based filtering for cold-start optimization (see §3.3.1)
- [ ] **Agent loop:** Custom Python orchestration that reads LLM responses, detects tool calls, routes them through FastMCP Client's `call_tool()`, and feeds results back. This is the thin layer that wires FastMCP Client to the LLM SDK.
- [ ] **Chat UI:** Lightweight web app (React or similar) — chat interface, workflow selection, key management
- [ ] **No backend needed for v1** — client-side only (FastMCP Client's HTTP transport connects directly to Horizon endpoints)
- [ ] **Workflow discovery and onboarding UX**
- [ ] **Validation milestone:** Run the essay workflow end-to-end through agora-creations client, connecting to `mikros-writing` on Horizon, using the user's own Anthropic key, then repeat with an OpenAI key — zero code changes.

### Phase H — agora-creations mobile app (future, after web validation)

- [ ] React Native / Expo for iOS + Android
- [ ] Port web client patterns (including progressive tool disclosure) to mobile
- [ ] App Store + Play Store distribution

-----

## 7. Competitive Positioning

### What we are NOT competing with

- GSD-2, CCPM, Simone — spec-driven development frameworks for coding agents (megálos competes here, later)
- OpenAI Apps, Custom GPTs, Claude Projects — third-party integrations / prompt-stuffed assistants
- OpenClaw — autonomous AI agent (opposite philosophy: unleash vs. constrain)
- Pi-mono — open-source coding agent harness (different layer: agent harness vs. workflow engine; complementary)
- Rasa CALM — enterprise conversational AI platform (same thesis, vastly different delivery: proprietary runtime, enterprise infrastructure, no-code studio). megálos is the lightweight, MCP-native, open-source version of the same idea.

### What we ARE creating (new category)

**YAML-defined deterministic chatbots, deployed as MCP servers, consumed in any chat interface.**

Closest analogues (all significantly different):

| Competitor | How they differ |
|------------|----------------|
| Voiceflow | Visual builder, locked to own runtime, not LLM-native |
| Botpress | Drag-and-drop, own runtime, enterprise-focused |
| Typebot | Visual chatbot flow builder, own runtime |
| Rasa CALM | Same thesis, but proprietary enterprise runtime, complex deployment, no-code studio |

megálos differentiators:

- **Text-file-native** — YAML, not drag-and-drop. Version-controlled, diffable, reviewable.
- **Runtime-agnostic** — any MCP-compatible client (Claude, ChatGPT, Cursor, custom).
- **Provider-agnostic** — any LLM (Claude, GPT, Gemini, local). No adapter layer.
- **Anti-bloat by design** — the AI is constrained to follow the program, not unleashed to improvise.
- **3-dependency runtime** — `fastmcp` + `pyyaml` + `jsonschema`. That's it.
- **Token-efficient at scale** — progressive tool disclosure via FastMCP CodeMode for the future client.
- **30-minute authoring** — a domain expert who knows YAML can write a workflow without a no-code studio.
- **Single-framework client stack** — the same `fastmcp` package that powers the server also powers the client. No framework mismatch.

-----

## 8. Success Metrics

### Framework (this repo)

- A new contributor can author a workflow YAML from scratch in < 30 minutes using the Authoring Guide
- `python3 -m server.validate` catches 100% of schema violations with clear error messages
- The runtime stays under 20 files / 500 lines for `server/` (currently: 6 files, ~400 lines — healthy)

### Domain servers

- At least 3 domain repos deployed on Horizon within 4 weeks of refactoring
- At least 1 Remix server composing multiple domains (superseded by FastMCP Client multi-server config if Remix remains paid-only)
- At least 5 non-author users have completed a workflow through Claude.ai or Horizon Agents

### Validation

- User feedback confirms structured workflows produce measurably better output than free-form chat for the same task
- Workflow completion rate > 70% (users who start finish, not abandon mid-way)
- At least 2 different LLM providers (e.g., Claude + GPT) successfully complete the same workflow without any provider-specific code

### agora-creations client (Phase G metrics)

- Tool definition overhead < 2k tokens at cold start (via CodeMode progressive disclosure)
- Time-to-first-response < 3 seconds despite connecting to multiple domain servers
- Users can connect external MCP servers (Drive, Slack) without degrading workflow performance
- Provider swap test: same workflow, same client, Anthropic key → OpenAI key, zero code changes

-----

## 9. Key Design Decisions Register

### 9.1 No provider adapter layer (decided, §4.1)

**Decision.** The MCP server is the deterministic layer. No per-provider prompt translation.
**Rationale.** Enforcement happens at the gate, not at the prompt. A provider adapter creates maintenance cost for a theoretical problem.

### 9.2 Progressive tool disclosure for agora-creations (decided, §3.3.1)

**Decision.** The future chatbot client must not load all tool definitions upfront. Discovery-first architecture.
**Rationale.** Token efficiency at scale. 98.7% reduction via discovery-first patterns (Anthropic, November 2025). FastMCP CodeMode implements this pattern natively.

### 9.3 Pi-mono as design reference, not dependency (decided, §3.4)

**Decision.** Study pi-mono's extension patterns. Implement natively in Python for megálos.
**Rationale.** Different layer, different language, no MCP support. Patterns valuable; dependency not.

### 9.4 BYOK only, always (decided, §4.3)

**Decision.** The platform never pays for LLM inference. Users provide their own API keys.
**Rationale.** Zero LLM cost for operator. No vendor lock-in. No subscription complexity.

### 9.5 Schema expressiveness ceiling (decided, §5.2)

**Decision.** The YAML schema must not become Turing-complete. No debugger, no interpreter, no recursion.
**Rationale.** Target author is a domain expert who knows YAML, not a programmer.

### 9.6 Rasa CALM as design reference, not template (decided, §4.4)

**Decision.** Adopt CALM's validated patterns (`collect` semantics, conversation repair defaults, description best practices). Do not adopt CALM's syntax, complexity, or infrastructure model.
**Rationale.** CALM and megálos share the thesis ("LLM interprets, code enforces"). CALM validated this thesis at enterprise scale over 2+ years. Its patterns are proven. But CALM is an enterprise platform with a no-code studio, an NLU pipeline, a proprietary runtime, and multi-hundred-line configuration files. megálos's competitive advantage is simplicity: 3 dependencies, 5-field steps, 30-minute authoring. Importing CALM's full syntax would erase that advantage. Adopt the patterns; reject the complexity.

### 9.7 FastMCP Client as MCP protocol layer, not Claude Managed Agents (decided, §4.5 + §4.3) — UPDATED in v3

**Decision.** The agora-creations client (Phase G web, Phase H mobile) uses FastMCP Client as its MCP protocol layer, combined with FastMCP's built-in LLM sampling handlers and the user's own API key. It does NOT build on Claude Managed Agents, and it does NOT reimplement MCP protocol handling from the raw `mcp` SDK.

**Rationale.** Three options were evaluated (see §4.5.3):

- **Claude Managed Agents** rejected: bills inference through the agent owner's Anthropic account (contradicts §4.3 BYOK), locks to Claude as provider (contradicts provider-agnostic principle), designed for autonomous long-running sessions (not the mikrós use case).
- **Raw MCP SDK** rejected: requires manual transport negotiation, connection lifecycle, and protocol handling that FastMCP Client already abstracts. No built-in multi-server composition, no tool transformations, no tag filtering. Unnecessary complexity.
- **FastMCP Client** selected: zero new dependencies (already in `pyproject.toml`), native multi-server composition (replaces Horizon Remix), progressive disclosure via CodeMode, built-in sampling handlers for Anthropic/OpenAI/Gemini, Apache 2.0 license, same team as the deployment platform. Full assessment in §4.5.

### 9.8 FastMCP Client adoption scope (decided, §4.5) — NEW in v3

**Decision.** FastMCP Client is the MCP protocol layer only. The agora-creations client still custom-builds: chat UI, API key vault, workflow discovery UX, conversation history, and agent loop.

**Rationale.** FastMCP Client is a programmatic SDK, not a chat application. It eliminates the hardest protocol engineering (transport negotiation, multi-server composition, tool namespacing, connection lifecycle) but intentionally does not provide a UI or agent orchestration. This matches megálos's layered architecture: the framework provides the plumbing, the product provides the experience. Overloading FastMCP Client with responsibilities it wasn't designed for would violate the simplicity-guard philosophy.

-----

## 10. References & Lineage

- **Current repo:** `github.com/agora-creations/megalos`
- **Original megálos spec** (2026-04-11): spec-driven Claude Code workflow, anti-bloat thesis
- **Competitive analysis** (2026-04-12): positioning vs. GSD-2, CCPM; identified "no programmatic control plane" as key weakness — MCP server solves this
- **GSD-2** (`github.com/gsd-build/gsd-2`): Milestone/Slice/Task hierarchy, iron rule, anti-pattern list (attributed, MIT-licensed)
- **Rasa CALM** (`rasa.com/docs/learn/concepts/calm/`): Closest architectural analog. Independently arrived at the same thesis: "The LLM interprets what the user wants. The logic decides what happens next." YAML-defined flows, deterministic execution, separation of concerns. Enterprise-grade, proprietary runtime. mikrós adopts CALM's validated patterns; rejects its complexity.
- **Rasa agent skills** (`github.com/RasaHQ/rasa-agent-skills`): Skill packaging patterns and CALM YAML syntax examples for AI coding agents.
- **Rasa CALM demo** (`github.com/RasaHQ/rasa-calm-demo`): Complete working YAML flows for banking use cases with domain definitions, pattern overrides, and E2E tests. Primary reference for YAML authoring patterns.
- **Rasa flow documentation** (`rasa.com/docs/pro/build/writing-flows/`): YAML syntax for flow definitions, step types, collect steps, branching, actions. Reference for Phase E authoring guide.
- **Prefect Horizon**: deployment platform, Remix for composing servers, Gateway for governance, Agents for web interface.
- **FastMCP** (`gofastmcp.com`, `github.com/PrefectHQ/fastmcp`): Framework powering both the mikrós MCP server and the agora-creations client. Apache 2.0. 70% market share across MCP servers. Server: `FastMCP` class, tool/resource/prompt decorators, streamable HTTP transport, YAML workflow loading. Client (v2.0.0+): `Client` class, multi-server configuration (v2.4.0+), CodeMode progressive disclosure (v3.x), tool transformations, tag-based filtering, built-in sampling handlers for Anthropic/OpenAI/Gemini. Full assessment: §4.5.
- **FastMCP Client docs** (`gofastmcp.com/clients/client`): Client overview, transport auto-detection, configuration-based multi-server composition, connection lifecycle, operations (tools/resources/prompts), callback handlers.
- **FastMCP Client Transports** (`gofastmcp.com/clients/transports`): STDIO, HTTP, SSE, in-memory transports. Multi-server config with tool namespacing. Tool transformations via `include_tags`/`exclude_tags` and per-tool overrides.
- **FastMCP CodeMode** (`gofastmcp.com/servers/transforms/code-mode`): Progressive tool disclosure via meta-tools (`search`, `get_schema`, `execute`). Replaces upfront tool catalog with on-demand discovery. Implements the pattern from Anthropic's November 2025 article as a built-in framework feature.
- **FastMCP Sampling** (`gofastmcp.com/clients/sampling`): Built-in LLM sampling handlers for Anthropic, OpenAI, and Gemini APIs. Supports full sampling API including tool use.
- **Anthropic, "Code execution with MCP"** (November 2025): source for progressive tool disclosure architecture and token-efficiency measurements.
- **Claude Managed Agents** (`platform.claude.com/docs/en/managed-agents/overview`, beta as of 2026-04): Anthropic-hosted agent runtime with first-class multi-MCP composition, session-scoped auth, and managed sandbox containers. Evaluated and rejected for Phase G — see §4.5.3 and §9.7.
- **Pi-mono** (`github.com/badlogic/pi-mono`): design reference for hooks, extension model, agent chain architecture. Not a dependency.
- **pi-mcp-adapter** (`github.com/nicobailon/pi-mcp-adapter`): community MCP bridge for pi-mono — validates progressive disclosure pattern.
- **LLM Enhancement Mechanisms Plan** (2026-04-13): Internal plan documenting six mechanism types for enhancing LLM behavior within server-enforced boundaries: context injection, output schema validation, adaptive branching, behavioral directives, server-validated intermediate artifacts, guardrails with escalation. Phased implementation order. Informs Phase D.5 and beyond.
- **JSON Schema validation:** `jsonschema` library v4.26.0 (Python). Draft 2020-12 support, lazy validation with iterative error reporting. Used by `submit_step` for `output_schema` enforcement.
- **OpenAI Apps in ChatGPT**: what we are NOT — third-party integrations. We are the chatbot's behavioral program itself.
- **OpenClaw**: what we are NOT — autonomous AI agent. Opposite philosophy (unleash vs. constrain).

-----

**End of spec.**
