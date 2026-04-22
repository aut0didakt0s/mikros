# megálos — Vision v5

**Status:** Canonical. Supersedes [`2026-04-15-megalos-vision-v4.md`](./2026-04-15-megalos-vision-v4.md), which becomes a historical document. Where v4 and v5 conflict, v5 governs.
**Author:** Diego Marono (with strategic review)
**Date:** 2026-04-22
**Principal change from v4:** Reverses v4's rejection of a no-code visual authoring studio, introduces a fifth customer shape (non-technical authors), and pins six mechanical guardrails that prevent the failure mode v4 was originally guarding against.

---

## 0. Why this document exists

Vision-v4 rejected a no-code visual authoring studio as incompatible with megálos's thesis. That rejection was load-bearing — it was part of how megálos positioned against Landbot, Voiceflow, Botpress, Typebot, and the no-code studio layer of Rasa CALM. A strategic reversal of that magnitude requires a new canonical document that honestly names what changed, argues for the reversal on its merits, and specifies the constraints that keep the reversed position from collapsing into the failure mode the original rejection was guarding against.

This is that document. It does not pretend vision-v4 was wrong when it was written. It argues that the conditions under which v4 was written have changed enough that a different answer is now correct, and it pins the mechanical constraints that make the new answer honest.

Every decision in v4 that is not explicitly amended here carries forward unchanged. The technical thesis — LLM interprets, code enforces, schema stays simple, rigor is non-negotiable — is preserved in full. The change is in who megálos serves and how they reach the runtime, not in what the runtime does.

---

## 1. What vision-v4 rejected, and why

Vision-v4's §9.5 stated: *"The YAML schema must not become Turing-complete. No debugger, no interpreter, no recursion. Rationale: Target author is a domain expert who knows YAML, not a programmer."*

Vision-v4's §9.6 rejected Rasa CALM's no-code studio as part of the rationale for treating CALM as a design reference rather than a template: *"CALM is an enterprise platform with a no-code studio, an NLU pipeline, a proprietary runtime, and multi-hundred-line configuration files. megálos's competitive advantage is simplicity."*

Vision-v4's §7 differentiated megálos against Voiceflow, Botpress, and Typebot specifically on the axis of *"Text-file-native — YAML, not drag-and-drop. Version-controlled, diffable, reviewable"* and claimed as a product property that *"a domain expert who knows YAML can write a workflow without a no-code studio"*.

Together, these were not three independent positions. They were one position: **the target author is technical, and the product surface should match.** The rejection of the visual studio was a direct consequence of that audience decision.

The reasoning was sound given the target audience. Visual studios compete on making authoring accessible to non-technical users; making authoring accessible to non-technical users is not free; the cost takes the form of expressive surface creep in the underlying representation (fields added to support the GUI rather than the runtime), of the GUI becoming canonical and the underlying schema becoming a serialization format, and of rigor eroding under UX pressure. v4's rejection was guarding against exactly this failure mode in the service of a technical-author audience.

What v5 changes is not the accuracy of that warning — it is the scope of the audience the product serves.

---

## 2. What has changed since vision-v4 was written

Four substantive shifts, each of which contributes to why the v4 rejection no longer holds in its original form.

**First, the technical harness is now mature enough to support a visual layer without the visual layer dominating.** When v4 was written, M001–M008 had not shipped. The runtime did not yet have sub-workflows (M004), multi-flow context (M005), MCP tool calls as actions (M006), the security layer (M007), a performance baseline (M008), or the workflow-versioning correctness property (M010). Adding a visual editor on top of an unstable runtime would have meant the visual editor dominating the roadmap; the runtime would have evolved to fit the GUI rather than the other way around. With M001–M010 shipped and M011–M012 in progress, the runtime is a settled foundation. A visual editor added on top of it is a client of the runtime, not a force that reshapes it.

**Second, the four-customer-shape framework makes the author-vs-consumer split explicit in a way v4 did not.** v4 treated the audience as undifferentiated "users who write workflows." The four-shape framework (workflow authors, small teams / indie developers, enterprise self-hosters, hosted-plan customers via Horizon Developer+) separates the technical-author population from the technical-platform-engineer population from the consumer-of-deployed-workflows population. The v4 rejection was about not turning megálos into a product optimized for non-technical authors at the cost of the technical-author experience. The v5 position is additive: serve the existing four shapes uncompromised *and* serve a fifth shape (non-technical authors) through a product surface that does not touch the other four.

**Third, the template-library approach changes the failure mode the rejection was guarding against.** v4's concern was that a visual editor allows arbitrary structure to be constructed by users who do not understand structure. A template library inverts this: non-technical users do not construct structure from scratch; they adapt pre-curated templates. The structure comes from the templates, authored by technical authors using the full authoring surface. The visual editor is not a flow-builder for arbitrary topologies — it is a modification surface over a curated catalog. This is a different product than Landbot's free-form visual builder, and the failure modes are different: Landbot's users can build anything and the rigor erodes under UX pressure on the visual side; template-library users can adapt well-designed patterns within bounded scope, and the rigor lives in the templates themselves.

**Fourth, BYOK plus a consumer-subscription onramp changes the economic relationship.** v4's rejection of the no-code studio layer was partly defensive — competing with Landbot on their terms (per-message pricing, proprietary runtime, locked-in GUI) would have lost. BYOK (v4 §9.4, unchanged in v5) plus the ability to connect a consumer Claude or ChatGPT subscription (new in v5) makes megálos economically different from Landbot at every price point. A non-technical user on the megálos visual studio is not paying per-message or per-workflow — they are bringing their existing LLM access. That is a competitive position v4 did not contemplate because the "connect your consumer subscription" onramp was not a mature pattern in the LLM ecosystem when v4 was written.

None of these four arguments alone would justify the reversal. Together, they describe a landscape different enough from v4's that the original rejection no longer produces the right answer.

---

## 3. What vision-v5 asserts

**megálos serves five customer shapes, each with a distinct product surface, unified by a single deterministic runtime and a single canonical schema.**

The five shapes:

*Shape 1 — Workflow authors.* Technical authors producing YAML workflows against the schema, via CLI tooling, IDE extensions, and the dry-run inspector. Unchanged from v4.

*Shape 2 — Small teams / indie developers.* Technical users building against megálos, onboarded via mikrós (Phase F) and a reference client (Phase G). Unchanged from v4.

*Shape 3 — Enterprise self-hosters.* Technical platform engineers deploying megálos in their own infrastructure, served by Phase H (distribution hardening). Introduced in planning docs post-v4; canonicalized here.

*Shape 4 — Hosted-plan customers via Horizon Developer+.* Users of a future managed megálos offering on Anthropic's Horizon. Architecturally enabled by Phase H, deferred as Phase I. Introduced in planning docs post-v4; canonicalized here.

*Shape 5 — Non-technical authors.* Users adapting pre-curated templates to their specific needs via a visual authoring studio. Served by Phase J (the visual studio + template library + consumer-subscription onramp). **New in v5.**

The runtime and schema are shared across all five shapes. Every shape's authoring surface produces the same artifact — a valid megálos workflow conforming to `megalos_server/schema.py`. There is no Shape-5-specific schema, no Shape-5-specific runtime, no Shape-5-specific validator. What differs across shapes is the authoring experience, not the authored artifact.

---

## 4. What v5 preserves from v4

The following v4 positions carry forward unchanged and are re-asserted here:

**The schema expressiveness ceiling (v4 §5.2, §9.5).** The schema must not become Turing-complete. No debugger, no interpreter, no recursion. Target author remains "a domain expert who knows YAML" for Shape 1. The visual studio does not expand the schema's expressiveness; it surfaces the existing expressiveness through a different authoring interface.

**The LLM-interprets, code-enforces thesis (v4 §2).** The MCP server is the deterministic layer. All structural enforcement — out-of-order rejection, output_schema validation, forward invalidation on revision, session caps, TTL, the `_DO_NOT_RULES` injection — is mechanical. None of this changes.

**BYOK (v4 §9.4).** The platform never pays for LLM inference. Users provide their own API keys. Extended in v5 to include consumer subscription connections (Claude Pro, ChatGPT Plus) as an alternative onramp for Shape 5.

**Provider-agnosticism (v4 §9.1).** No per-provider prompt translation layer. The runtime is LLM-interpreted at the gate, not at the prompt.

**Progressive tool disclosure (v4 §9.2).** The megálos client must not load all tool definitions upfront. Discovery-first architecture.

**The three-dependency runtime (v4 §4.2).** `fastmcp`, `pyyaml`, `jsonschema`. Phase H pluggable backends live in extras packages; the visual studio and template library live in a separate distribution; neither adds a hard runtime dep.

**Rejection of Python escape hatches in YAML (prior strategic reviews).** Still rejected. The visual studio does not introduce arbitrary-code execution paths.

**Rejection of schema-version-compatibility matrices (ADR-001).** The workflow-versioning stance is unchanged. The visual studio produces YAML; YAML edits in a live deployment still produce `workflow_changed` envelopes under the M010 contract.

---

## 5. The six guardrails

The visual studio is permitted *only if* all six of the following mechanical constraints hold at every point in its evolution. Violation of any guardrail is a correctness regression, not a feature decision.

**Guardrail 1 — YAML stays canonical.** The visual studio reads and writes YAML as its source format. Every state the visual studio can produce has a lossless YAML representation. Every valid YAML workflow has a visual representation the studio can display. There are no "visual-only" properties, no hidden state in the GUI, no rendering hints that do not round-trip. If a user edits a workflow in the visual studio, saves, opens it in VS Code with the M012 extension, edits further, and re-opens it in the visual studio, the visual studio sees every change.

**Guardrail 2 — Schema remains the contract.** The visual studio is constrained by `megalos_server/schema.py` in exactly the way the validator is. A manipulation the studio offers must produce valid YAML or must be refused before emission. The schema does not loosen to accommodate GUI convenience. If a visual interaction cannot be expressed in the existing schema, the interaction is removed from the studio, not added to the schema.

**Guardrail 3 — Templates are workflows, not a subtype.** A template is a starter workflow with a name, a description, and a canonical path in the template library. There is no separate "template schema." Users who modify a template produce a regular workflow, indistinguishable from a workflow authored from scratch in any other surface. The template library is a curated catalog of exemplar workflows, not a derived product category.

**Guardrail 4 — The visual studio is a client of the runtime.** The studio uses the same validation path, the same schema, the same error envelopes, and the same loading mechanism as the CLI tools and the IDE extension. Authors using VS Code with the Red Hat YAML extension, authors using the dry-run inspector, and Shape-5 users clicking through the visual studio are all authoring the same artifact type through clients of the same canonical backend. The studio is not a privileged authoring surface with access to hidden APIs.

**Guardrail 5 — Non-technical authoring is bounded by templates.** A Shape-5 user authoring from scratch, with no template as a starting point, is not a supported path. The product promise is "anyone can adapt a curated template to their specific need," not "anyone can build any workflow." Users who want to author from scratch are implicitly Shape 1 or Shape 2 and should use the CLI-based authoring surfaces. The visual studio's entry point is always a template.

**Guardrail 6 — The expressiveness ceiling holds.** v4's rule that the schema must not become Turing-complete applies with equal force in v5. The visual studio cannot add expressiveness the schema does not have. It can only make the existing expressiveness more accessible. If the template library or the visual studio's UX would benefit from a new schema field, the proposal goes through the same schema-change gate as any other proposal: it must be justified by the runtime's needs, not the authoring surface's needs.

If any of these guardrails is relaxed in a future design conversation, it is a reversion toward the failure mode v4 was guarding against and must be treated as such — not as a feature evolution.

---

## 6. The revised key design decisions register

The following decisions from v4's §9 are amended or added. v4 decisions not listed here carry forward unchanged.

**9.5 (amended) — Schema expressiveness ceiling.** The schema must not become Turing-complete. This constraint binds every authoring surface, including the visual studio. The visual studio exposes the existing schema through a different UI; it does not expand the schema. Target author for CLI-based authoring remains "a domain expert who knows YAML." Target user for the visual studio is "a person adapting a curated template," which is a different and strictly bounded role.

**9.6 (amended) — Rasa CALM as design reference, not template.** CALM's patterns are adopted where proven (v4 §9.6, unchanged). CALM's no-code studio was rejected in v4 as incompatible with a technical-author-only audience. v5 builds a visual studio for Shape 5 specifically, *under the six guardrails above*, which produces a materially different product than CALM's studio (template-bounded rather than free-form, same schema as the CLI tools rather than a GUI-specific representation). The rejection of CALM's *specific studio design* holds; the rejection of *any visual studio whatsoever* is lifted.

**9.8 (new) — Customer-shape framework as canonical.** megálos serves five customer shapes (Shape 1 through Shape 5, as specified in §3). Every product decision names which shape or shapes it serves. No product decision serves Shape 5 at the cost of Shapes 1–4; guardrail 5 (templates-only entry) and guardrail 4 (runtime client, not privileged surface) mechanically enforce this.

**9.9 (new) — Template library as first-class artifact.** The template library's quality is the product for Shape 5. A library with fewer than roughly two hundred templates across the core domains is insufficient to serve Shape 5 well. Template authoring is ongoing technical work performed by Shape 1 authors (potentially with mikrós assistance) and curated centrally. Templates are not user-generated content; they are curated exemplars.

**9.10 (new) — Consumer-subscription onramp for Shape 5.** BYOK (9.4) remains the technical default. For Shape 5, the authoring surface additionally supports "connect your Claude account" and "connect your ChatGPT account" onramps that route inference through the user's existing consumer LLM subscription. The runtime never sees billing information; the onramp is an authorization layer in the Shape-5 client surface only.

**9.11 (new) — Visual studio produces YAML, not an intermediate representation.** The output of any Shape-5 authoring session is a YAML workflow identical in shape to a CLI-authored workflow. There is no "visual workflow format" alongside the YAML. This is guardrail 1 expressed as a design decision for register completeness.

---

## 7. Relationship to the roadmap

The roadmap as understood before v5 consisted of M001–M012 (runtime and authoring DX, in progress), Phase F (mikrós, deferred), Phase G (client layer, deferred), and Phase H (distribution hardening, deferred). v5 inserts Phase J and renames Phase I.

**Phases unchanged by v5:** M001–M012, Phase F, Phase G, Phase H. Each continues to serve its existing target shape (Shapes 1–3). Phase H remains focused exclusively on Shape 3; its scope is not expanded by v5.

**Phase I (was Horizon Developer+, now remains).** The managed hosting tier that serves Shape 4. Sequenced after Phase H as before.

**Phase J (new) — Visual studio, template library, consumer-subscription onramp.** Serves Shape 5. The roadmap document for Phase J is a separate planning artifact and is not in scope for this vision document. Phase J is sequenced after Phase I by default; alternative sequencing (Phase J before Phase I, or Phase J in parallel with Phase I) is a future strategic question not resolved here.

The Phase H roadmap document dated 2026-04-22 is unaffected by v5 and remains canonical for Phase H. A companion Phase J roadmap document will be drafted separately.

---

## 8. Out of scope for v5

These are strategic questions v5 does not answer and that belong in later documents:

- The detailed scope of Phase J. This vision document asserts Phase J exists and names its guardrails; it does not scope its milestones. The Phase J roadmap is a separate artifact.
- The specific visual-studio UX, component taxonomy, or visual language. Design work, not strategic work.
- The specific template library taxonomy (how many domains, which domains, how templates are organized). Curation work, not strategic work.
- Phase J's position relative to Phase I in the sequence. Strategic question, but one best deferred until Phase H is closer to complete and the state of the ecosystem has more signal.
- Whether Phase J should be a distinct product surface (a separate web application) or integrated into the Phase G client. Product architecture question, resolved in the Phase J roadmap.
- The pricing model for Phase J beyond the BYOK + consumer-subscription-onramp commitment. If a managed hosting component is needed to support Shape 5 (e.g., template library hosting), that is a Phase I or Phase J scoping question.

---

## 9. Open questions

The following should be resolved during Phase J's `/discuss` gate, not preemptively:

1. **Consumer-subscription onramp feasibility.** Does Anthropic's consumer Claude API surface an OAuth flow that allows a third-party application to route inference through a user's subscription? Does OpenAI's equivalent exist? Both are assumed possible in v5; if neither is, Phase J's Shape-5 onramp may need to revert to API-key-only, and the strategic claim about serving non-technical users is weakened accordingly.
2. **Template library size floor.** §6's decision 9.9 names "roughly two hundred templates" as the quality floor. This is a rough estimate. The actual floor depends on the domain distribution and template quality. Revisit during Phase J's curation work.
3. **Shape-5's relationship to Phase F's mikrós.** A Shape-5 user adapting a template with AI assistance is a natural mikrós use case, but the mikrós skills as scoped are for AI coding agents authoring from scratch. Whether Shape-5 needs its own mikrós skills or can reuse Phase F's with UI adaptation is a Phase J question.
4. **Template authoring as a labeled activity.** If Shape 1 authors are expected to produce templates as a distinct deliverable, the authoring process may need specific tooling (template metadata, library indexing, review workflow). Scoped in Phase J.

---

## 10. Summary

Vision-v5 asserts that megálos serves five customer shapes — four preserved from prior planning plus a new Shape 5 (non-technical authors adapting curated templates via a visual studio). The addition of Shape 5 reverses vision-v4's rejection of a no-code visual studio, which was load-bearing for the technical-author-only audience v4 targeted.

The reversal is justified by four shifts since v4 was written: runtime maturity makes the visual studio additive rather than dominant; the four-shape framework separates audiences cleanly; the template-library approach bounds the failure mode v4 was guarding against; and the BYOK-plus-consumer-subscription onramp makes megálos economically different from Landbot at every price point.

The reversal is kept honest by six mechanical guardrails: YAML stays canonical, the schema is the contract, templates are workflows not a subtype, the studio is a runtime client not a privileged surface, non-technical authoring is bounded by templates, and the expressiveness ceiling holds. Violation of any guardrail is a correctness regression.

The technical thesis — LLM interprets, code enforces, schema stays simple, rigor is non-negotiable — is preserved in full. The runtime does not change. What changes is who megálos reaches, and the mechanism by which the new audience reaches the existing runtime.
