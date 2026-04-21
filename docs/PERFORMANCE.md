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

**Measurement protocol.** Each benchmark is run by `pytest-benchmark`
with a warmup phase (discarded) followed by N timed iterations. We
report the **median**, not the mean, because a single outlier from GC,
thermal throttle, or a background process will drag the mean around
without shifting the median. `pytest-benchmark` emits min, max, mean,
stddev, median, IQR, and OPS; the median row is the one to watch over
time.

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

## Baseline Numbers [to be populated in S02]

## Known Hot Paths [to be populated in S02]

## Dev-vs-Production Comparison [to be populated in S03]

## Soft Regression Floor Policy [to be populated in S03]
