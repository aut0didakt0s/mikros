# Phase H — Distribution Hardening Roadmap (MH1–MH6)

**Status:** Planning draft. Canonical for Phase H; supersedes the 2026-04-22 Phase H draft written under vision-v4.
**Author:** Diego Marono (with strategic review)
**Date:** 2026-04-22 (rewrite)
**Governing vision:** [`2026-04-22-megalos-vision-v5.md`](./2026-04-22-megalos-vision-v5.md). All decisions in this roadmap defer to v5 where they conflict.
**Shape served:** Shape 3 (enterprise self-hosters) exclusively. Shapes 1, 2, 4, and 5 are explicitly out of scope.

---

## 1. Context

The runtime is feature-complete against CALM parity after M001–M008. M010 (ADR-001) installed the workflow-versioning correctness property that makes the runtime safe under persistent state. M011 and M012 are in progress, closing the authoring-DX gap. Phase F (mikrós) gives technical users an AI-assisted onramp to the authoring surface; Phase G (reference client) gives them a way to consume deployed workflows. Phase J (vision-v5 §3, Shape 5) will later add a visual studio, template library, and consumer-subscription onramp for non-technical authors.

None of the above serves Shape 3 — enterprises that need to self-host megálos in their own infrastructure, at multi-replica scale, with operational properties that match what their platform teams already expect from production services. Phase H fills this gap and only this gap.

The current runtime makes four local-only architectural assumptions that are correct for Shape 2 and below but incorrect for Shape 3:

The session store is per-process SQLite. Two replicas behind a load balancer cannot share sessions; a session started on replica A is invisible to replica B. The rate-limiter's token buckets live in a Python dict on the process, so each replica has its own independent limiter — a user bounced between replicas can exceed the stated per-session rate. The workflow registry is a filesystem glob at `create_app()` time, so multi-replica deployments either need a shared filesystem (operationally painful) or a coordinated deploy that guarantees identical YAML across replicas simultaneously (operationally fragile). Auth is structurally `ANONYMOUS_IDENTITY` on every session; the capability-token security model works for single-user deployments, but enterprise needs a hook to populate real user identity from an upstream auth source.

These are not bugs. They are defensible v0.x choices that match the current deployment shape. `docs/rate-limits.md` already flags the shared-state question explicitly: *"Horizontal scaling or shared rate limits across replicas need out-of-process state, which would change the sync-consume atomicity story — revisit at that point, not before."* Phase H is the point where that IOU comes due.

Two constitutional constraints carry forward from prior phases and bind Phase H strictly. **The three-dependency rule.** The default install remains `fastmcp`, `pyyaml`, `jsonschema` and nothing more. Phase H's backend adapters live in extras packages (`megalos-server[postgres]`, `megalos-server[redis]`) or separate distributions. A self-hoster who does not use the pluggable backends pays no cost in dependency weight. **The schema-stability rule.** Phase H is backend-side work; workflow YAML authors are entirely unaffected. No new YAML fields, no weakened constraints, no new validator rules. If a Phase H milestone wants to change the schema, the design is wrong. This is vision-v5's guardrail 6 (expressiveness ceiling) applied to backend work.

---

## 2. Decision: six separate milestones, sequenced smallest-to-largest

Each architectural change is its own milestone. The alternative — one combined "distribution hardening" milestone — was rejected on three grounds. Scope mismatch: MH1 is configuration and documentation with an integration-test surface, while MH2 touches the state layer every tool funnels through. Independent shippability: MH1 delivers value to enterprise evaluators on its own, MH5 delivers identity propagation on its own, and bundling either with the architectural middle (MH2, MH3, MH4) delays cheap wins behind heavy work. Cancellation surface: if Shape 3 adoption signal is slower to materialize than expected, individual milestones can be deferred; a combined milestone is harder to suspend partway.

The sequencing MH1 → MH2 → MH3 → MH4 → MH5, with MH6 landing continuously alongside MH2–MH5, follows the same ascending-scope-and-descending-immediacy criterion used in the M001–M008 program and the post-M008 authoring-DX roadmap. An acceptable variant is documented in §10.

---

## 3. MH1 — Deployment Recipes

### 3.1 Goal

Give an enterprise evaluator a canonical set of deployment configurations they can try without architecting from scratch. Cover the three realistic deployment shapes — Docker, Docker Compose, Kubernetes — with working, tested, documented recipes that a platform engineer can adapt to their specific environment.

### 3.2 Scope

In scope: audit and promote the existing Dockerfile in the repo, documenting build and run; add `deploy/docker-compose.yml` for local prod-like deployment with server, health check, and volume mount for SQLite; add `deploy/k8s/` with minimal manifests (Deployment, Service, ConfigMap, PersistentVolumeClaim) that bring up a single-replica megálos-server on a Kubernetes cluster; add `deploy/helm/megalos-server/` as a Helm chart starter covering the same surface; write `docs/DEPLOYMENT.md` walking through each path with "how to verify it works" steps; build an integration-test harness under `scripts/deploy-test/` that spins up each recipe against a real target (Docker daemon, kind or minikube, local Docker Compose) and exercises a canonical workflow end-to-end. Also in scope: an envvar audit that consolidates `MEGALOS_*` envvars into a single reference in `docs/CONFIGURATION.md`, surfacing any that are currently implicit or undocumented; a `/health` endpoint returning 200 on a responsive server and 503 otherwise (if one does not already exist); verification that `SIGTERM` handling drains in-flight tool calls before the process exits, with the shutdown budget documented.

Out of scope: multi-replica recipes (those are unbuildable until MH2 lands, and MH1 is the single-replica story); deployment to specific cloud providers as named targets (ECS, Cloud Run, AKS, EKS) — the recipes are generic and cloud-specific guidance is community-contributed documentation; auto-scaling policies, even for single-replica (Phase I work); any managed-hosting offering of any kind (Phase I for Shape 4; this document is Shape 3).

### 3.3 Slices

- **S01 — Docker and Compose.** Dockerfile audit, Compose file, scripts to spin-test both locally. LOC budget: ~150 (mostly config and test scripts).
- **S02 — Kubernetes manifests.** Minimal K8s manifests plus a kind or minikube spin-test script. LOC budget: ~200.
- **S03 — Helm chart starter.** Basic Helm chart wrapping S02's manifests. LOC budget: ~200.
- **S04 — Documentation and envvar consolidation.** `docs/DEPLOYMENT.md`, `docs/CONFIGURATION.md`, health-check endpoint, graceful-shutdown verification. LOC budget: ~150 (mostly docs and tests).

### 3.4 Success criteria

An evaluator can clone the repo, run `docker compose up`, and reach a working megálos-server on localhost within five minutes. An evaluator can apply the K8s manifests against kind and reach a working server within ten minutes. An evaluator can `helm install` the starter chart and reach a working server within ten minutes. Every envvar referenced anywhere in the codebase appears in `docs/CONFIGURATION.md` with a description and default. `SIGTERM` produces a clean shutdown with no in-flight tool call left in an inconsistent state, verified by a test. Integration-test scripts run in CI (nightly, advisory) against at least the Docker and Compose paths.

### 3.5 Risk

Low-medium. The work is mostly configuration and documentation, but the integration-test surface is novel — CI has not previously exercised real deployment targets. Kind and minikube in CI are known-flaky; the mitigation is advisory-only runs rather than required checks, matching the existing `mcp-smoke` pattern documented in `megalos_server/MCP.md`.

---

## 4. MH2 — Pluggable Persistence Backend

### 4.1 Goal

Define a `SessionStore` interface satisfied by the current SQLite implementation; implement a Postgres adapter. Preserve every existing correctness property: the M010 fingerprint column, the sub-workflow stack semantics (parent-owned guard, call-frame and digression-frame invariants), the rate-limiter's atomicity requirements that remain in-process until MH3, and the ordering guarantees the concurrency test suite pins.

### 4.2 Scope

In scope: a `megalos_server/store/` package defining `SessionStore` as an abstract base (or Protocol) covering the surface `megalos_server/state.py` currently exposes; refactor of the existing SQLite implementation into `megalos_server/store/sqlite.py` satisfying the interface, with default behaviour unchanged; a new Postgres adapter at `megalos_server/store/postgres.py`, installed via `megalos-server[postgres]` extras, connected via `MEGALOS_STORE_URL=postgres://...`; backend selection via `MEGALOS_STORE_BACKEND=sqlite|postgres` with `sqlite` as default; a Postgres migration CLI (`megalos-server migrate` or `python -m megalos_server.migrate`) that creates the required tables against a fresh database as an explicit operator step — no automatic migrations on startup; full test-suite parity against both backends, with the existing test suite becoming the conformance suite every adapter must pass; Postgres tests running in CI against a `postgres:16` service container; an ADR documenting the interface surface, the transaction semantics each backend must satisfy, and the explicit non-goal of cross-backend session migration.

Out of scope: session migration from SQLite to Postgres in either direction (a self-hoster adopting Phase H starts fresh; existing SQLite sessions stay on SQLite, and cross-backend migration is an operator-script problem, not a runtime feature); read replicas, connection-pool tuning, query-level performance optimization (correctness first, performance later — the M008 baseline re-run is about catching regressions, not about optimizing); other backends such as MySQL, DynamoDB, Cosmos (the three-strikes rule applies — do not abstract beyond two concrete implementations); cross-region replication, geo-distributed deployments (single-region multi-replica is the Phase H ceiling).

### 4.3 Slices

- **S01 — Interface extraction.** Define `SessionStore`; refactor the existing SQLite state layer into an implementation behind it. No behaviour change; verified by the existing test suite passing without modification. LOC budget: ~400 (mostly refactor).
- **S02 — Conformance test suite.** Parameterize the existing state-layer tests across both SQLite and a stubbed second-backend placeholder, confirming the interface boundary is correct before the Postgres adapter exists. LOC budget: ~200.
- **S03 — Postgres adapter minimum viable.** Implement against the conformance suite. Pass the full existing test matrix. LOC budget: ~600.
- **S04 — Migration CLI and operator UX.** `megalos-server migrate` command, connection-string documentation, error messages for common setup failures. LOC budget: ~200.
- **S05 — Concurrency stress against Postgres.** Re-run the concurrency test suite (`test_concurrency.py`, `test_session_stack_push.py` et al.) against Postgres with higher thread counts. Pin the atomicity properties the SQLite implementation established. LOC budget: ~150.

### 4.4 Success criteria

The existing test suite passes identically against both backends. The concurrency test suite's invariants hold against Postgres at 4× the thread count it currently runs at against SQLite. The M010 `workflow_changed` envelope fires correctly across both backends. The sub-workflow stack tests (`test_stack_observability.py`, `test_sub_workflow_spawn.py`, `test_sub_workflow_propagation.py`) pass against Postgres. A `docs/CONFIGURATION.md` entry describes `MEGALOS_STORE_BACKEND` and `MEGALOS_STORE_URL` with connection examples. The three-dep constitutional rule holds for the default install; Postgres dependencies live in extras.

### 4.5 Risk

High. This is the heaviest milestone in Phase H and comparable in scope to M001 (runtime hardening) from the original program. Three named risk sources. **Atomicity-model mismatch.** SQLite's `BEGIN IMMEDIATE` and Postgres's `SERIALIZABLE` are not interchangeable semantics. The concurrency properties the current test suite pins — particularly the `session_stack_full` race that the two-threads-push-at-depth-2 test exercises — must translate cleanly. The conformance suite in S02 exists to catch divergence before the adapter lands. **Schema drift between backends.** If the SQLite schema and the Postgres schema diverge cosmetically (index definitions, column defaults, constraint names), debugging becomes painful. Mitigation: a single canonical schema module that emits backend-specific DDL from the same Python source, so the two implementations cannot silently drift. **Performance regression masquerading as correctness.** Postgres round-trips are dramatically more expensive than SQLite local calls. The concurrency suite may pass while wall-clock latency degrades significantly. Re-run the M008 performance baseline against Postgres as part of S05 and document the delta honestly in `docs/PERFORMANCE.md`.

---

## 5. MH3 — Pluggable Rate-Limiter Backend

### 5.1 Goal

Define a `RateLimiter` interface satisfied by the current in-process implementation; implement a Redis-backed adapter (or Postgres-backed — see §12 open questions). Consistent rate-limiting across replicas, preserving the sync-consume atomicity that is the current implementation's load-bearing invariant.

### 5.2 Scope

In scope: a `megalos_server/ratelimit/` package defining `RateLimiter` as an abstract base; refactor of the existing in-process implementation into `megalos_server/ratelimit/memory.py` satisfying the interface; a new adapter at `megalos_server/ratelimit/redis.py` (pending the §12 backend choice), installed via `megalos-server[redis]` extras, connected via `MEGALOS_RATELIMIT_BACKEND_URL=redis://...`; backend selection via `MEGALOS_RATELIMIT_BACKEND=memory|redis` with `memory` as default; a conformance test suite reused-or-extended from MH2's pattern; documentation of the atomicity requirements the adapter must satisfy (sync-consume token subtraction is the load-bearing invariant; any adapter that races on `GET` then `SET` is incorrect regardless of how well it performs).

Out of scope: distributed rate-limiting algorithms beyond the existing token-bucket (sliding window, leaky bucket) — match the existing semantic exactly; cross-region rate-limit synchronization; per-user rate limits beyond the existing per-session and per-IP axes — the rate-limit schema is unchanged.

### 5.3 Slices

- **S01 — Interface extraction.** Refactor the in-process implementation behind a `RateLimiter` interface. No behaviour change. LOC budget: ~200.
- **S02 — Redis adapter.** Implementation against the interface, with atomicity via Lua scripts or `WATCH`/`MULTI`/`EXEC`. LOC budget: ~300.
- **S03 — Documentation and operator UX.** Connection-string docs, failure-mode docs (what happens when Redis is unreachable at startup, becomes unreachable mid-session, latency-spikes), health-check integration. LOC budget: ~150.

### 5.4 Success criteria

Rate-limiting budgets are consistent across replicas when the Redis backend is configured. A test with two simulated replicas sharing a Redis backend confirms. The memory backend's behaviour is unchanged from the pre-MH3 implementation, verified by the existing rate-limit test suite. The sync-consume atomicity invariant holds against the Redis adapter under concurrent access. Failure-mode documentation covers at least: Redis unreachable at startup, Redis becomes unreachable mid-session, Redis latency spikes.

### 5.5 Risk

Medium. Smaller than MH2 in LOC and scope, but the atomicity properties require care. The main risk is reaching for a naive `GET`/`SET` implementation that loses atomicity. Lua scripts or transactions are non-negotiable. S01's interface surface must make the atomicity requirement explicit so the adapter cannot shortcut it.

---

## 6. MH4 — Workflow Registry Abstraction

### 6.1 Goal

Decouple workflow discovery from filesystem-at-startup. A multi-replica deployment must be able to guarantee that every replica sees the same set of workflows at the same fingerprints, without relying on a shared filesystem or a coordinated deploy that demands identical YAML across replicas simultaneously.

### 6.2 Scope

In scope: a `WorkflowRegistry` interface covering the surface `create_app()` currently uses (load-all-at-startup, name-to-workflow-dict lookup); refactor of the existing filesystem-glob implementation into a default `FilesystemRegistry` satisfying the interface; at least one alternative registry implementation, with the backend chosen at the MH4 `/discuss` gate from three candidates (database-backed — workflows stored in the Postgres schema from MH2; Git-backed — periodic pull from a configured remote; HTTP-backed — fetching from a known URL); the M010 fingerprint column becomes the registry's identity key, so two replicas reading the same workflow name must see the same fingerprint, and divergence surfaces through the existing `workflow_changed` mechanism; an operator command to reload a single workflow without a process restart, if the chosen backend supports it (the M010 stance makes this tractable for the first time — the fingerprint change fires `workflow_changed` on in-flight sessions using the edited workflow, exactly as the ADR specifies).

Out of scope: multiple concurrent workflow versions coexisting at runtime — still one fingerprint per name at any given moment; the `workflow_changed` semantic remains; workflow marketplace mechanics (rejected by prior strategic review; Phase J's template library is a curated catalog, categorically different from a user-contributed marketplace); cross-registry workflow federation.

A note on the relationship to Phase J. Phase J's template library and Phase H's workflow registry are different concepts. The template library is a curated catalog of exemplar workflows used as starting points for Shape-5 authoring; it lives in the Phase J client surface, not in the runtime. The workflow registry is the runtime's source of truth for which workflows are loaded and served. A template, once adapted by a Shape-5 user, becomes a regular workflow that the runtime loads through its registry like any other. Templates do not get a special path in the registry; they go through the same deployment pipeline as any other workflow.

### 6.3 Slices

- **S01 — Interface extraction.** Refactor the existing filesystem loading behind a `WorkflowRegistry` interface. LOC budget: ~200.
- **S02 — Alternative registry implementation.** The chosen backend from the `/discuss` gate. LOC budget: ~300–400 depending on choice.
- **S03 — Reload-without-restart.** Operator command plus test coverage for mid-flight reload semantics, integrating cleanly with M010's `workflow_changed`. LOC budget: ~200.

### 6.4 Success criteria

Two replicas configured against the same non-filesystem registry backend see identical workflow fingerprints. A workflow reload produces the expected `workflow_changed` envelopes on in-flight sessions using the edited workflow. The filesystem backend's behaviour is unchanged for local-run and single-replica deployments.

### 6.5 Risk

Medium. The registry-reload semantics interact with M010's session-terminal logic in non-obvious ways. Mitigation: extend M010's test matrix with reload scenarios before shipping S03. Specifically, test that `workflow_file_rewritten` (the post-T02 test scenario name) and the registry-reload path produce the same envelope shape on the same underlying fingerprint change.

---

## 7. MH5 — Auth Integration Hook

### 7.1 Goal

Give enterprise deployments a way to populate `owner_identity` with a real user identity sourced from upstream auth, without building an auth implementation into megálos-server itself.

### 7.2 Scope

In scope: a middleware hook that reads configured HTTP headers (default: `X-Forwarded-User`, `X-Forwarded-Email`) and populates `owner_identity` on new sessions; documentation showing how to front megálos with a reverse-proxy auth solution (oauth2-proxy, pomerium, Authelia) and have the identity propagate correctly; documentation for OIDC and SSO integration patterns, emphasizing that megálos does not implement these — it consumes the headers a properly-configured reverse proxy provides; an optional capability-token-plus-identity model: if both are present, both are checked; if only the token is present, behaviour is unchanged from pre-MH5; a `deny-anonymous` mode (envvar-gated) that rejects requests without a populated identity, off by default but recommended-on for enterprise deployments.

Out of scope: implementing OIDC, SSO, or any auth protocol inside megálos-server (we delegate to the reverse proxy — this is the correct answer and the `/discuss` gate must reject any proposal to in-source an auth protocol); role-based access control on specific tools or workflows (authorization is a separate design surface from identity propagation, and Phase H only covers the latter); audit logging of authentication events (covered by MH6's operational runbook discipline, not by new code here); identity federation across deployments.

### 7.3 Slices

- **S01 — Header middleware and identity propagation.** Read configured headers; stamp identity on new sessions. LOC budget: ~150.
- **S02 — `deny-anonymous` mode and documentation.** Envvar gate; docs page covering oauth2-proxy setup as the canonical example. LOC budget: ~200 (mostly docs).

### 7.4 Success criteria

A session started via a request carrying `X-Forwarded-User: alice@example.com` has `owner_identity` populated accordingly. `deny-anonymous` mode rejects unauthenticated requests with a typed error envelope. Existing capability-token semantics continue to work unchanged when no identity headers are present. Documentation walks through a full oauth2-proxy → megálos-server setup end-to-end.

### 7.5 Risk

Low-medium. The surface is small; the risk is scope creep into implementing auth protocols. Guard at the `/discuss` gate.

---

## 8. MH6 — Operational Runbook

### 8.1 Goal

Document the operational properties an enterprise self-hoster needs to run megálos safely: backup and restore, rolling upgrades, monitoring and alerting, incident response. Not code work — documentation work, stress-tested against real scenarios before canonization.

### 8.2 Scope

In scope: `docs/OPERATIONS.md` covering backup and restore procedures for SQLite and Postgres backends; `docs/UPGRADES.md` covering rolling-upgrade procedure (the M010 workflow-versioning stance makes "drain, then ship" the correct discipline; this doc specifies the drain procedure); `docs/MONITORING.md` covering structured-log format, recommended log-aggregation patterns, recommended alerting thresholds, health-check endpoint usage; `docs/INCIDENT_RESPONSE.md` covering common failure modes (database unreachable, Redis unreachable, orphaned sessions, stuck workflows) with diagnosis and recovery steps. Every documented procedure is stress-tested against a real deployment before it is considered canonical — a backup procedure that has never been restored is a fiction.

Out of scope: commercial SLA templates (Shape 4 concern, not Shape 3); observability tooling integrations by name (Datadog, New Relic, Honeycomb — generic log-format documentation only); compliance documentation (SOC 2, HIPAA, GDPR data flows — those require legal and compliance expertise Phase H does not scope).

### 8.3 Slices

MH6 is docs-heavy and lands continuously across MH2–MH5 rather than sequentially. Alongside MH2: backup/restore for both backends; drain procedure for rolling upgrades (foundational — does not depend on MH3+). Alongside MH3: Redis unreachability failure modes; monitoring recommendations for the rate-limiter. Alongside MH4: registry-backend failure modes; reload-without-restart operator procedure. Alongside MH5: auth-integration operator procedures; `deny-anonymous` rollout guidance.

### 8.4 Success criteria

Every documented procedure has been executed end-to-end against a real deployment before publication. A new operator can read `docs/OPERATIONS.md` + `docs/UPGRADES.md` + `docs/MONITORING.md` and reach a production-ready configuration without additional input. Incident-response procedures cover every named failure mode from MH2–MH5 risk sections.

### 8.5 Risk

Low. The risk is publishing unverified procedures. The mitigation is the execute-end-to-end-before-canonization discipline.

---

## 9. Cross-milestone constraints

The following invariants bind every Phase H milestone and should be checked at each slice gate.

**Local-run stays table stakes.** Every abstraction installed has a default implementation that preserves the current local-run experience. A solo author on their laptop does not configure Postgres to use megálos. In-process SQLite plus in-process rate limiter plus filesystem registry plus anonymous identity stay as the default path; Phase H's pluggable backends are opt-in via environment variables. Shape 1 and Shape 2 do not regress to support Shape 3.

**No new hard runtime dependencies in the default install.** The three-dep constitutional rule (`fastmcp`, `pyyaml`, `jsonschema`) stays intact for `pip install megalos-server`. Postgres, Redis, and any other adapter dependencies live in extras packages or separate distributions.

**The schema doesn't change.** Phase H is backend-side work. Workflow YAML authors are entirely unaffected. Vision-v5's guardrail 6 (expressiveness ceiling holds) binds Phase H exactly as it binds Phase J and every other future surface. If a Phase H milestone wants to add a YAML field, the design is wrong.

**The conformance test suite is the backend contract.** Every pluggable-backend interface (`SessionStore`, `RateLimiter`, `WorkflowRegistry`) ships with a conformance test suite that every adapter must pass. The default implementation's current test suite becomes the conformance suite. Adding a new backend without passing the suite is a non-starter.

**Horizon continues to work as today.** The public Horizon deploy consumes megálos-server like any other deployer. It uses the default SQLite + in-process rate limiter + filesystem registry. Phase H does not change Horizon's operational properties. Horizon is a deployment of megálos, not a special case of it — and the future Horizon Developer+ (Phase I, serving Shape 4) is a separate managed-hosting offering that Phase H architecturally enables but does not itself deliver.

**Everything is a runtime client.** This is vision-v5's guardrail 4 restated for Phase H context. The Phase H backends are adapters consumed by the runtime through defined interfaces. The CLI tools, the IDE extension, the dry-run inspector, the Phase G reference client, and (eventually) the Phase J visual studio are all clients of the same runtime. Phase H's pluggable backends live behind the runtime's existing API; none of the clients need to know which backend is configured.

---

## 10. Sequencing rationale

### Within Phase H

MH1 → MH2 → MH3 → MH4 → MH5, with MH6 landing alongside MH2–MH5. The order is chosen against three criteria.

Ascending scope. MH1 is ~700 LOC across four config/doc-heavy slices. MH2 is ~1550 LOC across five slices touching the runtime's hot path. MH5 is ~350 LOC. The heaviest work lands in the middle, which matches how prior programs scheduled their highest-cost milestones.

Descending immediacy of value. MH1's output (working deployment recipes) is visible to any enterprise evaluator the day it ships. MH2's output is invisible until an evaluator actually deploys multi-replica. MH5's output matters only after MH2 and MH4 have landed.

Dependency ordering. MH3 and MH4 both benefit from MH2's interface-extraction pattern. MH5 is independent and could ship earlier; it is sequenced last only because it is lower-urgency, not because of dependencies.

An acceptable refinement: ship MH1 standalone as "M013" between Phase G and the main Phase H investment. MH1 is low-risk and independently valuable. It unblocks the "enterprise evaluator tries megálos in their cluster" story without committing to MH2's architectural work until a prospect is asking for it. This is a reasonable variant; flagged here, not adopted by default.

### Phase H relative to Phases F, G, I, and J

No hard dependency in either direction with Phase F (mikrós) or Phase G (reference client). Phase F touches authoring, Phase G touches the LLM-facing side, Phase H touches the server-facing backend. Three reasonable orderings exist for the F/G/H trio:

- **F → G → H (default).** Ships Shape 1 and Shape 2 before committing to Phase H's architectural investment. Best if you want user signal from the authoring-DX-complete state before investing in multi-replica work. Recommended unless a specific enterprise prospect is knocking.
- **F → H → G.** Does the backend-hardening work before the client work. Best if enterprise inbound interest materializes earlier than hobbyist adoption. Less natural against the current strategic framing because it leaves Phase G (highest-risk) unresolved longer.
- **H before F.** Rejected. Shape 3 is the smallest audience today by a wide margin; building for them before Shapes 1 and 2 are served is speculation.

Phase H is architecturally prerequisite to **Phase I** (Horizon Developer+, Shape 4). Managed hosting is infeasible while the runtime assumes single-process everything. Phase I cannot begin until MH2, MH3, and MH4 have landed at minimum; MH5 is strongly recommended but not strictly blocking.

Phase H has no hard sequencing relationship with **Phase J** (visual studio, template library, consumer-subscription onramp, Shape 5). A Shape-5 user in Phase J might use a megálos server deployed via Phase H recipes, but neither phase presupposes the other. Phase J can proceed in parallel with Phase H, before it, or after it, depending on adoption-signal priorities. The default sequence assumes Phase J ships after Phase I, but that is not load-bearing and can be revisited.

---

## 11. Out of scope for Phase H

These are strategic questions Phase H does not answer, listed explicitly so that scope creep during implementation is caught at the `/discuss` gate rather than at merge time.

- **A Horizon Developer+ plan or any managed-hosting offering.** Shape 4. Phase I work. Phase H is architecturally prerequisite but does not itself deliver managed hosting.
- **A visual authoring studio, template library, or consumer-subscription onramp.** Shape 5. Phase J work. Phase H does not touch the authoring surface at all; vision-v5 guardrail 4 (everything is a runtime client) means Phase J will consume Phase H's backends through the same runtime API as every other client.
- **Commercial licensing, billing infrastructure, tenancy models.** Shape 4 concerns. Phase I.
- **Cross-region replication, geo-distributed deployments, eventual-consistency semantics.** Single-region multi-replica is the Phase H ceiling. Cross-region is a future design conversation with its own architectural shape.
- **RBAC, ACLs, workflow-level authorization.** Phase H adds identity propagation (MH5); authorization is a separate design surface.
- **Commercial support, SLA commitments, incident-response guarantees.** Shape 4 and Phase I.
- **A second runtime, a second schema, or a second source of truth.** `megalos_server/schema.py` remains canonical. Every Phase H milestone consumes from it; no milestone diverges from it. Vision-v5 guardrail 2 (schema is the contract) applies.
- **Python or arbitrary-code escape hatches in YAML.** Rejected by prior strategic review; still rejected.
- **Session migration from one backend to another.** A self-hoster adopting Phase H starts fresh. Cross-backend migration is an operator-script problem, not a runtime feature.
- **Changes to the user-facing YAML schema.** Vision-v5 guardrail 6. Phase H is backend-side work only.

---

## 12. Open questions

The following should be resolved during the `/discuss` gate of each milestone, not preemptively.

**One backend or two for MH2 and MH3?** Single `MEGALOS_STORE_URL` covering both sessions and rate limiting, or independent `MEGALOS_STORE_URL` plus `MEGALOS_RATELIMIT_BACKEND_URL`? Lean toward one-knob unless a strong reason surfaces. Decide in MH3 S01.

**Redis versus Postgres for MH3's rate limiter.** Redis is the idiomatic choice; Postgres reduces infrastructure surface (one backend to deploy instead of two). Decide in MH3 S01.

**MH4's alternative registry choice.** Database-backed (leverages MH2's Postgres, simplest operational story), Git-backed (operationally familiar to many teams, natural GitOps fit), or HTTP-backed (most flexible, highest complexity). Decide at MH4's `/discuss`.

**MH5's header conventions.** Which headers are defaults (`X-Forwarded-User`, `X-Forwarded-Email`, `X-Forwarded-Groups`)? What is the envvar for customization? Decide in MH5 S01.

**Helm chart hosting.** Publish to Artifact Hub? Self-host on GitHub Pages? Or keep as a starter-only artifact users copy into their own chart museum? Decide in MH1 S03.

**Backport of MH1's envvar consolidation.** The envvar audit may surface naming inconsistencies from prior phases. Do we fix those in-place (breaking change for anyone running master) or deprecate-then-remove across a release? Decide in MH1 S04.

**MH1 standalone variant.** Ship MH1 as M013 between Phase G and the rest of Phase H, or keep it as the first slice of Phase H? Decide at the Phase G → Phase H boundary, not now.

---

## 13. Post-Phase-H evaluation

After Phase H ships, run the post-milestone evaluation across the existing five dimensions — timed-user validation, workflow completion rate, multi-provider validation, runtime stability boundary, documented correction-loop recovery cases. Add two dimensions specific to Phase H.

**Multi-replica correctness.** Deploy megálos-server with two replicas sharing a Postgres + Redis backend; verify sessions are visible across replicas, rate limits are consistent, workflow fingerprints agree. A concrete pass/fail check, not a timing dimension. If this fails, one of MH2/MH3/MH4 shipped with a correctness bug and Phase H is not complete until the regression is fixed.

**Enterprise-evaluator onboarding.** Time-to-first-successful-deployment for an evaluator starting from the repo README, using the Helm chart against a Kubernetes cluster they control. Target: under 30 minutes. If this exceeds the target, MH1's documentation is insufficient — a docs-iteration follow-up rather than a milestone-level problem.

If both succeed, Phase H is complete and Phase I (Horizon Developer+) becomes architecturally possible for the first time. Phase I's own design conversation can begin at that point.

---

## 14. Summary

| Milestone | Scope | Slices | LOC est. | Risk | Shape-3 surface |
|-----------|-------|--------|----------|------|-----------------|
| MH1 — Deployment Recipes | Docker + Compose + K8s + Helm + docs | 4 | ~700 | Low-medium | Evaluator onboarding |
| MH2 — Pluggable Persistence | `SessionStore` interface + Postgres adapter | 5 | ~1550 | High | Multi-replica sessions |
| MH3 — Pluggable Rate-Limiter | `RateLimiter` interface + Redis adapter | 3 | ~650 | Medium | Cross-replica limits |
| MH4 — Workflow Registry | `WorkflowRegistry` interface + non-fs backend | 3 | ~700 | Medium | Shared workflow source-of-truth |
| MH5 — Auth Integration Hook | Header middleware + deny-anonymous + docs | 2 | ~350 | Low-medium | Identity propagation |
| MH6 — Operational Runbook | Docs across MH2–MH5 scenarios | continuous | — | Low | Operator enablement |

Six milestones, sequenced smallest-to-largest, each independently shippable (MH1, MH5) or interface-dependent (MH2 precedes MH3 and MH4 architecturally). Together they remove the four local-only architectural assumptions that prevent Shape 3 from running megálos in multi-replica production configurations.

Phase H serves Shape 3 exclusively. Shape 5's visual studio is a Phase J concern; Shape 4's managed hosting is a Phase I concern. Vision-v5's five-shape framework treats each as a distinct product surface with distinct product work; Phase H is the distinct product work for Shape 3.

At sole-author pace, Phase H is a six-to-twelve-month investment. Combined with Phase I and Phase J, the remaining megálos roadmap beyond current in-flight work (M011, M012, Phase F, Phase G) is in the eighteen-to-twenty-four-month range. Phase H is not the largest single remaining investment — Phase J plausibly matches or exceeds it — but it is the prerequisite investment: Shape 4 cannot begin without Phase H, and Shape 5's deployment surface implicitly uses Phase H recipes.

Upon Phase H completion, Shapes 1, 2, and 3 are all served to foundation-complete standard. The runtime is ready to be deployed as production infrastructure by an enterprise platform team. That is the threshold Phase H exists to cross.
