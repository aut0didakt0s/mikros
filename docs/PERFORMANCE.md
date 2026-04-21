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

## Dev-vs-Production Comparison [to be populated in S03]

## Soft Regression Floor Policy [to be populated in S03]
