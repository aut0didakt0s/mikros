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
| `schema_version` | string | no (defaults to `"0.1"`) | Schema spec version this workflow targets. Omit to get the default. |

## Step fields

Each entry in `steps` is a mapping with:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Unique step identifier within the workflow. |
| `title` | string | yes | Human-readable step name. |
| `directive_template` | string | yes | Prompt template sent to the LLM for this step. |
| `gates` | list of strings | yes | Conditions that must be met before the step can be submitted. |
| `anti_patterns` | list of strings | yes | Behaviours the LLM should avoid during this step. |

## Validation rules

- The file must parse as valid YAML.
- The root must be a mapping (not a list or scalar).
- All top-level required fields must be present.
- `steps` must be a non-empty list of mappings.
- Each step must contain all five required step keys.
- `gates` and `anti_patterns` must each be lists.

Multiple errors are reported at once when using `python3 -m megalos_server.validate`.

## Schema versioning

The top-level `schema_version` field declares which schema spec a workflow targets. It is an optional string. When omitted, `load_workflow` fills in `"0.1"` as the default.

Current version: **`0.1`**. This is the schema documented in this file.

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
