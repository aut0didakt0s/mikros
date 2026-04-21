# megalos Performance

## Purpose

The benchmark suite serves two framings:

- **Regression gate (pre-Phase-F baseline).** Before Phase F lands, we
  capture a numeric baseline of in-process cold-start and hot-path
  timings so later refactors cannot silently regress performance
  without the benchmark suite catching it.
- **Surprise detection.** A pytest-benchmark run that reports numbers
  wildly different from the recorded baseline is a signal to
  investigate — whether the cause is a code change, a dependency
  bump, or a host/OS shift.

Explicit non-goal: these benchmarks are **not** inputs to Fork B SLA
guarantees. We report local, mock-LLM timings from an in-process
dispatch path; real-world latency depends on provider round-trips,
network, and workload shape. SLA numbers must be sourced separately.

## Methodology

**Mock-LLM discipline.** Benchmarks dispatch tools in-process; no
`fastmcp.Client` loop, no real provider calls. Enforced by
`benchmarks/conftest.py` via a collection-time scan that aborts the
suite if any benchmark file references `fastmcp.client` or
`from fastmcp import Client`.

**Median over min — the reported statistic.** Each benchmark is run by
`pytest-benchmark` with a warmup phase (discarded) followed by N timed
iterations. We report the **median**, not the min and not the mean:
- **Not min:** a single lucky iteration (cache-hot CPU, no GC pause)
  can land well below any realistic repeat; min overstates best-case
  and understates regressions.
- **Not mean:** a single outlier from GC, thermal throttle, or a
  background process drags the mean around without shifting the
  median.

`pytest-benchmark` emits min, max, mean, stddev, median, IQR, and OPS;
the median row is the one to watch over time.

**Warmup discards.** `pytest-benchmark` runs a warmup phase before the
timed rounds; its timings are discarded. Per-benchmark we also request
explicit `warmup_rounds=3–5` when using `.pedantic(...)` so the first
few cold-cache calls do not pollute the recorded distribution. Fresh
devs: do not disable warmup to "see" faster numbers — you will be
chasing a signal that will regress the moment the cache cools.

**No pytest-benchmark JSON artifacts in git.** The `.benchmarks/` and
`.pytest_benchmark/` directories hold machine-specific timing archives
that churn on every run. They carry no value once the numbers are
snapshotted in this document, so they are gitignored and never
committed. Commit the summary table here; let the raw JSON live only
on the machine that produced it.

**File-backed SQLite under `tmp_path`, never `:memory:`.** Every
benchmark runs against a file-backed SQLite database in `tmp_path`
(autouse `_bench_isolated_db` fixture in `benchmarks/conftest.py`).
Matching production is the goal — `:memory:` databases hide the disk
I/O that is the dominant SQLite cost in real deployments, and FastMCP
dispatches handlers through the asyncio executor which requires
cross-thread DB visibility (another reason `:memory:` would misbehave).
A grep gate enforces the rule: `grep -rn ':memory:' benchmarks/` must
return zero matches.

### Memory regression gate

The rotating-IP stress test at
`tests/test_ratelimit_adversarial.py::test_rotating_ip_attack_bounded_by_store_cap`
drives 100K unique IPs through the LRU-capped IP store
(`ip_store_cap=10_000`). The test now asserts a tracemalloc peak
ceiling on top of the pre-existing bucket-count assertion, catching
memory regressions that the count check would miss (e.g., a per-bucket
field that bloats dict shape while leaving eviction correct).

- **Measurement:** peak bytes under `tracemalloc.start()` /
  `get_traced_memory()` bracket over the 100K-IP loop.
- **Observed peak (2026-04-21, cap=10k):** ~3.0 MB, stable across 3
  runs with <0.1% variance.
- **Ceiling:** 5 MB (≈1.67× measurement). Tight enough to fail loud on
  a >70% regression (bucket leak, LRU bug, struct bloat); loose enough
  to absorb minor runtime variance across Python patch versions.
- **Re-derive when:** `ip_store_cap` changes (peak scales linearly
  with cap), bucket shape changes (new fields on `_IpStore.buckets`
  entries), or Python major-version bump shifts dict internals
  materially.
- **Re-run command:**
  ```bash
  uv run python -c "
  import tracemalloc
  from megalos_server.ratelimit import RateLimiter, RateLimitConfig, AXIS_IP
  cfg = RateLimitConfig(ip_rate=1.0, ip_burst=5.0,
                        ip_store_cap=10_000, ip_idle_ttl_sec=1e9)
  peaks = []
  for _ in range(3):
      limiter = RateLimiter(cfg, monotonic=lambda: 0.0)
      tracemalloc.start()
      for i in range(100_000):
          limiter.try_consume(AXIS_IP, f'10.0.{i // 256}.{i % 256}')
      _, peak = tracemalloc.get_traced_memory()
      tracemalloc.stop()
      peaks.append(peak / (1024*1024))
  print(sorted(peaks))
  "
  ```
  Then update `_IP_STORE_100K_PEAK_CEILING_MB` in
  `tests/test_ratelimit_adversarial.py` to `1.5×–2×` the new measured
  peak and update this subsection.

**Noise handling.** Run the suite on a quiesced host when possible:

- Close latency-sensitive user applications (browsers, chat clients,
  video calls) to reduce background CPU churn.
- Let the machine thermally stabilize for a minute or two before the
  first run — cold laptops often clock higher than a "warm"
  steady-state they'll settle into.
- On Linux, optionally disable frequency scaling for the run
  (`cpupower frequency-set -g performance`) and re-enable afterward.
  On macOS, `sudo pmset -a lowpowermode 0` during the run.
- Prefer consistent power state (plugged in for laptops).

Noise is unavoidable on commodity hardware; that is why we compare
medians, and why the suite is oriented toward detecting
order-of-magnitude surprises rather than sub-percent drifts.

## Re-run Steps

```bash
uv run pytest benchmarks/ --benchmark-only
```

Flag explanations:

- `benchmarks/` — target the benchmark directory. `benchmarks/pytest.ini`
  overrides the repo-root `pyproject.toml` config so coverage
  instrumentation does not run and inflate measurements.
- `--benchmark-only` — skip any non-benchmark test function. Harmless
  belt-and-braces given `pytest.ini` already sets this in `addopts`;
  explicit on the command line makes re-runs obvious in shell history.

For a verbose run that prints the pytest-benchmark summary table:

```bash
uv run pytest benchmarks/ --benchmark-only -v
```

## Infrastructure

Benchmarks live in `benchmarks/`. The directory is a self-contained
pytest root with its own `pytest.ini`, its own `conftest.py` (enforcing
the mock-LLM contract), and one file per benchmark concern.

`pytest-benchmark` is a dev dependency gated behind a dedicated uv
dependency group so it does not inflate the default `uv sync`:

```bash
uv sync --group benchmark
```

Runtime dependencies (`fastmcp`, `jsonschema`, `pyyaml`) are unchanged.

## Baseline Numbers

Captured 2026-04-21 against commit `1d945ad` (`chore: track
.gsd/deferred/ as project artifact namespace`), authored on the same
day. Host: macOS on Apple Silicon (`Darwin 25.4.0`), Python 3.12.12,
pytest-benchmark 5.2.3. Re-capture is expected if numbers drift more
than ~5% from these in a fresh run on the same class of hardware.

| Benchmark | Median | Stddev | Ops/sec | Expectation |
| --- | --- | --- | --- | --- |
| `bench_submit_step` | 664.9 µs | 81.6 µs | 1,442 | Per-turn write anchor — full middleware + SQLite write. Diff vs `get_state` ≈ write-path cost. |
| `bench_get_state` | 655.3 µs | 553.5 µs | 1,290 | Per-turn read control. Middleware + SQLite read. Diff vs `submit_step` ≈ 10 µs — write path is a thin margin on top of dispatch overhead. |
| `bench_subworkflow_depth_1` | 2.65 ms | 269.8 µs | 369 | One push_flow + one pop_flow. Baseline stack mechanism cost. |
| `bench_subworkflow_depth_3` | 8.36 ms | 6.49 ms | 107 | Three pushes + three pops at `max_stack_depth = 3` cap. ≈3.15× depth_1 — linear scaling, as expected. |
| `bench_list_workflows_n3` | 654.3 µs | 579.9 µs | 1,488 | Catalog iter at prod-proxy N=3. ~same as `get_state` — dispatch overhead dominates iteration cost at small N. |
| `bench_list_workflows_n20` | 922.2 µs | 859.4 µs | 1,040 | Catalog iter at N=20. 1.41× N=3; iteration cost is sub-linear in catalog size because dispatch is a fixed floor. |
| `bench_concurrent_sessions_gather10` | 32.96 ms | 9.49 ms | 32 | 10-way asyncio.gather of (start_workflow + submit_step). ~3.3× what 10 serial pairs would cost — within the loose lock-contention guard, no serialization bug surfaced. |

Numbers above are medians from the `bench` run used to land the
baseline; the adjacent runs during authoring produced medians within
±5% on the same hardware. Stddev widths in the `list_workflows` rows
are a real feature of that fast path — at ~650 µs per call the round
count is high (>1,000) so a few GC pauses in the long tail pull stddev
up while leaving the median unchanged.

## Known Hot Paths

No hotspots surfaced during S02 baseline capture. Per-turn write vs.
read is within ~10 µs; sub-workflow depth scales linearly from 1 to 3;
catalog iteration is sub-linear in N; 10-way concurrent gather stays
inside the loose 3× serial ceiling. Future regressions caught by
§Baseline Numbers re-capture per §Methodology.

No conditional `normalize_session_id` microbenchmark was added: the
`bench_submit_step` distribution showed no signal that normalize was a
notable fraction of the median, and the plan is explicit that the
microbench is conditional, not speculative.

## Known Issues

- **`bench_concurrent_sessions_gather10` — known flake under cross-round session accumulation.** The benchmark's 10-way gather interacts with the 5-session cap when pytest-benchmark rolls multiple rounds without teardown; `start_workflow` returns a `session_cap_exceeded` envelope (no `session_id` field) and the benchmark's unguarded `r["session_id"]` surfaces a `KeyError`. Typically passes on immediate re-run from a fresh process. Investigation and fix (defensive response read + per-iteration cleanup, or cap-bump fixture) tracked separately.

## Dev-vs-Production Comparison

**Snapshot.** Captured against `megalos-writing.fastmcp.app` (Fork B,
Horizon-hosted at megalos runtime v0.3.0). Re-run via
`scripts/perf/horizon_snapshot.py`. See §Horizon Snapshot Runbook
below for auth setup.

> **Table body:** `[OPERATOR TO CAPTURE POST-MERGE]` — the standalone
> script lands in this commit but the snapshot itself requires a
> browser-brokered Horizon-org-member session that is not available in
> the agent environment that lands this change. The operator will run
> the script separately and add a follow-up commit populating the row
> values below. Table header is the stable contract that the script
> emits; only the three data rows are pending.

| Operation | Samples | RTT floor | Prod median | Prod stddev | Dev median | Server work (prod − RTT) | Ratio (server/dev) |
|-----------|---------|-----------|-------------|-------------|------------|--------------------------|--------------------|
| list_workflows | 10 | [pending] | [pending] | [pending] | 654.3 µs | [pending] | [pending] |
| get_state | 10 | — | [pending] | [pending] | 655.3 µs | [pending] | [pending] |
| submit_step | 10 | — | [pending] | [pending] | 664.9 µs | [pending] | [pending] |

Dev-median values are pinned to §Baseline Numbers above (captured
2026-04-21 at commit `1d945ad`). `scripts/perf/horizon_snapshot.py`
hard-codes the same three values; re-running the snapshot after a
baseline update requires updating both the script's constants and
this table.

### Divergence Budget

Expected sources of dev-vs-prod delta, in rough order of contribution:

- **Network RTT** — dev box to Horizon region round-trip. Captured as
  the RTT floor column (via a prelude of 10 `list_workflows` calls
  over a TLS-warmed connection). Every "server work" value in the
  table subtracts this floor so the column isolates actual
  server-side cost.
- **TLS handshake** — cold-connect adds a handshake roundtrip.
  Subsequent calls on a warm connection are cheaper. The script
  pre-warms via the RTT-floor prelude before the measured passes to
  minimize this term.
- **JSON-over-wire overhead** — serialize/deserialize of request and
  response bodies through FastMCP's HTTP transport. Scales with
  payload size; dominant for large `get_state` responses on long
  sessions, negligible for the small payloads this script exercises.
- **Horizon container overhead** — container runtime plus FastMCP's
  HTTP transport layer. Fundamentally absent in the in-process
  benchmarks, so shows up entirely as delta.
- **Container-disk SQLite** — Horizon's disk is a container volume
  with different I/O characteristics than a dev laptop SSD. May be
  faster or slower depending on host. Affects the `submit_step` row
  more than the read-path rows.

### Interpretation Guidance

Use the budget above before reading into raw dev-vs-prod ratios.
"Dev 25× faster than prod" is noise — a commodity laptop running
in-process dispatch against a local SQLite file is expected to beat
a containerized HTTP deployment across a network round-trip by
orders of magnitude. That ratio is physics plus the architectural
difference, not a signal to investigate.

The real signal is **unexpected divergence beyond the budget**: a
re-capture where the RTT floor is consistent with prior runs but
one operation's server-work column jumps by a large factor, or a
stddev that widens substantially on a path that used to be stable.
That is the pattern worth investigating — a code change that added
latency on one tool surface without affecting others, or a
dependency-version bump that altered one subpath's performance
profile.

### Horizon Snapshot Runbook

**Auth setup.** Horizon free-tier org-auth is mandatory (see
`SECURITY.md#deployment-forks` and `.megalos/DECISIONS.md` entry
dated 2026-04-14 "Fork B"). Headless CLI invocations of
`scripts/perf/horizon_snapshot.py` return 401 because:

- Raw Prefect account keys (`pnu_*`) are not Horizon access tokens
  and 401 as expired on the megalos endpoint.
- The browser OAuth consent flow succeeds but the
  `fastmcp.Client` code-for-token exchange 401s because the script
  is not a registered OAuth app on Horizon's side.

**Workable pattern.** Run the script from a shell where a browser
or the Horizon CLI has already authenticated and persisted an
org-member session that `fastmcp.Client` can consume. If a 401
surfaces, refresh the Horizon session in the browser and retry.
Matches the same dispatch-only constraint that drove the
`.github/workflows/mcp-smoke.yml` schedule removal (M007/PR#5).

**Default target.** `https://megalos-writing.fastmcp.app`. All three
domain endpoints (`megalos-writing`, `megalos-analysis`,
`megalos-professional`) run the same megalos runtime at v0.3.0;
pick one and stick with it across re-captures so the numbers are
comparable over time.

**Post-capture.** Paste the script's stdout markdown table into
the table block above. Update the snapshot header with the
capture date, the commit SHA the endpoint was serving at capture
time, and the endpoint URL if changed. Commit with a message
describing the capture context (re-capture trigger or initial
population).

## Soft Regression Floor Policy

### What 'Soft' Means

This policy is **human-reviewed, not machine-enforced**. A future
CI engineer reading the "~20% threshold" line below could build a
hard CI gate that fails PRs on benchmark drift. They should not.
The thresholds below are **trigger points for human review**, not
failure conditions for automated testing.

The hard CI regression gate is explicitly deferred (see §Hard CI
Gate — Explicitly Deferred at the bottom of this section) because
a single baseline snapshot is not robust enough to distinguish
machine-specific timing variance from real regression. An
automated enforcement layer on top of a single-point floor would
either be too tight (flake-prone on thermal and scheduling noise)
or too loose (misses real regressions). Neither mode is useful.

**Load-bearing framing.** A contributor reviewing a benchmark-drift
warning must decide whether it is a hardware-class change requiring
re-capture or a real regression requiring investigation. That
decision is not mechanizable on one data point. Build the
multi-point floor before building the gate.

### Per-Benchmark Thresholds

Any benchmark whose median drifts by **more than ~20%** versus the
§Baseline Numbers table triggers human review. Not automated
failure — review triggers a judgment call:

- **Legitimate drift** — hardware upgrade, interpreter minor
  version change, OS major upgrade on the benchmark host. Remedy:
  re-capture the baseline per §Re-Capture Triggers and record the
  trigger in the commit message that updates the baseline.
- **Potential regression** — no obvious environment change, or a
  known hot-path milestone change coincides with the drift.
  Remedy: investigate. If the drift is confirmed as a regression,
  remediation lands as a follow-up task or milestone — **never
  revert the baseline to hide a regression**.

~20% is chosen as tier-one stability. Tighter thresholds (e.g.
~5% or ~10%) fire on thermal throttling, scheduler jitter, and
GC-pause variance that produce flake-fatigue without surfacing
real issues. The §Baseline Numbers stddev widths on the sub-ms
paths (`list_workflows`, `get_state`, `submit_step`) exceed 5% of
their medians already — a 5% threshold would alert on every run.

### Re-Capture Triggers

Re-capture §Baseline Numbers (and re-run the Horizon snapshot if
relevant) when any of the following fires:

- **Hot-path milestone change** — a milestone that modifies any
  of: the middleware chain, `megalos_server/state.py` write path,
  the rate limiter, session_id canonicalization, the sub-workflow
  stack, or the workflow loader.
- **Dependency version bump** — `fastmcp`, `pyyaml`, or
  `jsonschema` at the major or minor version level. Patch bumps
  do not trigger; they are assumed bug-fix-only by semver.
- **Interpreter version change** — Python minor version change on
  the benchmark host (e.g., 3.12 → 3.13).
- **OS upgrade on benchmark host** — major macOS or Linux version
  upgrade where the baseline was originally captured. Patch-level
  security updates do not trigger.

Re-capture **replaces** the previous baseline; there is no archive
of prior baselines in this document. The git history of this file
is the archive — previous commits carry earlier baselines under
their own SHA, and re-capture commit messages record the trigger
(hot-path change / dependency bump / interpreter change / OS
upgrade) so the chain is reconstructible.

### Judgment-Call Override

Thresholds are a trigger point, not an enforcement mechanism.
Legitimate drift (hardware upgrade, interpreter change, OS
upgrade) is acceptable; the remedy is **re-capture**, not
revert. The reviewer documents the judgment call in the commit
message that updates the baseline: which trigger fired, what the
environment delta was, and — where relevant — confirmation that
the drift direction matches the expected direction of the
environment change.

A "drift direction sanity check" is a useful discipline: a
hardware upgrade that produces *slower* medians is a surprise
worth investigating before accepting the new baseline; the same
upgrade producing *faster* medians is the expected case and
needs less scrutiny.

### Hard CI Gate — Explicitly Deferred

**Deferred:** a hard CI-enforced regression gate that fails PRs
on benchmark median drift beyond a configured threshold.

**Why:** A single dev-box baseline is insufficient for automatic
enforcement — one snapshot cannot distinguish machine-specific
timing variance from real regression. Automated enforcement on a
single-snapshot floor would either be too tight (flake-prone) or
too loose (misses real regressions), and neither mode is useful.

**When:** A follow-up milestone introduces the hard gate once
multiple baseline snapshots (collected over time, and potentially
across different benchmark hosts) inform the floor. That
milestone will define the noise envelope and a statistical
threshold grounded in multi-point variance, not a single-point
cutoff.
