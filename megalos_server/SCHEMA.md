# Workflow YAML Schema Reference

Every workflow YAML file must be a mapping with the following fields.

## Top-level fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Short identifier for the workflow (e.g. `coding`, `essay`). |
| `description` | string | yes | One-line summary of what the workflow guides. |
| `category` | string | yes | Grouping tag (e.g. `professional`, `writing_communication`, `analysis_decision`). |
| `output_format` | string | yes | Expected output type (e.g. `text`, `structured_code`). |
| `steps` | list | yes | Ordered list of step mappings (at least one). |
| `schema_version` | string | no (defaults to `"0.4"`) | Schema spec version this workflow targets. Omit to get the default. |
| `conversation_repair` | mapping | no | Optional overrides for default repair-behavior strings injected into step responses. See "Conversation repair defaults" section. |

## Step fields

Each entry in `steps` is a mapping with:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Unique step identifier within the workflow. |
| `title` | string | yes | Human-readable step name. |
| `directive_template` | string | yes | Prompt template sent to the LLM for this step. |
| `gates` | list of strings | yes | Conditions that must be met before the step can be submitted. |
| `anti_patterns` | list of strings | yes | Behaviours the LLM should avoid during this step. |
| `step_description` | string | no | One-sentence, action-oriented summary of what this step does. Authoring metadata only — not injected into step responses. See "Directive quality" section for phrasing principles. |
| `collect` | boolean | no | When `true`, the step is flagged as a structured data-collection step. The validator requires `output_schema` to be present on the same step. Used for steps where the LLM must gather specific, schema-conformant input from the user. |
| `precondition` | mapping | no | Optional gate on whether this step is reachable, declared against prior-step output. Exactly one of two predicates: `when_equals` (scalar equality) or `when_present` (presence check). See "Precondition" section below. |
| `call` | string | no | Name of a child workflow to invoke declaratively when this step is reached. See "Sub-workflow call" section below. |
| `call_context_from` | string | no | Optional ref-path (`step_data.<step_id>[.<field>...]`) selecting a sub-tree of parent `step_data` to seed the child workflow's context. Requires `call` on the same step. See "Sub-workflow call" section below. |
| `action` | string | no | When set to `"mcp_tool_call"`, marks the step as a non-LLM external tool call; the `directive_template`/`gates`/`anti_patterns` trio is replaced by `server` + `tool` + `args`. See "MCP tool call" section below. |

## Sub-workflow call

A step may declare a `call` field to invoke a child workflow declaratively. The child runs to completion and its return value is surfaced back to the parent step.

**`call: <child_workflow_name>`** — names the child workflow to invoke:

```yaml
- id: analyze
  title: Run analysis sub-workflow
  directive_template: hand off to analysis child
  gates: [done]
  anti_patterns: [none]
  call: analysis_child
```

**`call_context_from: step_data.<step_id>[.<field>...]`** — optional ref-path that selects a sub-tree of parent `step_data` to seed the child's context. Uses the same ref-path grammar as `precondition` (see above).

```yaml
- id: analyze
  title: Run analysis with topic from earlier step
  directive_template: hand off to analysis child
  gates: [done]
  anti_patterns: [none]
  call: analysis_child
  call_context_from: step_data.intake.topic
```

**Mutex rules (parse-time):**

- `call` + `collect: true` is rejected — sub-workflow steps cannot also collect structured data. (error code: `call_with_collect`)
- `call` + `intermediate_artifacts` is rejected — sub-workflow steps cannot also produce intermediate artifacts. (error code: `call_with_intermediate_artifacts`)
- `call_context_from` without `call` on the same step is rejected. (error code: `call_context_from_without_call`)
- `call_context_from` whose value is not a valid ref-path is rejected. (error code: `call_invalid_context_ref`)

**Allowed compositions:**

- `call` + `output_schema` — allowed. The `output_schema` validates the child workflow's return value.
- `call` + `branches` — allowed. Branching evaluates against the child's return after it completes.
- `call` + `precondition` — allowed. The precondition gates whether the child is invoked at all.

**Cross-workflow checks** (target existence and cycle detection across the `call` graph) run at workflow-load time; see M004/S01/T02 notes in `docs/AUTHORING.md` for details.

## MCP tool call

A step may declare `action: mcp_tool_call` to invoke an external MCP server tool instead of driving an LLM turn. MCP-tool-call steps are **non-LLM**: they do not carry a `directive_template`, `gates`, `anti_patterns`, or any of the data-collection fields. They just name a server + tool and supply arguments.

```yaml
- id: fetch_weather
  title: Pull forecast
  action: mcp_tool_call
  server: weather
  tool: get_forecast
  args:
    city: "${step_data.intake.city}"
    units: metric
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `action` | string literal `"mcp_tool_call"` | yes | Step-type discriminator. |
| `server` | string | yes | Literal server name; must match an entry in the registry (`mcp_servers.yaml`). Interpolations (`${...}`) are rejected. |
| `tool` | string | yes | Literal tool name on that server. Interpolations are rejected. |
| `args` | mapping | yes | Argument payload. Keys are strings. Values are scalars, literal strings, ref-path strings, nested mappings, or nested lists. |
| `timeout` | number | no | Per-call timeout in seconds. Must be positive. `bool` is rejected. Falls back to `timeout_default` on the server entry when omitted. |

### Args grammar

- Scalars (`int`, `float`, `bool`, `null`) pass as literals.
- String values are either pure literals (no `${` substring) or a **whole-string** ref-path `"${step_data.<sid>[.<field>...]}"`. Mixed interpolation like `"hello ${step_data.name}"` or `"${step_data.x} suffix"` is rejected at load time (`mcp_tool_call_mixed_interpolation_not_supported`). If mixed interpolation ever lands, it will be a non-breaking grammar extension.
- Mappings and lists nest freely; the validator walks the full tree.
- Ref-path strings use the same grammar as `precondition.when_equals.ref` — `step_data.` prefix followed by `.`-separated segments matching `^[A-Za-z_][A-Za-z0-9_-]*$`. Malformed ref-paths are rejected at load (`mcp_tool_call_invalid_ref_path`).

Whether a ref-path actually resolves at runtime depends on which prior steps ran; unresolved refs are a runtime concern, not a load-time one.

### Mutex rules (parse-time)

An `mcp_tool_call` step must not declare any of the following fields. Each violation has its own error code:

| Forbidden field | Error code |
|-----------------|------------|
| `directive_template` | `mcp_tool_call_with_directive_template` |
| `gates`              | `mcp_tool_call_with_gates` |
| `anti_patterns`      | `mcp_tool_call_with_anti_patterns` |
| `call`               | `mcp_tool_call_with_call` |
| `collect`            | `mcp_tool_call_with_collect` |
| `output_schema`      | `mcp_tool_call_with_output_schema` |

Literal-only violations on `server` and `tool` carry the codes `mcp_tool_call_server_not_literal` and `mcp_tool_call_tool_not_literal` respectively.

### Allowed compositions

- `action: mcp_tool_call` + `precondition` — allowed. The precondition gates whether the tool is invoked at all.
- `action: mcp_tool_call` + `branches` / `default_branch` — allowed. Branching evaluates against the tool's return envelope after it lands.
- `action: mcp_tool_call` + `step_description` — allowed. Pure authoring metadata.

### Registry cross-check

Workflow validation accepts an optional `Registry` loaded from `mcp_servers.yaml`. When provided, each `mcp_tool_call` step's `server` field is checked against the registry; an unknown name fails load with the step's id, the missing name, and the list of known names (`mcp_tool_call_unknown_server`).

When the registry is **not** provided, workflows that contain any `mcp_tool_call` step fail load with `mcp_tool_call_registry_required`. Workflows without `mcp_tool_call` steps pass regardless — back-compat for everything that predates this step type.

The CLI entry point `python -m megalos_server.validate <workflow>` accepts an optional `--registry <path>` flag. Without it, the CLI looks for `mcp_servers.yaml` next to the workflow file first, then in the current working directory. First hit wins. If neither location has one and the workflow needs a registry, the CLI surfaces the load-time error and exits non-zero.

See [MCP.md](MCP.md) for integration semantics including registry format, retry behavior, and prompt-injection posture.

## Precondition

A step may declare a `precondition` mapping that gates whether it is reachable based on prior-step output. The grammar has exactly two predicates — never compounded, never coerced.

**`when_equals`** — scalar equality against a ref-path:

```yaml
precondition:
  when_equals:
    ref: step_data.plan.depth
    value: deep
```

**`when_present`** — presence check on a ref-path:

```yaml
precondition:
  when_present: step_data.plan
```

Exactly one of `when_equals` or `when_present` must be present. Never both, never neither. No other predicate keys (no `and`, `or`, `not`, `when`, `if`, `unless`).

**Ref-path grammar.** Refs must start with the literal prefix `step_data.`. After that prefix, segments are dot-split and each must match the identifier regex `^[A-Za-z_][A-Za-z0-9_-]*$`. No empty segments, no escaping, no brackets, no array indexing, no quoted keys. Valid: `step_data.plan`, `step_data.plan.depth`, `step_data.step_1.field_a`. Invalid: `step_data..foo`, `step_data.step_1."field.with.dots"`, `step_data.step_1.field[0]`.

See `docs/AUTHORING.md` §X (landed in M003/S02) for worked examples, the precondition-vs-branches distinction, and cascade-error guidance.

## Validation rules

- The file must parse as valid YAML.
- The root must be a mapping (not a list or scalar).
- All top-level required fields must be present.
- `steps` must be a non-empty list of mappings.
- Each step must contain all five required step keys.
- `gates` and `anti_patterns` must each be lists.
- When a step has `collect: true`, it must also have `output_schema` (otherwise the flag is meaningless — the step collects no structured data).

Multiple errors are reported at once when using `python3 -m megalos_server.validate`.

## Schema versioning

The top-level `schema_version` field declares which schema spec a workflow targets. It is an optional string. When omitted, `load_workflow` fills in `"0.4"` as the default.

Current version: **`0.4`**. This is the schema documented in this file.

| Version | Notes |
|---------|-------|
| `0.1`   | Initial schema (M009). |
| `0.2`   | Adds the optional `collect` boolean on steps; `output_schema` validation errors now include a JSON field path prefix (e.g., `"title: ..."`). |
| `0.3`   | Adds the optional `precondition` field on steps with `when_equals`/`when_present` predicates (M003/S01). |
| `0.4`   | Adds the optional `call` and `call_context_from` fields on steps for sub-workflow invocation (M004/S01). The `enter_sub_workflow` MCP tool is registered as a placeholder; runtime arrives in M004/S02. |

Future schema changes will bump this value explicitly. Unrecognized values pass through without error — the server does not currently reject future or unknown versions. (If cross-version incompatibility becomes a concern, rejection will be added then.)

## Global DO NOT rules

Every tool response from the megálos MCP server includes a fixed list of behavioural rules the LLM must follow. These are hardcoded in `megalos_server/tools.py` as the `_DO_NOT_RULES` constant and are injected into every step response. They are not configurable per workflow today.

The current rules (verbatim):

1. Do NOT skip ahead to later steps.
2. Do NOT produce final artifacts yet.
3. Do NOT ask multiple questions at once.
4. Do NOT proceed until all gates for this step are satisfied.
5. Do NOT submit a step without showing your work to the user and waiting for their confirmation. Each step is a conversation, not a task you complete silently.
6. Do NOT submit multiple steps in a single response. Complete ONE step, present it, wait for the user to respond, then move to the next.
7. Do NOT reveal step names, step numbers, or internal workflow mechanics to the user. The workflow should feel like a natural conversation, not a numbered checklist. Never say things like "Step 2: Decompose and Structure" or "we are now in the plan phase".

## Conversation repair defaults

Every tool response from the megálos MCP server includes a `conversation_repair` dict injected alongside `_DO_NOT_RULES`. It tells the LLM how to react to four common conversational side-effects. The dict has exactly four keys:

| Key | Default | Meaning |
|-----|---------|---------|
| `on_go_back` | `"Guide the user to use revise_step"` | User wants to return to an earlier step. |
| `on_cancel` | `"Confirm cancellation, then use delete_session"` | User wants to abandon the session. |
| `on_digression` | `"Acknowledge, then redirect to current step"` | User wandered off-topic. |
| `on_clarification` | `"Re-explain the current step's directive more simply"` | User asks what the current step means. |

Defaults are hardcoded in `megalos_server/tools.py` as `_CONVERSATION_REPAIR_DEFAULTS`. A workflow YAML can override any subset of the four keys by declaring a top-level `conversation_repair` mapping:

```yaml
conversation_repair:
  on_cancel: "Ask if the user wants to save their partial work before cancelling."
```

Unrecognized keys inside `conversation_repair` are rejected at load time. Non-string values are rejected at load time. Override granularity is per-key — unspecified keys keep the default.

## Directive quality

Inspired by Rasa CALM's flow-description best practices, the `directive_template` (runtime prompt) and `step_description` (authoring metadata) fields both benefit from the same phrasing discipline: **concise, specific, action-oriented, avoiding vague language**.

The LLM reads directives verbatim; vague prose produces vague behavior. A good directive states a concrete action and an observable output. A bad directive gestures at a theme and lets the LLM fill in the gaps.

**Good example** (clarify step, from `example.yaml`):

```yaml
step_description: Capture the user's explicit goal in their own words before any response planning.
directive_template: >-
  Ask the user what they want to accomplish. Do not assume — let the user
  state their goal in their own words before proceeding.
```

**Bad example** (what not to write):

```yaml
step_description: Help the user with their request.
directive_template: >-
  Discuss the topic with the user and try to understand what they need.
  Be helpful and think about the best way to respond.
```

The bad example fails on every axis: "help", "discuss", "try to understand", "think about", "be helpful" are all LLM-filler phrases with no concrete anchor. The good example names a specific action ("capture the goal"), identifies a condition ("in their own words"), and gives an explicit prohibition ("do not assume").

When writing `directive_template` and `step_description`, the rule is: **every sentence should be removable if it doesn't either (a) name a concrete action, (b) name an observable output, or (c) name a concrete prohibition.** If a sentence can be deleted without changing what the LLM should do, delete it.

## Gate enforcement semantics

Each step's `gates` field is a list of natural-language conditions that must be satisfied before the step is considered complete. Gates are **LLM-interpreted**: the server surfaces them to the LLM in every tool response, and the LLM is responsible for verifying each gate is met before calling `submit_step`.

The server does **not** evaluate gate content. It does not parse the gate strings, check them against submission content, or reject submissions whose content fails a gate.

What the server does enforce mechanically:

- **Step sequencing.** `submit_step` rejects submissions for any step that is not the current step. Out-of-order submissions return an error.
- **Revision semantics.** `revise_step` resets `current_step` and invalidates all downstream `step_data`.
- **Session caps.** `start_workflow` refuses to create new sessions when 5 are already active.
- **Output schema** (when declared on a step) — validated mechanically against the submitted content.
- **Intermediate artifacts** (when declared on a step) — each artifact is validated against its own schema before the step can advance.
- **Guardrails** (when declared at workflow level) — mechanical triggers (keyword match, step count, step revisit, output length) evaluated on every `submit_step`.

The distinction: step *sequence* is code-enforced; gate *content* is LLM-interpreted.

## Built-in workflow examples

The five built-in workflows in `megalos_server/workflows/` illustrate the schema:

- **coding** -- `category: professional`, `output_format: structured_code`. Six steps from intent capture to delivery.
- **essay** -- `category: writing_communication`, `output_format: text`. Guided essay from exploration to polished prose.
- **blog** -- `category: writing_communication`, `output_format: text`. Blog post from angle to publication-ready.
- **decision** -- `category: analysis_decision`, `output_format: text`. Structured decision framework.
- **research** -- `category: analysis_decision`, `output_format: text`. Research synthesis from question to findings.
