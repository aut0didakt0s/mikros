# real-agorà — Vision, Architecture & Execution Plan

**Date:** 2026-04-14
**Status:** Strategic spec — **Phases A, B, C shipped**; Phase D in progress; D.5–H pending
**Author:** Diego Marono
**Repo:** `github.com/agora-creations/mikros`

> **Execution status (2026-04-14):** Phases A, B, and C of §6 are complete and merged to `main`. The runtime package is distributed as `mikros-server` (not `mikros-engine` as originally drafted). The three domain repos (`mikros-writing`, `mikros-analysis`, `mikros-professional`) are live under `github.com/agora-creations/`. Production MCP server deployed at `https://Mikros.fastmcp.app/mcp`; domain endpoints not yet deployed. Remaining: Phase D (Horizon deployment), Phase D.5 (CALM-informed schema enrichment), Phase E (authoring guide), Phase F (megálos extraction), Phases G–H (agora-creations client).

> **What changed during development:** Discovery of Rasa CALM as closest architectural analog. CALM validates mikrós's core thesis ("LLM interprets, code enforces") and provides concrete patterns worth adopting: `collect` step semantics, default conversation repair behaviors, and flow-description best practices. A new Phase D.5 is inserted between deployment and the authoring guide to absorb these learnings. No architectural refactoring required — the changes are additive. Competitive positioning updated. Reference material expanded.

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

- `mikros-writing`: essay (6 steps), blog (6 steps). Category: `writing_communication`.
- `mikros-analysis`: research (6 steps), decision (6 steps). Category: `analysis_decision`.
- `mikros-professional`: coding (6 steps). Category: `professional`.
- Core repo retains a single `example.yaml` as reference.

**Terminal Runtime** (project root, future megálos):

- `mikros.py` — 357-line stdlib-only Python state machine for terminal workflows (Claude Code / Gemini CLI). Manages `.mikros/STATE.md`, `DECISIONS.md`, task advancement, atomic writes.
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

- `mikros.py`, `CLAUDE.md`, `GEMINI.md`, `install.sh` → megálos repo (Phase F)
- Workflow-specific tests → already moved with domain repos (Phase C, done)

### 3.2 mikrós-{domain} (separate repos) — Domain MCP Servers

Each repo contains workflow YAMLs for a specific domain, plus a thin `main.py` that imports the mikrós runtime and points it at the local `workflows/` directory.

| Repo | Workflows (current + planned) | Status |
|------|-------------------------------|--------|
| `mikros-writing` | essay, blog, academic paper, technical documentation, grant proposal, creative fiction | essay + blog shipped |
| `mikros-analysis` | research, decision, competitive analysis, financial analysis | research + decision shipped |
| `mikros-strategy` | business plan, product spec/PRD, project roadmap, marketing campaign | All new — validate before committing |
| `mikros-learning` | concept tutorial, study guide, interview prep, skill assessment | All new — strong structural case |
| `mikros-creative` | brainstorming/ideation, worldbuilding, design brief | Borderline — validate with users first |
| `mikros-professional` | coding, cover letter/resume, consulting deliverable | coding shipped |

**Key filter:** Does a structured multi-step workflow add value over free-form chat? If the answer is no, don't build it.

**Dropped from scope** (free-form chat is sufficient): email, meeting agenda, recipe, travel itinerary, event planning, newsletter, speech scripts.

**Each domain repo structure:**

```
mikros-writing/
├── workflows/
│   ├── essay.yaml
│   ├── blog.yaml
│   └── ...
├── main.py          # imports mikros runtime, points to ./workflows/
├── pyproject.toml   # depends on mikros-server @ git+...
├── Dockerfile
├── deploy.sh
├── README.md
└── tests/
```

**Deployment:** Each repo deploys independently to Prefect Horizon. Horizon Remix composes them into governed domain bundles.

### 3.3 agora-creations (proprietary client) — FUTURE


A purpose-built chat interface optimized for mikrós workflow consumption. BYOK (bring your own key) only — users provide their own LLM API keys. The platform never pays for LLM inference.

**Phase 1 — Web client** (after traction): Lightweight web app that connects to Horizon-hosted MCP servers and routes to the user's chosen LLM provider. Thin client: chat UI + API key vault + MCP client.

**Phase 2 — Mobile app** (after web validation): React Native / Expo for iOS + Android.

#### 3.3.1 Progressive Tool Disclosure — a founding design principle

As the number of mikrós domain servers grows, the agora-creations client will connect to multiple Horizon endpoints simultaneously. This creates the token-efficiency problem documented by Anthropic's engineering team in their November 2025 article *"Code execution with MCP: Building more efficient agents"*.

**Design principle:** Progressive tool disclosure from day one. The client must not load all tool definitions upfront.

**Implementation approach:**

1. **Discovery-first architecture.** Single lightweight `search_tools` capability (~200 tokens) instead of injecting all tool schemas upfront. The LLM discovers tools on demand. Same pattern as `nicobailon/pi-mcp-adapter`, which achieved 98.7% token reduction in Anthropic's measurements.
2. **Hierarchical tool presentation.** Domain servers presented as a navigable tree. The LLM reads the top-level directory, then loads specific tool schemas only when needed.
3. **Context-efficient tool results.** mikrós tool responses are already small (~500 tokens per step). External MCP server results must be filtered before entering context.
4. **One-shot workflow loading.** Once the LLM identifies a domain (e.g., "essay"), pre-load that domain's full tool chain as a batch. Hybrid: progressive discovery of *which* domain, upfront loading of *that domain's* tools.

**When this matters:** Phase G (web client) and Phase H (mobile app). Not relevant to Phases A–F, where users consume mikrós through existing MCP-compatible clients. But the architecture must be designed now, even if implementation is deferred.

### 3.4 megálos (separate repo) — FUTURE

> μεγάλος (ancient Greek: *big*)

The developer-oriented, terminal-first product for structured coding workflows. megálos is mikrós applied to software engineering — same YAML schema, same MCP runtime, but with terminal UX, coding-specific anti-patterns, and the full simplicity-guard lineage.

**What megálos inherits from mikrós:** YAML schema, MCP server runtime, simplicity-guard philosophy.

**What megálos adds:** Terminal-first delivery (slash commands, git worktree isolation, fresh-context-per-task dispatch), the `mikros.py` state machine, `CLAUDE.md`/`GEMINI.md` templates, `install.sh`, coding-specific workflow YAMLs, LOC budget hooks, the Milestone → Slice → Task hierarchy from GSD-2.

**Competitive landscape:** GSD-2 (market leader), CCPM (GitHub-native), Simone (MCP-based). megálos competes on philosophy (anti-bloat, simplicity-first) plus a real programmatic control plane (the MCP server).

**When to build:** After the mikrós platform is stable. megálos depends on the same runtime.

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
│         mikrós MCP Server (Horizon)               │
│         Mikros.fastmcp.app/mcp                    │
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

mikrós and CALM solve the same problem with different trade-offs:

| Concern | mikrós | Rasa CALM |
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

- [x] Runtime distributed as `mikros-server` (import: `mikros_server`)
- [x] `create_app(workflow_dir=...)` factory replaces hardcoded path
- [x] Three runtime deps: `fastmcp`, `pyyaml`, `jsonschema`
- [x] Built-in workflows reduced to single reference `example.yaml`

### Phase C — Repo split ✅ (shipped M007/S01, 2026-04-14)

- [x] `mikros-writing` live at `github.com/agora-creations/mikros-writing` (essay, blog)
- [x] `mikros-analysis` live at `github.com/agora-creations/mikros-analysis` (research, decision)
- [x] `mikros-professional` live at `github.com/agora-creations/mikros-professional` (coding)
- [x] Each domain repo: own pyproject.toml, Dockerfile, deploy.sh, tests with conftest constructing its own mcp
- [x] Domain repos depend on runtime via pinned git URL

### Phase D — Horizon deployment

- [ ] Deploy `mikros-writing` to Horizon as first standalone domain server
- [ ] Deploy `mikros-analysis` as second domain server
- [ ] Create a Horizon Remix server composing writing + analysis
- [ ] Test with non-technical users through Horizon Agents web interface
- [ ] Document the deployment workflow for future domain repos
- [ ] Test provider agnosticism: run the essay workflow through Claude AND GPT without code changes

### Phase D.5 — CALM-informed schema enrichment (NEW)

**Purpose:** Absorb validated patterns from Rasa CALM into the mikrós schema and runtime without adding architectural complexity. Three targeted additions, each informed by CALM's production experience.

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

- [ ] Write "How to author a mikrós workflow" — the definitive document enabling anyone to write a new YAML
- [ ] Structure: (1) When does a workflow add value over free-form chat? (2) Schema reference with all fields. (3) Design principles (collect patterns, directive writing, gate design). (4) Step-by-step example: build a workflow from scratch. (5) Common mistakes and how to avoid them. (6) Validation workflow: write YAML → run `python3 -m server.validate` → deploy.
- [ ] Include CALM-informed guidance on writing good directives: concise, specific, action-oriented. Reference Rasa's flow-description principles as inspiration.
- [ ] Include guidance on `collect` steps: when to use them, how to write `output_schema` that the LLM can satisfy, how to handle validation failures gracefully.
- [ ] Include conversation repair guidance: when to override defaults, domain-specific repair examples.
- [ ] Include the "expressiveness ceiling" warning: if you need a debugger, the workflow is too complex. Split it.

### Phase F — megálos extraction (future)

- [ ] Create `megalos` repo
- [ ] Move: `mikros.py`, `CLAUDE.md`, `GEMINI.md`, `install.sh`, terminal-specific tests
- [ ] Share `simplicity-guard/` via git submodule or installable package
- [ ] megálos imports mikrós runtime for its MCP server component
- [ ] Add coding-specific workflow expansion (beyond current 6-step `coding.yaml`)
- [ ] Study pi-mono extension patterns (hooks, agent chains) — implement natively in Python
- [ ] Offer optional `mikros-pi-extension` for pi-mono users

### Phase G — agora-creations web client (future, after traction)

- [ ] Lightweight web app: chat UI + API key manager + MCP client
- [ ] Direct connection to LLM providers (user's key) and Horizon MCP servers
- [ ] **Progressive tool disclosure from day one** (see §3.3.1)
- [ ] No backend needed for v1 — client-side only
- [ ] Workflow discovery and onboarding UX

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
- Rasa CALM — enterprise conversational AI platform (same thesis, vastly different delivery: proprietary runtime, enterprise infrastructure, no-code studio). mikrós is the lightweight, MCP-native, open-core version of the same idea.

### What we ARE creating (new category)

**YAML-defined deterministic chatbots, deployed as MCP servers, consumed in any chat interface.**

Closest analogues (all significantly different):

| Competitor | How they differ |
|------------|----------------|
| Voiceflow | Visual builder, locked to own runtime, not LLM-native |
| Botpress | Drag-and-drop, own runtime, enterprise-focused |
| Typebot | Visual chatbot flow builder, own runtime |
| Rasa CALM | Same thesis, but proprietary enterprise runtime, complex deployment, no-code studio |

mikrós differentiators:

- **Text-file-native** — YAML, not drag-and-drop. Version-controlled, diffable, reviewable.
- **Runtime-agnostic** — any MCP-compatible client (Claude, ChatGPT, Cursor, custom).
- **Provider-agnostic** — any LLM (Claude, GPT, Gemini, local). No adapter layer.
- **Anti-bloat by design** — the AI is constrained to follow the program, not unleashed to improvise.
- **3-dependency runtime** — `fastmcp` + `pyyaml` + `jsonschema`. That's it.
- **Token-efficient at scale** — progressive tool disclosure architecture for the future client.
- **30-minute authoring** — a domain expert who knows YAML can write a workflow without a no-code studio.

-----

## 8. Success Metrics

### Framework (this repo)

- A new contributor can author a workflow YAML from scratch in < 30 minutes using the Authoring Guide
- `python3 -m server.validate` catches 100% of schema violations with clear error messages
- The runtime stays under 20 files / 500 lines for `server/` (currently: 6 files, ~400 lines — healthy)

### Domain servers

- At least 3 domain repos deployed on Horizon within 4 weeks of refactoring
- At least 1 Remix server composing multiple domains
- At least 5 non-author users have completed a workflow through Claude.ai or Horizon Agents

### Validation

- User feedback confirms structured workflows produce measurably better output than free-form chat for the same task
- Workflow completion rate > 70% (users who start finish, not abandon mid-way)
- At least 2 different LLM providers (e.g., Claude + GPT) successfully complete the same workflow without any provider-specific code

### agora-creations client (Phase G metrics)

- Tool definition overhead < 2k tokens at cold start
- Time-to-first-response < 3 seconds despite connecting to multiple domain servers
- Users can connect external MCP servers (Drive, Slack) without degrading workflow performance

-----

## 9. Key Design Decisions Register

### 9.1 No provider adapter layer (decided, §4.1)

**Decision.** The MCP server is the deterministic layer. No per-provider prompt translation.
**Rationale.** Enforcement happens at the gate, not at the prompt. A provider adapter creates maintenance cost for a theoretical problem.

### 9.2 Progressive tool disclosure for agora-creations (decided, §3.3.1)

**Decision.** The future chatbot client must not load all tool definitions upfront. Discovery-first architecture.
**Rationale.** Token efficiency at scale. 98.7% reduction via discovery-first patterns (Anthropic, November 2025).

### 9.3 Pi-mono as design reference, not dependency (decided, §3.4)

**Decision.** Study pi-mono's extension patterns. Implement natively in Python for megálos.
**Rationale.** Different layer, different language, no MCP support. Patterns valuable; dependency not.

### 9.4 BYOK only, always (decided, §4.3)

**Decision.** The platform never pays for LLM inference. Users provide their own API keys.
**Rationale.** Zero LLM cost for operator. No vendor lock-in. No subscription complexity.

### 9.5 Schema expressiveness ceiling (decided, §5.2)

**Decision.** The YAML schema must not become Turing-complete. No debugger, no interpreter, no recursion.
**Rationale.** Target author is a domain expert who knows YAML, not a programmer.

### 9.6 Rasa CALM as design reference, not template (decided, §4.4) — NEW

**Decision.** Adopt CALM's validated patterns (`collect` semantics, conversation repair defaults, description best practices). Do not adopt CALM's syntax, complexity, or infrastructure model.
**Rationale.** CALM and mikrós share the thesis ("LLM interprets, code enforces"). CALM validated this thesis at enterprise scale over 2+ years. Its patterns are proven. But CALM is an enterprise platform with a no-code studio, an NLU pipeline, a proprietary runtime, and multi-hundred-line configuration files. mikrós's competitive advantage is simplicity: 3 dependencies, 5-field steps, 30-minute authoring. Importing CALM's full syntax would erase that advantage. Adopt the patterns; reject the complexity.

-----

## 10. References & Lineage

- **Current repo:** `github.com/agora-creations/mikros`
- **Original mikrós spec** (2026-04-11): spec-driven Claude Code workflow, anti-bloat thesis
- **Competitive analysis** (2026-04-12): positioning vs. GSD-2, CCPM; identified "no programmatic control plane" as key weakness — MCP server solves this
- **GSD-2** (`github.com/gsd-build/gsd-2`): Milestone/Slice/Task hierarchy, iron rule, anti-pattern list (attributed, MIT-licensed)
- **Rasa CALM** (`rasa.com/docs/learn/concepts/calm/`): Closest architectural analog. Independently arrived at the same thesis: "The LLM interprets what the user wants. The logic decides what happens next." YAML-defined flows, deterministic execution, separation of concerns. Enterprise-grade, proprietary runtime. mikrós adopts CALM's validated patterns; rejects its complexity.
- **Rasa agent skills** (`github.com/RasaHQ/rasa-agent-skills`): Skill packaging patterns and CALM YAML syntax examples for AI coding agents.
- **Rasa CALM demo** (`github.com/RasaHQ/rasa-calm-demo`): Complete working YAML flows for banking use cases with domain definitions, pattern overrides, and E2E tests. Primary reference for YAML authoring patterns.
- **Rasa flow documentation** (`rasa.com/docs/pro/build/writing-flows/`): YAML syntax for flow definitions, step types, collect steps, branching, actions. Reference for Phase E authoring guide.
- **Prefect Horizon**: deployment platform, Remix for composing servers, Gateway for governance, Agents for web interface
- **FastMCP** (`gofastmcp.com`): framework powering the MCP server, 70% market share
- **Anthropic, "Code execution with MCP"** (November 2025): source for progressive tool disclosure architecture and token-efficiency measurements
- **Claude Managed Agents** (`platform.claude.com/docs/en/managed-agents/overview`, beta as of 2026-04): Anthropic-hosted agent runtime with first-class multi-MCP composition (array of `mcp_servers`), session-scoped auth via pre-registered vaults (`vault_ids` per session), and managed sandbox containers. Architecturally relevant to Phase G: the heaviest pieces of the client layer (session store, harness loop, MCP proxy, credential vault) are available as primitives. Caveat for the BYOK thesis (§4.3): vaults are platform-registered, not end-user-supplied, and LLM inference bills through the agent owner's Anthropic account. Phase G must explicitly decide whether to build on Managed Agents (accept metered inference, platform-brokered vaults) or on the Messages API directly (preserve raw BYOK, rebuild the primitives).
- **Pi-mono** (`github.com/badlogic/pi-mono`): design reference for hooks, extension model, agent chain architecture. Not a dependency.
- **pi-mcp-adapter** (`github.com/nicobailon/pi-mcp-adapter`): community MCP bridge for pi-mono — validates progressive disclosure pattern
- **LLM Enhancement Mechanisms Plan** (2026-04-13): Internal plan documenting six mechanism types for enhancing LLM behavior within server-enforced boundaries: context injection, output schema validation, adaptive branching, behavioral directives, server-validated intermediate artifacts, guardrails with escalation. Phased implementation order. Informs Phase D.5 and beyond.
- **JSON Schema validation:** `jsonschema` library v4.26.0 (Python). Draft 2020-12 support, lazy validation with iterative error reporting. Used by `submit_step` for `output_schema` enforcement.
- **OpenAI Apps in ChatGPT**: what we are NOT — third-party integrations. We are the chatbot's behavioral program itself.
- **OpenClaw**: what we are NOT — autonomous AI agent. Opposite philosophy (unleash vs. constrain).

-----

**End of spec.**
