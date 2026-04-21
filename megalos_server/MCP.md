# MCP Integration Guide

How workflows call external MCP (Model Context Protocol) servers via
`action: mcp_tool_call` steps. For the line-item field reference, see
[SCHEMA.md](SCHEMA.md). This document covers the runtime envelope, the
registry format, the retry policy, and the prompt-injection posture
authors must keep in mind when consuming tool output.

## Action reference

### Step shape

An `mcp_tool_call` step is non-LLM. It names a server and a tool, supplies
literal or ref-path arguments, and writes a flat result envelope into
`step_data.<id>`. It never carries `directive_template`, `gates`, or
`anti_patterns` — those are rejected at load time.

Required fields: `id`, `title`, `action: mcp_tool_call`, `server`, `tool`,
`args`.

Optional fields: `timeout` (positive number, seconds — overrides the server's
`timeout_default`), `precondition`, `branches` / `default_branch`,
`step_description`.

Minimal example:

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

### Mutex rules

An `mcp_tool_call` step must not declare any of the following. Each
violation has its own error code; the validator reports all mismatches in
one pass rather than short-circuiting on the first.

| Forbidden field | Error code |
|-----------------|------------|
| `directive_template` | `mcp_tool_call_with_directive_template` |
| `gates`              | `mcp_tool_call_with_gates` |
| `anti_patterns`      | `mcp_tool_call_with_anti_patterns` |
| `call`               | `mcp_tool_call_with_call` |
| `collect`            | `mcp_tool_call_with_collect` |
| `output_schema`      | `mcp_tool_call_with_output_schema` |

`server` and `tool` must be literal strings. Interpolation in either field
fails load as `mcp_tool_call_server_not_literal` /
`mcp_tool_call_tool_not_literal`.

### The envelope

Every `mcp_tool_call` step writes exactly one of two shapes into
`step_data.<id>`:

```yaml
# success
ok: true
value: "<tool text output, concatenated across text content blocks>"
```

```yaml
# failure
ok: false
error:
  message: "<prefixed failure reason>"
```

`value` is always a string on the success path. v1 only accepts
`TextContent` blocks from the server; anything else (images, resources,
structured data blocks) surfaces as a `protocol error`. Multiple text blocks
are concatenated without separators.

The `ok` field is the authoritative success discriminator. Route downstream
work with `precondition.when_equals` against `step_data.<id>.ok`:

```yaml
- id: handle_success
  title: Act on success
  directive_template: Use step_data.fetch_weather.value
  gates: [done]
  anti_patterns: [skip]
  precondition:
    when_equals:
      ref: step_data.fetch_weather.ok
      value: true
```

### Failure-message prefixes

Every `ok: false` envelope carries a message prefixed with a class marker,
so downstream LLM steps (and log grepping) can discriminate without parsing
free text:

| Prefix | Meaning |
|--------|---------|
| `schema error:` | The caller's `args` failed validation against the tool's `inputSchema`, or the server's schema itself is malformed. |
| `transport error:` | Could not reach the server (connect error, HTTP error, missing auth env var). Persists after retries. |
| `protocol error:` | Server reachable, but the response violated the MCP envelope contract (unknown tool, v1-unsupported content type, malformed JSON-RPC). |
| `timeout` | The effective per-call deadline elapsed. No `:` — the string is exactly `timeout`. |
| `unresolved arg ref:` | An `${step_data.<path>}` arg pointed at a step that never ran (precondition false, for example). The tool was **not** invoked. |

The `unresolved arg ref:` message names the offending ref verbatim, e.g.
`unresolved arg ref: ${step_data.intake.city}`. The executor intentionally
does not dispatch the call when any arg leaf is unresolved — sending a
sentinel value to a remote tool produces a cryptic server-side schema
rejection that is harder to diagnose than the local error.

### Skipped predecessors

If an `mcp_tool_call` step's ref-path points at a step that was **itself
skipped** (by an unsatisfied precondition further upstream), the cascade
propagates: the current step is also skipped, no envelope is written, and
downstream refs into it cascade the same way. This matches the semantics of
LLM steps and precondition-skipped steps elsewhere in the workflow engine.

### Retry behavior

The client retries transient failure classes with deterministic exponential
backoff: **3 total attempts**, 200 ms before attempt 2, 400 ms before
attempt 3, no jitter. Only `transport error:` and `timeout` outcomes are
retried. `schema error:`, `protocol error:`, and tool-execution failures
return on the attempt that produced them — retrying them never changes the
answer, and the round-trip cost is real.

Attempts surface only via logs on the `megalos_server.mcp` logger, not via
the envelope. The envelope shape is stable regardless of attempt count.
Relevant log fields: `server`, `tool`, `attempt`, `backoff_ms` (on retry
transitions), `total_ms` (on terminal records), `arg_fingerprint` (SHA-256
hex8 of sorted-key JSON of args — safe to correlate, reveals nothing about
values). A retriable failure that exhausts all attempts emits a `warning`
with the terminal outcome detail; success and non-retriable failures emit
`info`.

## Registry format

`mcp_servers.yaml` declares which external servers a workflow may call
into. It is strict: unknown fields, duplicate names, unsupported
transports, and malformed auth all fail load with actionable errors.

### Top-level shape

```yaml
servers:
  - name: weather
    url: https://mcp.example.com/weather
    transport: http
    auth:
      type: bearer
      token_env: WEATHER_MCP_TOKEN
    timeout_default: 10
  - name: storage
    url: https://mcp.internal/storage
    transport: http
    auth:
      type: bearer
      token_env: STORAGE_MCP_TOKEN
```

An empty file or a file with `servers: []` is a valid zero-server
registry; workflows with no `mcp_tool_call` steps still load.

### Per-server fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Unique server identifier; referenced by `mcp_tool_call.server`. Duplicate names fail load. |
| `url` | string | yes | HTTP endpoint of the MCP server. |
| `transport` | literal `"http"` | yes | v1 supports HTTP only. `stdio` / `grpc` will widen the union when they land. |
| `auth` | mapping | yes | Authentication block. See below. |
| `timeout_default` | positive number | no | Default per-call timeout in seconds. Step-level `timeout` overrides. When neither is set, the client falls back to 30 s. |

### Auth

v1 supports bearer tokens via environment variables only:

```yaml
auth:
  type: bearer
  token_env: SOME_ENV_VAR_NAME
```

**The env var is resolved at call time, not at registry load.** This is
deliberate. A missing env var surfaces against a specific call as a
`transport error: auth env var $<VAR> not set`, not as a daemon startup
failure. Operators can rotate tokens without bouncing the server, and a
registry referencing an env var that only exists on some workloads stays
valid.

### Discovery precedence

The CLI entry point `python -m megalos_server.validate <workflow>`
resolves the registry in this order (first hit wins):

1. Explicit `--registry <path>` flag.
2. `<workflow.parent>/mcp_servers.yaml` — sibling of the workflow file.
3. `$CWD/mcp_servers.yaml` — current working directory.

Absence is valid when the workflow has no `mcp_tool_call` steps.
Otherwise, workflow load fails with `mcp_tool_call_registry_required`.

### Failure modes

- **Malformed registry YAML** — fail-fast at load. Messages name the file,
  the 1-based entry index, and the offending field, e.g.
  `mcp_servers.yaml: entry 2 (name="weather"): unknown field 'retries'`.
- **Unknown server reference** — a workflow's `mcp_tool_call.server` that
  is not in the registry fails load as `mcp_tool_call_unknown_server`,
  with the available names listed.
- **Missing auth env var at call time** — surfaces as `transport error:`
  on the envelope, not a crash. The step fails gracefully; the rest of
  the workflow sees the normal `ok: false` envelope.

## Prompt-injection posture

megalos treats LLM-reachable tool output as untrusted input to subsequent tool calls and LLM steps.

See [`SECURITY.md#prompt-injection-posture`](../SECURITY.md#prompt-injection-posture) for the full posture, untrusted-slot rule, and sanitation-step pattern.

This file no longer owns prompt-injection prose; `SECURITY.md` is canonical.

## Live smoke

A nightly advisory GitHub Actions job (`mcp-smoke`, workflow
`.github/workflows/mcp-smoke.yml`) runs `ci/mcp_smoke.py` against a
Horizon-deployed FastMCP stub (`mcp_stub/main.py:mcp`, auth off). The
stub URL is pinned via the `MCP_STUB_URL` GitHub secret and set by the
operator after running `./stub-deploy.sh` and completing the one-click
deploy on horizon.prefect.io.

- Current URL: `https://mcp-stub.fastmcp.app/mcp`
- Trigger: `schedule: '0 6 * * *'` (06:00 UTC) + `workflow_dispatch`.
- Check name: `mcp-smoke`. **Not** a required check — advisory only.
- A red run indicates live-path drift between the client and the
  deployed stub (envelope contract change, schema-cache staleness,
  transport regression). It does not block merges.

The smoke call is a single `echo(value="ci-smoke")` against the stub;
any outcome other than `Ok(value="ci-smoke")` fails the job.

## Migration note

The flat envelope `{ok, value|error}` is the v1 contract. Future extensions
will be **additive-optional** — a `meta` field carrying attempt counts,
duration, or server-provided metadata may land, but the `ok`, `value`, and
`error.message` fields will remain at their current paths with their
current semantics. Workflow authors can rely on
`step_data.<id>.ok`, `step_data.<id>.value`, and
`step_data.<id>.error.message` as stable ref paths across versions.
