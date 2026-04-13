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

Multiple errors are reported at once when using `python3 -m server.validate`.

## Built-in workflow examples

The five built-in workflows in `server/workflows/` illustrate the schema:

- **coding** -- `category: professional`, `output_format: structured_code`. Six steps from intent capture to delivery.
- **essay** -- `category: writing_communication`, `output_format: text`. Guided essay from exploration to polished prose.
- **blog** -- `category: writing_communication`, `output_format: text`. Blog post from angle to publication-ready.
- **decision** -- `category: analysis_decision`, `output_format: text`. Structured decision framework.
- **research** -- `category: analysis_decision`, `output_format: text`. Research synthesis from question to findings.
