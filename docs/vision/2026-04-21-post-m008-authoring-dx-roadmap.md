# Post-M008 Authoring DX Roadmap — M009, M011, M012

**Status:** Planning draft. Supersedes nothing; queued behind M008 (Performance Baseline).
**Author:** Diego (with strategic review)
**Date:** 2026-04-21

---

## 1. Context

The M001–M008 program closes the CALM-parity gap in the megálos runtime: hardening, sub-workflows, multi-flow context, MCP-tool actions, security, and a performance baseline. Once M008 ships, the runtime is feature-complete against the comparison set and stable enough that authoring volume — not runtime capability — becomes the next bottleneck.

The three recommendations absorbed from the Gemini 3.1 Pro review (after rejecting the Python escape hatch) all target the same surface: **the developer experience of authoring, debugging, and reviewing megálos workflow YAML**. They are not runtime features. They do not change what megálos can do at execution time. They change how fast, how confidently, and how visibly an author can produce a correct workflow.

This is the right phase for them. While M001–M008 was building the engine, M009–M012 builds the cockpit.

A non-trivial constraint: the vision-v4 doc states the expressiveness ceiling explicitly — *"The schema must NOT become Turing-complete. The moment it needs a debugger, it's failed."* This document interprets that rule strictly. None of M009–M012 may justify additional schema expressiveness. Tools that make the existing schema legible are in scope; tools that paper over an over-grown schema are out of scope.

---

## 2. Decision: three separate milestones, sequenced by ROI

Each recommendation is its own milestone (M009, M011, M012), shipped in that order. The alternative considered — a single combined "Authoring DX" milestone — was rejected for three reasons:

1. **Scope mismatch.** M009 is a 200–300 LOC static analyzer over a parsed schema. M012 includes a separate VS Code extension repo with TypeScript, marketplace publishing, and a non-Python release pipeline. Combining them violates the iron rule at the milestone level: a milestone should fit in a single coherent reasoning frame.
2. **Independent shippability.** Each milestone delivers usable value alone. There is no dependency that forces them to ship together. Bundling them only delays the cheap wins (M009) behind the expensive one (M012).
3. **Cancellation surface.** If priorities shift — for example, if Phase F resumption pulls earlier than expected — separate milestones can be deferred individually. A combined milestone is harder to suspend partway.

The sequencing (M009 → M011 → M012) is by ascending scope and descending immediacy of value. The smallest milestone delivers the fastest, most visible improvement; the largest is deferred until the cheaper wins are landed.

---

## 3. M009 — Workflow Visualization

### 3.1 Goal

Generate a visual representation of any megálos workflow YAML as a Mermaid diagram, emitted via a CLI command on the megálos-server runtime. Branches, preconditions, and sub-workflow calls render as graph elements; the diagram is committable to documentation and reviewable in pull requests.

### 3.2 Scope

**In scope:**
- A new module `megalos_server/diagram.py` that parses a workflow via the existing `validate_workflow` path, then emits a Mermaid `flowchart` definition.
- CLI invocation: `python -m megalos_server.diagram <workflow.yaml>` writing Mermaid to stdout.
- Rendering coverage: sequential steps, `branches`, `precondition` (both `when_equals` and `when_present`), `call` (sub-workflow invocation), `action: mcp_tool_call` steps (visually distinguished from LLM steps).
- Integration into the validator's clean run output as a flag: `python -m megalos_server.validate --diagram` writes both validation status and the Mermaid block.
- A small docs page `docs/VISUALIZATION.md` showing how to embed generated diagrams in workflow READMEs.

**Out of scope:**
- HTML rendering (Mermaid is the artifact; the renderer is whatever the reader uses — GitHub, VS Code, Mermaid Live).
- Dynamic visualization of running sessions. Static analysis of the YAML only.
- Auto-publishing diagrams to documentation sites.
- Any new YAML field intended to influence rendering (no `display:` annotations).

### 3.3 Slices

- **S01 — Sequential + branches renderer.** Loads YAML, walks `steps`, emits a linear Mermaid flow with branch arrows. Validates against three of the simpler domain workflows (essay, decision, coding). LOC budget: ~200.
- **S02 — Preconditions and sub-workflow calls.** Adds dotted-edge rendering for `precondition`-gated reachability and a sub-graph reference for `call`. Validates against fixtures already exercising these features. LOC budget: ~150.
- **S03 — CLI and validator integration.** Wires `python -m megalos_server.diagram`, the `--diagram` flag on validate, and the docs page. LOC budget: ~100.

### 3.4 Success criteria

- Every workflow currently in `megalos-writing`, `megalos-analysis`, and `megalos-professional` produces a syntactically valid Mermaid diagram.
- Diagrams correctly render branch options as labeled edges.
- Diagrams correctly render sub-workflow calls as named subgraph references (the child's full diagram does not need to be inlined).
- A reviewer of a workflow PR can paste the generated Mermaid into a comment and visually confirm the change.
- No new runtime dependencies added (Mermaid output is plain text).

### 3.5 Risk

**Low.** Pure static analysis. No new dependencies. The output format is text, so failures are visible immediately. The main risk is overscoping into an HTML viewer or live-session visualization — both are explicitly out of scope.

### 3.6 Naming nit

Do not call it "DAG visualization" in any user-facing surface. Megálos workflows are not arbitrary directed graphs; they are sequential flows with conditional branches, preconditions, and call-stack composition. The right framing is "workflow diagram" or "flow visualization." Calling it a DAG invites the LangGraph mental model that megálos explicitly rejects.

---

## 4. M011 — Dry-Run Inspector

### 4.1 Goal

Provide a CLI that loads a workflow and lets the operator step through it interactively, providing mock LLM responses in place of a real model. Exercises the actual runtime execution path — including gates, branches, preconditions, output schema validation, sub-workflow descent, and parent resume — without making any LLM calls.

### 4.2 Scope

**In scope:**
- A new module `megalos_server/dryrun.py` exposing an interactive REPL.
- CLI invocation: `python -m megalos_server.dryrun <workflow.yaml>`.
- The dry-run loop **must use the production execution path** (`tools.submit_step` and friends) with a thin input layer that sources LLM responses from stdin (or a `--responses-file` for scripted runs). No parallel implementation of step transitions is permitted.
- Display at each step: step ID, title, directive template (rendered with current `step_data`), gates, anti-patterns, and any precondition status.
- Output schema feedback: when a step has `output_schema`, validation runs against the operator's mock response and validation errors are shown verbatim, with the same retry budget as production.
- Branch selection: when a step declares `branches`, the operator is prompted to choose a branch target; precondition-blocked steps are visibly marked unreachable.
- Sub-workflow descent: when a `call` step is reached, the dry-run descends into the child workflow and propagates the child's final artifact back to the parent on completion (mirroring the M004 path).
- A `--responses-file` flag that accepts a YAML file mapping step IDs to mock response content, enabling scripted dry-runs in CI.

**Out of scope:**
- Real LLM invocation. The dry-run never calls an LLM. (Authors who want a real LLM in the loop should use a deployed instance.)
- Recording dry-run sessions for replay (defer to M012+ if demand emerges).
- A graphical interface. CLI only.
- A second runtime. The dry-run **must be the production runtime with a mock input source**.

### 4.3 Slices

- **S01 — Sequential happy path.** REPL skeleton, stdin-sourced responses, sequential step transitions via the production `submit_step` path. LOC budget: ~250.
- **S02 — Schema validation and gates.** Wires `output_schema` validation, retry budget display, and gate listing into the REPL output. LOC budget: ~150.
- **S03 — Branches and preconditions.** Branch prompt UX, precondition resolution display, unreachable-step marking. LOC budget: ~200.
- **S04 — Sub-workflow descent and parent resume.** Tests against the existing call-frame fixtures. LOC budget: ~150.
- **S05 — Scripted runs via `--responses-file`.** Enables CI integration. LOC budget: ~100.

### 4.4 Success criteria

- An operator can step through every workflow currently deployed without writing any test code.
- All schema validation, branching, precondition, and sub-workflow behavior matches production exactly (verified by reusing existing test fixtures).
- A scripted dry-run can be added to each domain repo's CI as a smoke test (no LLM cost, runs in seconds).
- No duplication of execution logic from `megalos_server/tools.py`. The dry-run module is purely an input/output adapter.

### 4.5 Risk

**Medium.** The critical failure mode is drift: an author writes a "simplified" version of `submit_step` for the dry-run, and over time the dry-run diverges from production. The mitigation is architectural — the dry-run module **must** call the production `submit_step` with a mocked LLM input source, not reimplement the transition logic. This constraint should be enforced in the milestone's S01 plan and re-checked at every slice gate.

A secondary risk is over-engineering the REPL UX (history, autocomplete, color-coded output). Boring is a feature. The first version should be plain prompts and plain output.

### 4.6 Why this is more than a CLI wrapper around tests

The existing test fixtures (`tests/fixtures/workflows/`) and harness already exercise the runtime end-to-end with synthetic inputs. The dry-run inspector is genuinely additional because:

1. **Authors are not test engineers.** A workflow author needs to see the directive template rendered, the gates listed, and the schema feedback at each step — not write a pytest case.
2. **Interactive exploration finds issues that scripted tests do not.** Mid-workflow "what happens if I respond like this?" is a different mode than "assert this trace."
3. **CI smoke-testing of full domain workflows.** Today, the test harness uses minimal fixture workflows. The dry-run with `--responses-file` lets the actual production workflows run end-to-end in CI on every change.

---

## 5. M012 — Authoring IDE Support

### 5.1 Goal

Provide keystroke-time error feedback, autocomplete, and inline documentation for megálos workflow YAML in the IDEs authors use day-to-day. Strengthen the 30-minute authoring target by moving error feedback from validator-run-time to typing-time.

### 5.2 Scope

This milestone splits cleanly into two slices that are *deliberately* sequenced as an early cheap win followed by a larger commitment. The split is inside the milestone, not across milestones, because they share a common artifact (the JSON Schema export) and a common goal.

**S01 (in `megalos-server` repo) — JSON Schema export and IDE binding docs.**

- Generate a complete JSON Schema document from `megalos_server/schema.py` as a build artifact: `schemas/megalos-workflow.schema.json`.
- The schema must cover every documented field, every constraint, every default. Validation parity with `validate_workflow` is the success criterion for the schema content.
- Document binding for the two relevant IDEs:
  - **VS Code:** via the Red Hat YAML extension's `yaml.schemas` setting.
  - **JetBrains family:** via the built-in JSON Schema mappings.
- A short docs page `docs/IDE_SETUP.md` shows the two configurations.

This slice alone delivers ~80% of the IDE support value at ~10% of the cost. Authors who already have the Red Hat YAML extension installed will get autocomplete, hover documentation, and error squiggles on the next workflow they open.

**S02+ (in a new `megalos-vscode` repo) — Custom diagnostics via Language Server.**

- New repository: `github.com/agora-creations/megalos-vscode` (TypeScript).
- A VS Code extension that provides diagnostics beyond what JSON Schema can express:
  - Cross-step references resolve (e.g., `precondition` references a real prior step).
  - `branches` targets are valid step IDs in the same workflow.
  - `call` targets are valid workflow names in the same domain repo.
  - Skipped-predecessor checks (the same logic as `validate_workflow`'s existing static analysis).
- Hover documentation pulled from `docs/AUTHORING.md` field descriptions.
- Optional: snippet templates for common patterns (`collect` step with `output_schema`, `branches` block, `call` step).

**Out of scope:**

- A full Language Server Protocol implementation usable by Vim, Emacs, and arbitrary editors. VS Code first; LSP-ification is a later candidate (M012+).
- Schema-driven snippet generation beyond a hand-curated set.
- Inline preview of dry-run output (cross-tool integration deferred).
- Marketplace publishing automation (manual publish is fine for v1).

### 5.3 Slices

- **S01 — JSON Schema export and IDE binding** (in `megalos-server`). Generate `schemas/megalos-workflow.schema.json` with full coverage; document VS Code and JetBrains setup. LOC budget: ~300 (mostly schema definition + docs).
- **S02 — `megalos-vscode` repo scaffold** (new repo). TypeScript project, build pipeline, basic activation, JSON Schema bound. LOC budget: ~400.
- **S03 — Custom diagnostics** (in `megalos-vscode`). Cross-step reference checking, branch target validation, sub-workflow target validation. LOC budget: ~500.
- **S04 — Hover docs and snippets** (in `megalos-vscode`). Field-level hover from authoring guide; curated snippet set. LOC budget: ~300.
- **S05 — Manual marketplace publish** (in `megalos-vscode`). Publisher account, release notes, install link in main repo README. No LOC budget; release work.

### 5.4 Success criteria

- Authors with VS Code + Red Hat YAML extension get autocomplete and validation on the first workflow they open after configuring the schema URL (S01 alone).
- Authors with the `megalos-vscode` extension installed get all of the above plus inline cross-reference diagnostics (S03).
- The 30-minute timed authoring target measurably improves for novice authors (re-run the M012-followup timing exercise from the Phase E plan after S01 and again after S03).
- The exported schema stays in sync with `schema.py` automatically — verified by a test that loads both and asserts equivalence.

### 5.5 Risk

**Medium-high.** Three distinct sources:

1. **Foreign technology stack.** The `megalos-vscode` repo introduces TypeScript, the VS Code extension API, and the marketplace release pipeline. None of these are present in megálos today. The risk is that the milestone consumes more time than the value warrants.
2. **Schema export drift.** If the JSON Schema export and the canonical `schema.py` drift apart, IDE feedback diverges from validator feedback — worse than no IDE feedback at all. Mitigation: a parity test (in the main repo) that fails CI if the export does not match the runtime schema.
3. **Diagnostic over-reach.** The temptation to encode workflow design rules (not just structural rules) as diagnostics. Resist. Diagnostics enforce schema validity; they do not enforce authoring quality. Authoring guide stays as prose; diagnostics stay structural.

### 5.6 The deliberate cheap-win-first split

S01 ships in the main repo and delivers usable IDE support to anyone with the Red Hat YAML extension already installed. It is a single PR, possibly an afternoon of work. If S01 ships and S02–S05 are deferred (because Phase F pulls in, or because the timed-authoring number after S01 is already good enough), megálos still gets the bulk of the IDE-support benefit.

This is the correct shape for a milestone that includes a high-cost component: front-load the cheap value, defer the expensive value to slices that can be cancelled cleanly.

---

## 6. Cross-milestone constraints

The following constraints apply to all three milestones and should be checked at each slice gate.

**No new schema expressiveness.** None of these milestones adds a YAML field, removes a constraint, or weakens a validator rule. If a tool needs the schema to change to be useful, the tool is wrong, not the schema.

**No new runtime dependencies in megalos-server.** The runtime stays at three deps (`fastmcp`, `pyyaml`, `jsonschema`). Mermaid output is text. The dry-run REPL uses stdlib. JSON Schema export uses stdlib. The `megalos-vscode` repo has its own dep graph; it does not influence megalos-server.

**No coupling between milestones at the code level.** M009's diagram module does not import from M011's dry-run module, and neither imports from anything in `megalos-vscode`. Each is a standalone tool over the schema.

**Domain repos are passive consumers.** None of these milestones requires changes to `megalos-writing`, `megalos-analysis`, or `megalos-professional` beyond optionally adding a CI smoke-test (M011 S05) or a README diagram (M009 S03). The domain repos must continue to function with no awareness of these tools.

**Boring beats clever.** Each milestone is a CLI or a static artifact. No daemons, no servers (other than the LSP, which is the unavoidable VS Code architecture), no cross-process state.

---

## 7. Sequencing rationale

The order M009 → M011 → M012 is chosen against three criteria:

1. **Ascending scope.** M009 is ~450 LOC across three slices. M011 is ~850 LOC across five slices. M012 is ~1500 LOC across five slices and a new repo. Smallest first.
2. **Descending immediacy of value.** M009's output (Mermaid in PRs and READMEs) is visible to anyone reading the repo. M011's output (interactive dry-runs, CI smoke tests) is visible to authors and CI. M012's output (IDE feedback) is visible only to authors at edit time, and only after they configure their IDE.
3. **Risk staging.** M009 is low-risk (text output, static analysis). M011 has the drift-from-production risk that needs architectural discipline. M012 introduces a new tech stack and a new repo. Building confidence with the easier milestones first is the conservative path.

A reordering — for example, prioritizing M012 S01 (JSON Schema export) before M009 — would also be defensible, since it is even cheaper than M009 and benefits everyone immediately. If the milestones are sequenced strictly, the cheap M012 S01 win has to wait. **One acceptable refinement:** lift M012 S01 out of M012 and ship it as a small task before M009. The remaining four slices of M012 then become M012 proper. This is a reasonable variant; flagged here, not adopted by default.

---

## 8. Out of scope for all three milestones

These appear in adjacent design conversations and are explicitly excluded:

- **A no-code visual authoring studio.** Rejected by the vision doc; rejected here.
- **Python or arbitrary-code escape hatches in YAML.** Rejected for the reasons in the prior strategic review.
- **A second runtime, a second schema, or a second source of truth.** The schema in `megalos_server/schema.py` is canonical. Every tool here consumes from it; no tool diverges from it.
- **Multi-author collaboration features.** Comments, locks, presence indicators, change proposals. Out of scope; YAML in git already solves this.
- **Workflow-marketplace mechanics.** Discovery, ratings, install commands. Not relevant until the authoring base is large enough to justify it; defer indefinitely.

---

## 9. Open questions

The following should be resolved during the `/discuss` gate of each milestone, not preemptively:

1. **Mermaid version and renderer compatibility.** GitHub renders a specific Mermaid version; VS Code may render a different one. Pin the dialect in M009 S01 against GitHub's renderer.
2. **`--responses-file` format.** YAML or JSON? YAML is more consistent with the rest of megálos but JSON has lower parser-error surface in CI. Decide in M011 S05.
3. **Diagnostic severity in the VS Code extension.** Should missing required fields be errors or warnings? Should a precondition referencing a non-existent step be an error or a warning? Default error; revisit if it produces noise.
4. **`megalos-vscode` repo licensing.** The main repo has a license; the extension repo will need its own. Decide before S02 ships.
5. **Timed-authoring re-measurement.** The Phase E follow-up timing exercise was deferred. M012's success criteria assume it gets re-run after S01 and S03. Confirm a willing test subject exists before locking M012's success criteria to that measurement.

---

## 10. Post-M012 evaluation

After M012 ships, run the post-milestone evaluation across the existing five dimensions (timed-user validation, workflow completion rate, multi-provider validation, runtime stability boundary, documented correction loop recovery cases). Add one more dimension specific to this trio:

- **Authoring DX dimension.** Time-to-first-valid-workflow for a novice author with the IDE extension installed and the dry-run inspector available. Target: < 20 minutes (an improvement over the 30-minute baseline). Compare against the pre-M009 measurement to validate the milestones delivered their stated value.

If the authoring DX dimension does not improve measurably, the assumption that authoring volume is the bottleneck was wrong. That is itself a useful finding and should re-route attention to whatever the actual bottleneck turned out to be.

---

## 11. Summary

| Milestone | Scope | Slices | LOC est. | Risk | Value visibility |
|-----------|-------|--------|----------|------|------------------|
| M009 — Workflow Visualization | Mermaid generator over schema | 3 | ~450 | Low | High (PRs, READMEs) |
| M011 — Dry-Run Inspector | Interactive + scripted CLI dry-run | 5 | ~850 | Medium | Medium (authors, CI) |
| M012 — Authoring IDE Support | JSON Schema export + VS Code extension | 5 | ~1500 + new repo | Med-high | Medium (authors at edit time) |

Three milestones, sequenced smallest-to-largest, each independently shippable, each delivering a different surface of authoring developer experience without expanding the schema or the runtime's expressiveness. Together they close the loop on the YAML-as-source-code thesis: legible, debuggable, validated at the keystroke.
