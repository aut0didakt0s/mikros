# How to author a megálos workflow

This guide is for anyone who knows YAML and wants to author a megálos workflow from scratch. By the end you will have a schema reference you can read without cross-referencing source code, a set of design principles that keep your workflow from becoming a thing that needs a debugger, and a walked-through example you can copy. Budget roughly thirty minutes the first time through; ten once you have written one.

megálos is a deterministic, lightweight conversational workflow runtime. It borrows structural ideas from [Rasa CALM](https://rasa.com/docs/learn/concepts/calm/) — explicit step IDs, directive templates, gates, and conversation-repair primitives — and exposes them as a flat YAML schema that validates locally and runs over MCP.

## Contents

1. [When does a structured workflow beat free-form chat?](#1-when-does-a-structured-workflow-beat-free-form-chat)
2. [Schema reference](#2-schema-reference)
3. [Design principles](#3-design-principles)
4. [Worked example — build `interview-prep.yaml`](#4-worked-example--build-interview-prepyaml)
5. [Common mistakes](#5-common-mistakes)
6. [Validation workflow](#6-validation-workflow)

---

## 1. When does a structured workflow beat free-form chat?

Most conversations do not need a workflow. A workflow earns its place only when structure produces an outcome that free-form chat cannot reliably reach. Before you author anything, apply this filter:

> **Does a structured multi-step workflow add value over free-form chat? If the answer is no, don't build it.**

A workflow adds value when several of the following are true:

- The task has a **non-obvious order of operations** and skipping ahead produces worse output (e.g., writing code before clarifying the requirement; drafting an analysis before framing the decision).
- There is a **specific artifact** the conversation must eventually produce — a plan, a document, a decision memo — and the quality of that artifact depends on distinct intermediate reasoning steps.
- You want **repeatable output shape** across users or sessions. A workflow enforces structure; free-form chat does not.
- The domain has **recognised failure modes** (e.g., answering before understanding the question, padding with filler) that a directive-plus-gate pair can prevent.

A workflow does NOT add value when the conversation is a single exchange, or when the "steps" are really just turns in an ordinary conversation. The following were explicitly **dropped from megálos scope** because free-form chat already handles them well:

- Writing an email
- Drafting a meeting agenda
- Writing a recipe
- Planning a travel itinerary
- Planning an event
- Drafting a newsletter
- Writing a speech script

If your candidate workflow resembles any of the above — a single artifact with no branching dependencies and no distinct reasoning phases — author it as a well-written prompt instead. You will get better output with less maintenance cost.

**Rule of thumb.** If you cannot name three or more distinct cognitive steps that must happen in order, and at least one gate that would catch a common failure, you are probably reaching for a workflow when a prompt would do.

---

## 2. Schema reference

A megálos workflow is a single YAML file. The canonical reference is [`megalos_server/SCHEMA.md`](../megalos_server/SCHEMA.md); this section summarises the surface so you can author without flipping between files. For the minimal valid example, see [`megalos_server/workflows/example.yaml`](../megalos_server/workflows/example.yaml).

The schema is **v0.2** as of M010. Fields `collect`, `conversation_repair`, and `step_description` are live.

### Top-level fields

| Field | Type | Required | Purpose |
|-------|------|----------|---------|
| `name` | string | yes | Short identifier for the workflow (e.g. `coding`, `essay`). |
| `description` | string | yes | One-line summary of what the workflow guides. |
| `category` | string | yes | Grouping tag (e.g. `professional`, `writing_communication`, `analysis_decision`). |
| `output_format` | string | yes | Expected final output type (e.g. `text`, `structured_code`). |
| `steps` | list | yes | Ordered list of step mappings. Must contain at least one step. |
| `schema_version` | string | no (defaults to `"0.2"`) | Version of the schema spec this workflow targets. Omit to get the current default. |
| `guardrails` | list of strings | no | Global do-not rules applied across all steps, in addition to the built-in `_DO_NOT_RULES`. Rare — most workflows do not need this. |
| `conversation_repair` | mapping | no | Overrides for default repair strings injected into step responses. See the defaults table below. |

### Per-step fields

Each entry in `steps` is a mapping with the following fields.

| Field | Type | Required | Purpose |
|-------|------|----------|---------|
| `id` | string | yes | Unique step identifier within the workflow. |
| `title` | string | yes | Human-readable step name. |
| `directive_template` | string | yes | Prompt template sent to the LLM for this step. |
| `gates` | list of strings | yes | Conditions that must be satisfied before the step can be submitted. |
| `anti_patterns` | list of strings | yes | Behaviours the LLM should avoid during this step. |
| `step_description` | string | no | One-sentence, action-oriented summary of what this step does. Authoring metadata only — not injected into step responses. |
| `collect` | boolean | no | When `true`, marks the step as a structured data-collection step. Requires `output_schema` on the same step. |
| `output_schema` | mapping | no (required when `collect: true`) | JSON-Schema-style mapping describing the structured data the step must produce. |
| `branches` | list | no | Conditional next-step transitions based on collected data. Adopts the branching idea from [Rasa CALM flow steps](https://rasa.com/docs/reference/primitives/flows/flow-steps/). |
| `inject_context` | list of strings | no | Names of earlier-step outputs to inject into this step's directive. |
| `directives` | mapping | no | Behavioural shaping keyed by `tone` / `strategy` / `persona` / `constraints`. Values are strings, except `constraints` which is a list of strings. Use sparingly; prefer a single well-written `directive_template`. |
| `intermediate_artifacts` | list of strings | no | Names of artifacts the step is expected to produce and hand off to later steps. |

For the precise shape of `output_schema`, `branches`, and `inject_context` see `megalos_server/SCHEMA.md` — this guide deliberately does not duplicate the validator's rules.

### Built-in do-not rules (injected into every step response)

Every step response includes the following seven do-not rules, defined in `megalos_server/tools.py`. You do not need to repeat them in your `anti_patterns`; only add domain-specific prohibitions there.

```python
_DO_NOT_RULES = [
    "Do NOT skip ahead to later steps.",
    "Do NOT produce final artifacts yet.",
    "Do NOT ask multiple questions at once.",
    "Do NOT proceed until all gates for this step are satisfied.",
    "Do NOT submit a step without showing your work to the user and waiting for their confirmation. Each step is a conversation, not a task you complete silently.",
    "Do NOT submit multiple steps in a single response. Complete ONE step, present it, wait for the user to respond, then move to the next.",
    "Do NOT reveal step names, step numbers, or internal workflow mechanics to the user. The workflow should feel like a natural conversation, not a numbered checklist. Never say things like 'Step 2: Decompose and Structure' or 'we are now in the plan phase'.",
]
```

### Conversation-repair defaults

megálos adopts conversation-repair primitives from [Rasa CALM](https://rasa.com/docs/pro/build/writing-flows/) — specifically the idea that a flow should declare what to do when the user digresses, cancels, goes back, or asks for clarification, rather than silently derailing. The built-in defaults, from `megalos_server/tools.py`:

```python
_CONVERSATION_REPAIR_DEFAULTS = {
    "on_go_back": "Guide the user to use revise_step",
    "on_cancel": "Confirm cancellation, then use delete_session",
    "on_digression": "Acknowledge, then redirect to current step",
    "on_clarification": "Re-explain the current step's directive more simply",
}
```

Override any of these at the workflow level by adding a `conversation_repair` mapping at the top of your YAML. Only override keys where your domain genuinely differs — the defaults are tuned to work across domains.

---

## 3. Design principles

Five principles separate workflows that feel natural from workflows that feel like a form. Each is a heuristic, not a hard rule; the schema does not enforce them, but the validator cannot tell you whether your workflow is good — only whether it is parseable.

### 3a. When to use `collect` steps (and how to pair them with `output_schema`)

Use `collect: true` when the step's job is to elicit **structured, schema-conformant data** from the user — a list of goals, a decision context, a set of constraints — rather than to reason or produce prose. A collect step is the right tool when:

- The information the user provides is the input to a later step's directive.
- Missing or malformed information would cause later steps to produce degraded output.
- You want the validator to enforce that an `output_schema` is present and checked.

When you set `collect: true`, you **must** provide `output_schema` on the same step. The validator requires this. Keep the schema minimal: only require fields you will actually read downstream. Over-specified schemas become friction; under-specified schemas let malformed data leak forward.

A collect step should have a directive that asks **one thing at a time** (the built-in do-not rule `"Do NOT ask multiple questions at once"` already enforces this across the workflow, but the directive should model the pattern). Do not ask the user to produce JSON; ask them in plain language and let the LLM shape the response into the schema.

### 3b. Directive-quality rules

A good directive names (a) a concrete action, (b) an observable output, or (c) an explicit prohibition in every sentence. Vague verbs — "help", "discuss", "try to understand", "think about", "explore" — are LLM-filler with no concrete anchor. They make the step feel conversational but produce mush.

**Good:**

```yaml
step_description: Capture the user's explicit goal in their own words before any response planning.
directive_template: >-
  Ask the user what they want to accomplish. Do not assume — let the user
  state their goal in their own words before proceeding.
```

**Bad:**

```yaml
step_description: Help the user with their request.
directive_template: >-
  Discuss the topic with the user and try to understand what they need.
  Be helpful and think about the best way to respond.
```

The bad version has no concrete action, no observable output, and no prohibition. "Discuss", "try to understand", "think about", "be helpful" are all filler. The good version names an action ("Ask"), an observable output ("the user has stated a concrete goal"), and a prohibition ("Do not assume").

This mirrors [Rasa CALM's guidance](https://rasa.com/docs/pro/build/writing-flows/) on step prompts: concise, specific, action-oriented.

### 3c. Gate design

A gate is a condition that must be satisfied before the step can be submitted. Gates are the workflow's quality filter — they catch the case where the LLM raced ahead and produced an artifact the step was not ready to produce.

A good gate is **testable by reading the conversation**: a human reviewing the transcript can say yes or no without guessing. A bad gate is vague enough that any output clears it.

- **Good:** `User has stated a concrete goal in their own words.`
- **Good:** `Response directly addresses the stated goal.`
- **Bad:** `The user is happy with the response.`
- **Bad:** `The step is complete.`

Write between one and four gates per step. Zero gates means the step has no quality floor; more than four usually means the step is doing too much and should be split.

### 3d. When to override `conversation_repair` defaults

The four defaults cover the common cases across domains. Override only when your domain genuinely differs. Examples where overrides earn their place:

- A coding workflow might override `on_digression` to preserve the current code context before redirecting.
- A decision-memo workflow might override `on_go_back` to warn the user that going back discards a downstream analysis.

Examples where overrides do NOT earn their place:

- Rewording the default string in a slightly friendlier tone.
- Adding emoji.
- Splitting one default into two near-identical strings.

If you find yourself overriding all four defaults, pause — you are probably reinventing the defaults rather than improving them.

### 3e. The expressiveness ceiling

megálos is deliberately a flat, shallow schema. It does not have loops, subroutines, or conditional compound expressions. If you find yourself wanting any of the following, your workflow has hit the expressiveness ceiling:

- A debugger to trace which step will fire next.
- A diagram on a whiteboard to explain the flow to a colleague.
- A step whose `directive_template` contains more branching logic than the YAML's `branches` field can express.
- More than roughly a dozen steps in a single file.

**If you need a debugger, split the workflow.** Two workflows that each do one thing well are always better than one workflow that tries to do two things. Splitting is cheap: a second YAML file, a second `name`, a second `/run_workflow` call by the user. The ceiling is a feature — it is the reason megálos workflows stay readable.

---

## 4. Worked example — build `interview-prep.yaml`

This section builds a complete workflow from zero, one stage at a time, so you see the schema grow under your hands. The finished file lives at [`docs/examples/interview-prep.yaml`](examples/interview-prep.yaml) — open it in a second editor pane if you want to compare against the end state as you go. It is shipped as a **teaching artifact**: not a production workflow, not registered with any server, and deliberately categorised as `teaching_example` so nobody confuses it with something a domain repo should pick up.

The workflow walks a candidate through interview preparation in five steps, following a natural arc:

1. **`identify_role`** — collect the target role, experience level, and company name.
2. **`research_company`** — synthesise what an interviewer at that company likely cares about.
3. **`practice_questions`** — generate three questions grounded in role and company.
4. **`refine_answers`** — critique the candidate's drafts for specificity.
5. **`mock_interview`** — run a short role-play exchange.

We will not get there in one leap. Each sub-section below adds a little more of the schema until the file passes the validator.

### 4a. Top-level fields

Every workflow starts with four required top-level strings plus a non-empty `steps` list. Start a new file at `docs/examples/interview-prep.yaml` with just the header:

```yaml
name: interview_prep
description: Guide a candidate through role-targeted interview preparation.
category: teaching_example
output_format: text

steps: []
```

Why each field:

- **`name`** is the short identifier the runtime uses; keep it snake_case and unique within your domain repo.
- **`description`** is one line, read by humans browsing the workflow list — state the outcome, not the mechanism.
- **`category`** groups the workflow for listing tools; for a teaching artifact, `teaching_example` makes it obvious this is not production.
- **`output_format`** hints at what the final artifact is — here, a conversational `text` exchange.

`steps: []` is a placeholder. The validator will reject this as-is (it requires at least one step), which is fine: we are about to add one.

### 4b. The first step skeleton

A step needs five required fields: `id`, `title`, `directive_template`, `gates`, and `anti_patterns`. Replace the empty `steps` list with a first step:

```yaml
steps:
  - id: identify_role
    title: Identify the Target Role
    directive_template: >-
      Ask the user which role they are preparing for, their current experience
      level, and the company name. Ask for one piece of information at a time.
      Do not begin research or question generation until all three are captured.
    gates: []
    anti_patterns: []
```

The `directive_template` is the heart of the step — it is the prompt the LLM will act on. Notice the shape: a concrete action (`Ask`), a specific observable output (three named fields), and an explicit prohibition (`Do not begin ... until all three are captured`). This is the directive-quality rule from [§3b](#3b-directive-quality-rules) applied end-to-end. A weaker version — *"Help the user identify what role they want to prep for"* — would be shorter and also useless, because it names no action the LLM can verify it took.

### 4c. Adding `gates` and `anti_patterns`

Empty `gates` and `anti_patterns` lists parse, but they leave the step with no quality floor and no prohibitions. Fill them in:

```yaml
  - id: identify_role
    title: Identify the Target Role
    directive_template: >-
      Ask the user which role they are preparing for, their current experience
      level, and the company name. Ask for one piece of information at a time.
      Do not begin research or question generation until all three are captured.
    gates:
      - User has stated a concrete target role
      - User has stated an experience level
      - User has stated a company name
    anti_patterns:
      - Inferring the role from prior conversation
      - Asking for all three fields in a single question
```

Each gate is testable by reading the transcript — the [§3c](#3c-gate-design) rule. A reviewer can answer yes or no without guessing what *"User is ready"* would mean. The `anti_patterns` name two failure modes that are specific to this step, not general workflow hygiene — the built-in do-not rules ([§2](#2-schema-reference)) already cover the generic prohibitions (skip-ahead, multi-question-at-once, etc.), so there is no need to repeat them here.

Rule of thumb: if every one of your `anti_patterns` would apply to any step of any workflow, you are restating the built-ins. Cut them and add something step-specific, or leave the list short.

### 4d. Turning it into a `collect` step with an `output_schema`

`identify_role` is not trying to reason or produce prose — its whole job is to elicit three structured fields that later steps will reference. That is exactly the case [§3a](#3a-when-to-use-collect-steps-and-how-to-pair-them-with-output_schema) describes. Mark the step as a collect step and pair it with an `output_schema`:

```yaml
  - id: identify_role
    title: Identify the Target Role
    step_description: Collect the target role, experience level, and company name before any preparation work.
    directive_template: >-
      Ask the user which role they are preparing for, their current experience
      level, and the company name. Ask for one piece of information at a time.
      Do not begin research or question generation until all three are captured.
    collect: true
    output_schema:
      type: object
      required: [target_role, experience_level, company_name]
      properties:
        target_role:
          type: string
          minLength: 2
        experience_level:
          type: string
          minLength: 2
        company_name:
          type: string
          minLength: 2
    gates:
      - User has stated a concrete target role
      - User has stated an experience level
      - User has stated a company name
    anti_patterns:
      - Inferring the role from prior conversation
      - Asking for all three fields in a single question
```

Two things changed:

1. **`collect: true`** tells the runtime (and the validator) that this step produces structured data. The validator enforces a hard pairing rule: if `collect: true`, there **must** be an `output_schema` on the same step. Delete the schema and the validator will fail the file. This is deliberate — it means a step promising structured output cannot silently drift into producing prose.
2. **`step_description`** is a one-sentence action-oriented summary. It is authoring metadata only — never injected into step responses — but it is the sentence that tells a later reader what the step exists for. A step without one is harder to review.

The schema is minimal: three required strings with a `minLength` floor. Resist the urge to add `enum` lists of "valid roles" or regex patterns — over-specified schemas become friction, and an under-specified schema would let a blank string through. `minLength: 2` is the boring, correct answer.

### 4e. Fleshing out the remaining steps and running the validator

With `identify_role` in shape, the remaining four steps follow the same recipe: required fields first, then optional `step_description` and `directives` where they earn their place. Two patterns worth calling out:

- **`step_description` on at least two steps.** Short, action-oriented, one sentence. It is cheap authoring hygiene and pays off the first time you skim a ten-step workflow looking for the one that collects X.
- **`directives` for tone and strategy.** Use this when the `directive_template` is doing its job and you want one small nudge on *how* it is done, not *what* it does. In the finished file, `research_company` sets `directives.tone` to keep the brief grounded, and `refine_answers` sets `directives.strategy` to push for specificity. A well-written `directive_template` does most of the work; `directives` is a small lever, not a rewrite of the prompt.

Open the finished [`docs/examples/interview-prep.yaml`](examples/interview-prep.yaml) in your editor to see the completed workflow — the rest of the steps are written in the same style as `identify_role` and do not need re-deriving here.

When the file is complete, run the validator from the repository root:

```bash
python -m megalos_server.validate docs/examples/interview-prep.yaml
```

Expected output:

```
Valid.
```

Exit code `0`. That is the full success signal — the validator is terse on purpose.

**See a validation error for yourself.** Comment out the `name:` line at the top of the file and re-run:

```bash
python -m megalos_server.validate docs/examples/interview-prep.yaml
```

You will see:

```
ERROR: Workflow missing required key: 'name'
```

Exit code `1`. The validator prints one `ERROR:` line per problem on stderr and exits non-zero. Restore the `name` line and you are back to `Valid.`

That is the full authoring loop: edit, validate, read the error, fix, re-validate. If you are about to register the workflow with a running server, the same validator runs there — a workflow that passes locally passes on load.

---

## 5. Common mistakes

These are the seven mistakes we see most often in first-draft workflows. Each is paired with a concrete fix and a cross-reference to the relevant section of this guide. Skim the headers on your first read; return to the details when the validator or a reviewer flags a problem.

**1. Vague directives.** A `directive_template` built on verbs like *"help the user"*, *"discuss the topic"*, *"try to understand"*, or *"think about"* gives the LLM nothing to verify. The step runs, the model fills the slot with plausible prose, and no gate can tell whether the step did its job.
*Fix:* rewrite so every sentence names a concrete action, an observable output, or an explicit prohibition. See the good-vs-bad example in [§3b](#3b-directive-quality-rules).

**2. Gates that restate the directive.** A gate like *"The step is complete"* or *"The user got a helpful response"* is not testable by reading the transcript — it just restates the intent of the step. Any output clears it, so the gate adds no quality floor.
*Fix:* write gates as observable conditions a reviewer can answer yes or no to from the transcript alone. See the good-vs-bad list in [§3c](#3c-gate-design).

**3. `collect: true` without an `output_schema`.** Marking a step `collect: true` without an `output_schema` on the same step is a hard validator error:

```
ERROR: Step 'X' has collect: true but is missing required 'output_schema'
```

*Fix:* either add a minimal `output_schema` (required fields + `minLength` floors, nothing more) or drop `collect: true` if the step is really producing prose. See [§3a](#3a-when-to-use-collect-steps-and-how-to-pair-them-with-output_schema).

**4. Over-permissive `output_schema`.** A schema with no `required` list, no `minLength`, and no enums lets blank strings and missing fields flow through to later steps. The validator passes the file; the runtime then hands downstream steps garbage input.
*Fix:* require every field a later step actually reads, and add `minLength: 2` (or larger) to every string you care about. Do not over-specify with `enum` lists or regex — that is the opposite failure mode. The `identify_role` step in [`docs/examples/interview-prep.yaml`](examples/interview-prep.yaml) shows the right level of detail.

**5. Workflows longer than roughly seven steps.** Once a workflow grows past seven steps, it almost always needs a debugger to reason about and stops fitting in one reader's head. This is the expressiveness ceiling in [§3e](#3e-the-expressiveness-ceiling).
*Fix:* split the workflow into two files. Two workflows that each do one thing well are always better than one workflow that tries to do two. A second `name`, a second YAML file, a second `/run_workflow` call — that is the full cost of splitting.

**6. Revealing step numbers or internal mechanics in `directive_template` content.** Phrases like *"Step 2: Decompose and Structure"*, *"we are now in the plan phase"*, or references to system prompts in step content break the conversation's feel — the workflow starts sounding like a numbered checklist. This is banned by built-in do-not rule #7 (see [§2](#2-schema-reference)).
*Fix:* write directives as if the workflow is a natural conversation. The runtime knows which step is active; the user does not need to.

**7. `directives` written as a list of strings.** The `directives` field is a **mapping** keyed by `tone` / `strategy` / `persona` / `constraints`, not a list. Writing `directives: [ "Be specific.", "Stay grounded." ]` gets the validator error:

```
ERROR: Step 'X' directives must be a mapping
```

*Fix:* rewrite as a mapping — `directives: { tone: "Grounded and specific; no marketing language." }`. See the `directives` row in [§2](#2-schema-reference)'s per-step fields table, and the `research_company` step in [`docs/examples/interview-prep.yaml`](examples/interview-prep.yaml) for a live example.

**A note on `anti_patterns`.** If every one of a step's `anti_patterns` would apply to any step in any workflow ("do not skip ahead", "do not answer too fast"), you are restating the built-in `_DO_NOT_RULES`. The built-ins are already injected into every step response — see [§2](#2-schema-reference). Either cut the list and leave it short, or replace the generic entries with something specific to the step's failure modes.

---

## 6. Validation workflow

The validator is the authoring-time quality gate. It is fast, offline, and terse on purpose — you run it after every edit, read the error if there is one, fix, and run it again. No code executes; only the YAML is parsed and checked against the schema.

### 6a. The clean run

From the repository root, run the validator against your workflow file:

```bash
python -m megalos_server.validate docs/examples/interview-prep.yaml
```

Expected output:

```
Valid.
```

Exit code `0`. That is the full success signal. If your shell prints a prompt immediately after `Valid.`, your workflow is schema-conformant and ready to load. The validator deliberately says nothing about quality — that is what the design principles in [§3](#3-design-principles) and the common mistakes in [§5](#5-common-mistakes) are for.

### 6b. A deliberate error

To see what a failure looks like, remove a required field. Take your working `interview-prep.yaml` and delete (or comment out) the first step's `id` line, so the step looks like:

```yaml
steps:
  # - id: identify_role
  - title: Identify the Target Role
    ...
```

Re-run the validator:

```bash
python -m megalos_server.validate docs/examples/interview-prep.yaml
```

You will see:

```
ERROR: Step 0 ('?') missing keys: ['id']
```

Exit code `1`. Notes on how the validator reports errors:

- Every problem is one line prefixed with `ERROR:`, printed to stderr.
- The validator does **not** stop at the first error — it reports every problem it can find in a single pass, so you can fix a batch at once.
- The `'?'` placeholder is what the validator prints when the step has no `id` to refer to it by; as soon as you add the `id` back, later errors (if any) will reference the step by its real name, e.g. `Step 'identify_role'`.

Restore the `id` line and you are back to `Valid.` This is the full authoring loop: edit, validate, read the error, fix, re-validate. Most first-draft workflows pass on the second or third run; after a few workflows you will start writing files that pass on the first try.

### 6c. From validation to deployment

Passing the validator means your workflow is loadable — every downstream runtime (the local `megalos-server`, a domain server on Horizon, a composed Remix) runs the same validator on startup, so a file that validates locally validates on deploy. It does **not** mean the workflow produces good conversations; that is what the design principles in [§3](#3-design-principles), the common mistakes in [§5](#5-common-mistakes), and a human reviewer are for.

To deploy your workflow to production, see [`docs/deploying-a-domain-server.md`](deploying-a-domain-server.md).
