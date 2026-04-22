# Phase J — Visual Studio, Template Library, Consumer Onramp (Strategic Scaffold)

**Status:** Pre-scoping strategic scaffold. **Not a roadmap.** Reserves strategic space for Phase J work, pins the invariants from vision-v5 that bind its eventual shape, and enumerates the open questions that must be resolved before milestone-level scoping becomes defensible. A proper Phase J roadmap in the shape of the Phase H document is a future artifact, written when Phase H is closer to shipping and the open questions in §7 have early answers.
**Author:** Diego Marono (with strategic review)
**Date:** 2026-04-22
**Governing vision:** [`2026-04-22-megalos-vision-v5.md`](./2026-04-22-megalos-vision-v5.md). Vision-v5 §3 introduces Shape 5 and commits megálos to serving it; this document is the first downstream consequence of that commitment.
**Shape served:** Shape 5 (non-technical authors adapting curated templates) exclusively.

---

## 1. Why this document is a scaffold rather than a roadmap

Phase H's roadmap could be concrete because MH1–MH6 map onto known architectural changes against a mature runtime. The work is large but the shape is legible: install an interface, implement an adapter, pass a conformance suite. The failure modes are known. The LOC estimates are defensible because the code surface being touched already exists and has tests.

Phase J has none of that grounding today. The visual studio's UX is undesigned. The template library's taxonomy is unchosen. The consumer-subscription onramp's technical feasibility depends on facts about external APIs (Anthropic's consumer API surface, OpenAI's equivalent) that vision-v5 §9 explicitly flagged as open. The client architecture — whether Phase J is a separate web application or integrated into Phase G's reference client — is itself a strategic question the vision doc did not resolve.

Drafting Phase J as a milestone-structured roadmap with slice breakdowns, LOC budgets, and success criteria would require inventing most of those specifics. The result would read as authoritative while being mostly speculation. When Phase H is closer to shipping and the open questions have answers, a proper roadmap becomes possible — and it will be better for having this scaffold as its foundation than if we force specifics now and rewrite them later.

The scaffold's job is to reserve the strategic space and make future scoping cheaper. Specifically: pin what vision-v5's guardrails imply for Phase J's shape, name the irreducible components, enumerate the open questions in the order they should be answered, and document the cost/value calculation that justifies Phase J's position in the roadmap. Nothing here commits code; everything here constrains what the eventual roadmap can look like.

---

## 2. The five irreducible components of Phase J

Regardless of how Phase J is eventually scoped, the following five components are irreducible — Phase J cannot serve Shape 5 without each of them existing in some form. They are listed here not as milestones but as the components any milestone structure will end up organizing.

**The template library.** A curated catalog of exemplar workflows across the domains megálos serves (writing, analysis, professional, and whatever additional domains are added between now and Phase J). Each template is a complete, valid YAML workflow with metadata describing what it does, who it serves, and how to adapt it. The library is the product for Shape 5 — its quality is the ceiling of what Shape 5 can achieve. Vision-v5 §9.9 names a rough floor of two hundred templates across core domains; that number is approximate and revisitable. The templates are authored by Shape 1 authors (potentially with mikrós assistance from Phase F) and curated centrally; they are not user-generated content.

**The visual studio.** A web-based authoring surface that loads a template as its starting point, lets the user modify it through direct manipulation, and writes the resulting YAML back to storage. Under vision-v5 guardrail 1, the studio reads and writes YAML as its source format with lossless round-trip; under guardrail 2, it is constrained by the canonical schema exactly as the validator is; under guardrail 5, it does not support from-scratch authoring — every session begins with a template. The studio is a client of the runtime (guardrail 4), not a privileged authoring surface.

**The consumer-subscription onramp.** An authorization layer that lets a Shape-5 user connect their existing consumer LLM subscription (Claude Pro, ChatGPT Plus) rather than paste an API key. Under vision-v5 §9.10, BYOK via API key remains supported as the technical default; the consumer-subscription flow is the Shape-5-friendly alternative. The runtime never sees billing information; the onramp is an authorization layer in the Phase J client surface only. Feasibility depends on external OAuth flows that may or may not exist — this is the single highest-uncertainty component of Phase J.

**The deployment target for authored workflows.** When a Shape-5 user saves a workflow in the studio, it has to live somewhere. The three plausible targets are: a megálos server the user deploys themselves (requires technical capability — contradicts Shape 5's premise), a hosted tier that Phase I provides (depends on Phase I having shipped), or a Phase-J-specific hosted surface that is architecturally lighter than Phase I's full managed-tier offering. This is a design question with product consequences, not an implementation detail.

**The discovery surface.** A public-facing catalog on megálos's website that surfaces the template library, shows what each template does, and provides a one-click path into the visual studio to begin adapting it. This is distinct from the template library itself — the library is the content; the discovery surface is how potential Shape-5 users find it. The discovery surface also serves as a natural home for the curated catalog of public domain servers (per the strategic conversation predating v5), which is an additive Shape-2 benefit that should live on the same infrastructure.

Each of these five components is a real problem, and each has substantive design surface. The eventual Phase J roadmap will either map one component per milestone (five milestones) or group them by shared architecture (fewer, larger milestones). That structural decision cannot be made today — it depends on early prototyping work that establishes which components share infrastructure and which are genuinely independent.

---

## 3. What vision-v5's guardrails constrain about Phase J's shape

The six guardrails in vision-v5 §5 are constraints that bind any implementation of Phase J. Restated here in Phase J context, because the eventual roadmap must treat each as non-negotiable.

**Guardrail 1 — YAML stays canonical.** The visual studio reads and writes YAML. There is no "visual workflow format" alongside the YAML. Every state the studio can produce has a lossless YAML representation; every valid YAML workflow has a visual representation the studio can display. A user who edits in the studio, saves, opens the file in VS Code with the M012 extension, and re-opens it in the studio sees every change. This forecloses any design that relies on a separate visual representation with custom round-trip logic — the studio's internal state is a YAML document being manipulated, not a proprietary graph that is serialized on save.

**Guardrail 2 — Schema remains the contract.** The studio is constrained by `megalos_server/schema.py` in exactly the way the validator is. If a visual manipulation would produce invalid YAML, the studio refuses it before emission. The schema does not loosen to accommodate UI convenience. Practical consequence: the studio's design cannot assume schema changes. If the UX would benefit from a new field, the proposal goes through the same schema-change gate as any other proposal — justified by the runtime's needs, not the authoring surface's.

**Guardrail 3 — Templates are workflows, not a subtype.** A template is a starter workflow with a name, a description, and a canonical path in the library. There is no separate "template schema." The template library is a curated collection of exemplar workflows; it is not a derived product category with its own semantics. Practical consequence: template authoring is ordinary workflow authoring with curation metadata attached. The same tooling Shape 1 uses for workflow authoring (CLI validator, dry-run inspector, IDE extension) is the tooling for template authoring.

**Guardrail 4 — The visual studio is a client of the runtime.** The studio uses the same validation path, the same error envelopes, and the same loading mechanism as every other client. There is no privileged studio-only API. Practical consequence: Phase J does not build a second backend. Whatever megálos-server surface the CLI and IDE clients use is the same surface the studio uses. If the studio needs a new capability, that capability becomes available to every client.

**Guardrail 5 — Non-technical authoring is bounded by templates.** A Shape-5 user without a template as a starting point is not a supported path. The product promise is "adapt a curated template to your specific need," not "build any workflow from scratch." Practical consequence: the studio's entry point is always a template selection. There is no blank-canvas mode. A user who wants from-scratch authoring is implicitly Shape 1 or Shape 2 and should use the CLI-based authoring surfaces.

**Guardrail 6 — The expressiveness ceiling holds.** The studio cannot add expressiveness the schema does not have. It can only make the existing expressiveness more accessible. Practical consequence: the studio's feature set is strictly a subset of the schema's feature set. If the schema supports it, the studio can surface it. If the schema does not, the studio cannot invent it.

These constraints narrow Phase J's design space considerably. Many design choices that would be natural in a free-form visual authoring tool (custom workflow representations, UI-only fields, schema extensions for GUI convenience, blank-canvas authoring, GUI-specific authoring shortcuts) are unavailable to Phase J by construction. The guardrails exist because vision-v5 §2 argued that without them, Phase J collapses into the failure mode vision-v4 was guarding against — visual layer dominates, rigor erodes, schema becomes a serialization format.

---

## 4. The cost/value calculation

Phase J is a substantial investment. Before committing to it in any concrete roadmap, the cost/value calculation has to be defensible. The scaffold version is below; the proper roadmap will re-examine it with whatever new information Phase H's completion and any early Phase J prototyping produce.

**Cost side.** At sole-author pace, Phase J is plausibly comparable in scope to Phase H — six to twelve months of work. Specifically, the five irreducible components each require non-trivial implementation: the template library requires authoring two hundred templates (assuming vision-v5 §9.9's estimate holds), which is ongoing content work; the visual studio requires front-end engineering skills and tooling that megálos has not yet invested in; the consumer-subscription onramp requires OAuth integration against at least one external provider, with the feasibility gated on external API surface; the deployment target introduces a new operational dimension; the discovery surface requires web infrastructure that megálos does not currently have. None of these costs are individually prohibitive; collectively, they are substantial.

**Value side.** Shape 5 is the largest potential audience by headcount. Non-technical users who want structured conversational workflows are a market that competitors (Landbot, Voiceflow, Typebot) have validated. The BYOK-plus-consumer-subscription economic model is differentiated against those competitors at every price point. A curated template library with two hundred exemplars across domains is a product shape that no direct competitor offers — most free-form visual builders leave users to construct workflows from scratch, with predictable quality outcomes. The megálos-specific value proposition — rigor preserved through templates, constraint enforced through the schema, provider-agnosticism preserved through BYOK — is distinguishable from what existing tools offer.

**Risk side.** Three risks are worth naming at the scaffold level. First, the OAuth feasibility question (§7 open question 1): if neither Anthropic's consumer Claude API nor OpenAI's equivalent surfaces a workable OAuth flow for third-party inference routing, the consumer-subscription onramp reverts to API-key-only and the Shape-5 value proposition weakens materially. Second, the template library's quality floor (§7 open question 2): if two hundred well-crafted templates turns out to be insufficient to cover the domains Shape 5 users want, the library becomes a permanent ongoing curation cost rather than a one-time build. Third, the guardrail-erosion risk: every visual authoring tool in the ecosystem's history has faced UX pressure to relax schema constraints; vision-v5's guardrails exist to prevent this, but the discipline to enforce them during implementation is itself a non-trivial commitment.

**Cost/value summary.** On the current information, Phase J is a defensible investment — the audience is large, the differentiation is real, the guardrails preserve the rigor that makes megálos worth using. But the investment is large enough that sequencing it correctly matters more than sequencing it fast. The scaffold's position: do not begin Phase J implementation work until Phase H is shipped (Shape 3 serves as the first "real infrastructure" proof) and until the OAuth feasibility question has early signal. Doing Phase J before those preconditions risks building the wrong thing, or building the right thing on infrastructure that is not ready to support it.

---

## 5. Sequencing

Phase J has no hard architectural dependency on Phase F, Phase G, Phase H, or Phase I. Each phase's work is orthogonal to Phase J's. However, there are soft sequencing preferences worth naming.

**Phase J after Phase H (strong preference).** Phase H's MH2–MH4 establish the pluggable-backend discipline that Phase J's deployment target component will benefit from. If Phase J ships before Phase H, its deployment target has to be built on assumptions that MH2–MH4 would otherwise refine; the result is likely rework. Shipping Phase H first gives Phase J's architecture a mature foundation to sit on.

**Phase J after Phase I (default, revisitable).** Phase I (Horizon Developer+, Shape 4) adds a managed-hosting tier. One of Phase J's design questions is where authored workflows are deployed, and Phase I's managed tier is a plausible answer. If Phase I ships first, Phase J can consume it; if Phase J ships first, it has to build its own deployment target, which may later duplicate Phase I's work. The default sequence I-then-J reflects this; the alternative J-then-I is defensible if Phase I's economics are ambiguous and a lighter Phase-J-specific deployment target is preferred.

**Phase J in parallel with Phase I (acceptable variant).** If adoption signal for Shape 5 is strong and Phase I's economics are uncertain, Phase J can proceed in parallel with Phase I rather than waiting. The risk is that Phase J's deployment target component and Phase I's managed tier end up duplicating work; the mitigation is to scope Phase J's deployment target narrowly to what Shape 5 specifically needs, deferring any general-purpose hosted offering to Phase I.

**Phase J before Phase H (rejected).** Building Shape 5 before Shape 3 is served is defensible only if Shape 5 adoption signal dramatically outweighs Shape 3 signal and Phase J can be built entirely on local-run infrastructure. Neither condition is likely to hold; rejected as the default.

---

## 6. Out of scope for this scaffold (and for the eventual Phase J roadmap)

The scaffold is explicit about what Phase J does not do. The following are adjacent concerns that surface in conversations about Shape 5 but belong elsewhere.

- **A second runtime, a second schema, or GUI-specific schema extensions.** Vision-v5 guardrail 2 and guardrail 6. Phase J consumes the canonical schema; it does not extend it.
- **From-scratch authoring for non-technical users.** Vision-v5 guardrail 5. Templates are the entry point.
- **User-contributed templates / marketplace mechanics.** The library is curated, not user-generated. Per prior strategic review, marketplace mechanics are indefinitely deferred.
- **Implementing OIDC, SSO, or consumer-LLM-provider APIs inside megálos itself.** The consumer-subscription onramp consumes OAuth flows that Anthropic and OpenAI provide externally. megálos does not become an identity provider.
- **Per-message pricing, token metering, or paid-tier infrastructure beyond BYOK and consumer-subscription.** Phase J preserves megálos's BYOK-only economic model (vision-v5 §9.4, unchanged).
- **Mobile-native applications.** Phase J is web-first. A mobile client is a future question, not a Phase J commitment.
- **Collaborative editing, presence indicators, multi-user template modification.** Single-author workflow editing. Multi-user collaboration is out of scope and likely a Phase K or later concern if ever.
- **AI-generated templates at scale.** The library is hand-curated. An AI-assisted template authoring tool (potentially via mikrós) is consistent with vision-v5's Version-A interpretation of mikrós (§7 open question 3), but the resulting templates still require human curation before entering the library.

---

## 7. Open questions (in priority order)

These questions bind what Phase J can become. Each is annotated with its downstream consequence — what changes in Phase J's eventual shape depending on how the question resolves.

**1. OAuth feasibility for consumer-subscription onramp.** Does Anthropic's consumer Claude API surface an OAuth flow that allows a third-party application to route inference through a user's subscription? Does OpenAI's equivalent exist? *If yes (either provider):* the consumer-subscription onramp is viable as specified; Shape 5's economic differentiation holds. *If no (both providers):* the onramp reverts to API-key-only, and Shape 5's "bring your consumer account" promise is broken. Phase J is still buildable without the consumer-subscription onramp, but the value proposition against Landbot weakens — API-key-paste is technical friction that non-technical users may not tolerate. **Highest-priority open question; answer before Phase J scoping proceeds.**

**2. Template library size, taxonomy, and curation load.** Two hundred templates is a rough estimate from vision-v5 §9.9. What the actual floor looks like depends on the domain distribution (how many domains, how many templates per domain, how deep within each domain), the target audiences (small business, creative, analytical, educational), and the per-template quality bar. *If the floor is accurate:* template authoring is a one-time-plus-maintenance investment, parallelizable with the rest of Phase J. *If the floor is substantially higher:* template authoring becomes a permanent ongoing cost, and Phase J's feasibility calculation changes. An early prototyping pass — author ten templates across two domains, measure authoring time per template, estimate what two hundred would cost — is the cheapest way to de-risk this.

**3. Phase J client architecture.** Separate web application, or integrated into Phase G's reference client? *Separate app:* cleaner Shape-5 experience, no leakage of Shape-2 technical surface, but duplicates infrastructure Phase G already builds. *Integrated:* reuses Phase G's client layer, but forces Shape 5 users to encounter a UI designed primarily for Shape 2. My weak preference is separate, on the grounds that Shape-5 UX should not be constrained by Shape-2 defaults; but Phase G's design may produce natural answers that override this. Resolve after Phase G is closer to shipping.

**4. Deployment target for authored workflows.** Where does a workflow saved in the visual studio live? Options: user-deployed megálos server (rejected — contradicts Shape 5 premise), Phase I's managed tier (depends on Phase I existing), or a Phase-J-specific hosted surface. *If Phase I exists:* the managed tier is the natural answer and Phase J's deployment component is thin. *If Phase I is delayed or rejected:* Phase J must build its own lighter-weight deployment target. The answer shapes what "a Phase J deployment component" actually means; resolve at Phase I's design gate.

**5. mikrós's role in Phase J.** A Shape-5 user adapting a template with AI assistance is a natural mikrós use case, but mikrós's skills as currently scoped target AI coding agents authoring from scratch (Version A of the mikrós debate, per vision-v5 §7 open questions). *If mikrós skills are reusable for Shape 5 with UI adaptation:* Phase J builds on Phase F's existing investment. *If Shape 5 needs its own mikrós-equivalent skills:* additional scope. Resolve after Phase F ships; expected answer is "mostly reusable with adaptation."

**6. Template authoring as a labeled activity.** If Shape 1 authors produce templates as a distinct deliverable, template authoring may need specific tooling — metadata conventions, library indexing, review workflow, curation process. The scaffold-level answer is "treat templates as workflows with curation metadata" (vision-v5 guardrail 3). The detailed answer requires early template authoring work to surface what curation metadata actually needs to look like.

**7. Visual studio scope boundary.** Phase J's studio surfaces the schema's existing expressiveness through a direct-manipulation interface. But the schema is large — steps, gates, branches, preconditions, sub-workflow calls, MCP tool calls, output schemas, intermediate artifacts, directives, `inject_context`, `call_context_from`. A minimum-viable studio that exposes only the most-commonly-used fields ships faster; a comprehensive studio that exposes everything is a much larger project. *If MVP:* faster to ship, but users hit ceilings when they want to use features the studio doesn't expose. *If comprehensive:* larger investment, but no artificial ceilings. The right answer is almost certainly MVP-plus-progressive-expansion, but defining the MVP scope is itself a non-trivial design question.

---

## 8. What this scaffold commits to

The scaffold commits megálos strategically to Phase J's existence and shape, under vision-v5's five-shape framework. It does not commit to a timeline, a milestone structure, a LOC budget, or specific implementation choices. Those belong to the proper roadmap, drafted when the open questions in §7 have early answers.

Concretely, the scaffold's commitments are:

- Phase J exists and serves Shape 5 as described in vision-v5 §3.
- Phase J comprises five irreducible components (§2): template library, visual studio, consumer-subscription onramp, deployment target, discovery surface.
- Phase J's eventual design honors all six vision-v5 guardrails (§3).
- Phase J's sequencing default is "after Phase H, after Phase I" with documented acceptable variants (§5).
- The open questions in §7 are resolved in roughly the stated priority order before the proper Phase J roadmap is drafted.

A scaffold is a promise about what the building will look like, not a commitment to start pouring concrete. When Phase H is closer to shipping and the open questions — particularly the OAuth feasibility question — have early answers, the proper Phase J roadmap can be written in the shape of the Phase H document. Until then, this scaffold reserves the space and constrains what can be built in it.

---

## 9. Summary

Phase J serves Shape 5 (non-technical authors adapting curated templates via a visual studio) as introduced by vision-v5. Its five irreducible components are: the template library, the visual studio, the consumer-subscription onramp, the deployment target, and the discovery surface. All five are constrained by vision-v5's six guardrails, which narrow Phase J's design space to preserve the rigor that makes megálos distinct from Landbot/Voiceflow/Typebot.

The proper Phase J roadmap is deferred until Phase H is shipped and the seven open questions in §7 have early answers — with priority given to the OAuth feasibility question, which is the single highest-uncertainty component of Phase J and materially affects the Shape-5 value proposition if it resolves negatively.

This scaffold's purpose is to reserve strategic space and constrain what Phase J can eventually become, not to direct near-term work. At sole-author pace, Phase J is plausibly six to twelve months; combined with Phase H and Phase I, the full remaining megálos roadmap beyond current in-flight work sits in the eighteen-to-twenty-four-month range. Phase J is not the first thing to build next. It is the last of the three large phases, and its timing depends on information the scaffold's authors don't yet have.

Upon Phase J completion, megálos serves all five customer shapes to foundation-complete standard. That is the endpoint the scaffold points at; the path to it is not yet drawn.
