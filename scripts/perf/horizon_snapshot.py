"""One-shot Horizon production perf snapshot for megalos.

Captures median + stddev wire-call latencies against a Horizon-deployed
megalos endpoint and emits a markdown-ready table that pastes verbatim
into ``docs/PERFORMANCE.md`` §Dev-vs-Production Comparison.

Shape of a run (single contiguous client session; 250 ms spacing
between iterations to stay well under the per-session rate limit of
2.0/s with burst 30):

    1. RTT floor prelude  ── 10× ``list_workflows`` over a pre-warmed
                             TLS connection. Measures the 'everything
                             below is physics' floor that all
                             subsequent server-work deltas subtract.
    2. ``list_workflows``  ── 10 iterations on a fresh client
                             (TLS already warm from prelude).
    3. ``start_workflow``  ── once; captures ``session_id`` for
                             subsequent reads/writes.
    4. ``get_state``       ── 10 iterations against that session.
    5. ``submit_step``     ── 10 iterations against that session.
                             Error envelopes (e.g. out-of-order,
                             validation) are expected on repeat
                             submissions; the wire path and middleware
                             stack still execute, which is what we're
                             measuring.
    6. ``delete_session``  ── teardown.

Why a standalone script, not a benchmark:
    ``benchmarks/conftest.py`` enforces a mock-LLM contract that
    forbids ``fastmcp.Client`` imports. This script genuinely needs a
    ``Client`` because that *is* the wire path to Horizon. Living in
    ``scripts/perf/`` preserves the benchmark contract and matches the
    manual-dispatch pattern set by ``.github/workflows/mcp-smoke.yml``.

Horizon auth (Fork B):
    Horizon's free-tier org-auth is mandatory (see
    ``SECURITY.md#deployment-forks`` and ``.megalos/DECISIONS.md``
    entry dated 2026-04-14 "Fork B"). The endpoint returns 401 to any
    request that does not carry a Horizon-org-member session. A
    credential-less CLI cannot produce that session: raw Prefect
    account keys (``pnu_*``) 401 as expired, and the
    OAuth-code-for-token exchange 401s because this script is not a
    registered OAuth app on Horizon's side.

    **Operator workflow for running this script today:**
    1. Sign in to ``horizon.prefect.io`` with a Horizon-org-member
       account that has access to the target endpoint.
    2. Open a browser session that will broker the org cookie to
       ``fastmcp.Client`` — in practice this means running the script
       from a machine where the Horizon CLI or a browser has already
       authenticated and persisted the org session.
    3. Run the script.

    If you see 401 ``www-authenticate: Bearer realm="FastMCP"`` in the
    error output, the browser-brokered org session is not reaching
    ``fastmcp.Client``. Refresh the Horizon session and retry; if that
    fails, this script cannot run from the current shell and the
    operator must capture the snapshot from a shell with a live
    browser-brokered org session.

Usage::

    python scripts/perf/horizon_snapshot.py \\
        --endpoint https://megalos-writing.fastmcp.app \\
        --iterations 10 \\
        --spacing-ms 250

Exits 0 on a successful capture with the markdown table on stdout.
Exits 1 on any wire failure, auth failure, sanity-floor violation,
or sample-count shortfall; stderr carries a runbook-pointing message
and no partial markdown is emitted.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from typing import Any, Callable

# Dev-median baselines from docs/PERFORMANCE.md §Baseline Numbers,
# captured 2026-04-21 at commit 1d945ad. Hard-coded intentionally:
# re-runs after a baseline update require a script update regardless,
# so a CLI flag would only push the coupling one layer out without
# adding flexibility.
_DEV_MEDIAN_US = {
    "list_workflows": 654.3,   # bench_list_workflows_n3 (prod-proxy N=3).
    "get_state": 655.3,        # bench_get_state.
    "submit_step": 664.9,      # bench_submit_step.
}

# Any sample below this value indicates the client is not actually
# reaching Horizon — likely a local cache hit, a mock, or a stub. This
# is NOT a latency SLO; it is a wire-reality check. Under 1 ms, the
# speed of light alone does not allow a round-trip to any Horizon
# region from a commodity laptop, so any sub-millisecond sample is
# evidence that the measurement is not what it claims to be.
_SANITY_FLOOR_MS = 1.0

_RUNBOOK = "docs/PERFORMANCE.md#dev-vs-production-comparison"


def _fail(msg: str) -> int:
    """Emit a single-line stderr error and return exit code 1.

    No partial stdout: the caller has already ensured nothing was
    written to stdout before calling us. Keeps the "never emit a
    half-written markdown table" invariant intact.
    """
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


async def _timed(
    fn: Callable[[], Any], spacing_ms: int, iterations: int
) -> list[float]:
    """Run ``fn`` ``iterations`` times with ``spacing_ms`` between calls.

    Returns a list of per-call wall-clock durations in milliseconds.
    Any exception aborts the run — no partial samples.
    """
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        await fn()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        samples.append(elapsed_ms)
        await asyncio.sleep(spacing_ms / 1000.0)
    return samples


def _summary_us(samples: list[float]) -> tuple[float, float]:
    """Return (median_us, stddev_us) for a list of millisecond samples."""
    median_us = statistics.median(samples) * 1000.0
    stddev_us = (statistics.stdev(samples) if len(samples) > 1 else 0.0) * 1000.0
    return median_us, stddev_us


def _fmt_us(value_us: float) -> str:
    """Render a microsecond value in the unit that best fits a table cell."""
    if value_us >= 1000.0:
        return f"{value_us / 1000.0:.1f} ms"
    return f"{value_us:.1f} µs"


async def _run(  # noqa: PLR0915 — linear script, splitting hides the sequence
    endpoint: str, iterations: int, spacing_ms: int
) -> int:
    # Import inside the function so ``--help`` works even if fastmcp is
    # unavailable in the environment. Matches ``scripts/smoke_endpoint.py``.
    try:
        from fastmcp import Client  # type: ignore[attr-defined]  # library typing gap
    except ImportError as exc:
        return _fail(
            f"fastmcp is not installed: {exc}. Install with `uv sync` "
            f"and re-run."
        )

    try:
        async with Client(endpoint) as client:
            # --- Step 1: RTT floor prelude (list_workflows × iterations).
            # Using list_workflows as the floor call is deliberate. A
            # dedicated "no-op" tool would be lighter, but doesn't exist
            # on the wire; list_workflows is the cheapest tool Horizon
            # exposes and its server-side cost is already a known
            # constant from §Baseline Numbers. The prelude measures the
            # TLS-warm, post-handshake floor — this is not the "pure
            # physics" of raw TCP RTT, but it is the floor that every
            # subsequent call inherits.
            rtt_samples = await _timed(
                lambda: client.call_tool("list_workflows", {}),
                spacing_ms,
                iterations,
            )
            rtt_median_ms = statistics.median(rtt_samples)

            # --- Step 2: list_workflows timed (TLS already warm).
            # This second pass measures a stable list_workflows median
            # on a warm connection; median across steps 1 + 2 would be
            # more robust but also opaque — two passes let the operator
            # sanity-check that the RTT floor and the measured call are
            # consistent.
            lw_result = await client.call_tool("list_workflows", {})
            lw_samples = await _timed(
                lambda: client.call_tool("list_workflows", {}),
                spacing_ms,
                iterations,
            )

            # --- Step 3: start_workflow once; capture session_id +
            # current step_id for the submit_step path.
            data = getattr(lw_result, "data", None) or getattr(
                lw_result, "structured_content", None
            )
            if not isinstance(data, dict) or not data.get("workflows"):
                return _fail(
                    f"list_workflows returned no workflows at {endpoint}. "
                    f"Runbook: {_RUNBOOK}"
                )
            first = data["workflows"][0]
            workflow_type = first.get("name")
            if not isinstance(workflow_type, str) or not workflow_type:
                return _fail(
                    f"list_workflows response missing 'name' at {endpoint}. "
                    f"Runbook: {_RUNBOOK}"
                )

            start_result = await client.call_tool(
                "start_workflow",
                {"workflow_type": workflow_type, "context": "perf snapshot"},
            )
            start_data = getattr(start_result, "data", None) or getattr(
                start_result, "structured_content", None
            )
            if not isinstance(start_data, dict) or "session_id" not in start_data:
                return _fail(
                    f"start_workflow did not return a session_id "
                    f"(workflow={workflow_type}). Runbook: {_RUNBOOK}"
                )
            session_id = start_data["session_id"]
            current_step = start_data.get("current_step", {})
            step_id = current_step.get("id") if isinstance(current_step, dict) else None

            # --- Step 4: get_state × iterations on the active session.
            gs_samples = await _timed(
                lambda: client.call_tool("get_state", {"session_id": session_id}),
                spacing_ms,
                iterations,
            )

            # --- Step 5: submit_step × iterations on the active session.
            # Only the first submission can succeed on a fresh session
            # (subsequent ones return an out_of_order_submission
            # envelope). That is the intended measurement: an error
            # envelope still exercises the full middleware + state
            # read/write path, which is the wire cost we care about.
            # If the operator wants to measure the write-success path
            # specifically, a future revision can start a fresh session
            # per iteration — at the cost of measuring start_workflow
            # overhead instead of submit_step in isolation.
            submit_args = {
                "session_id": session_id,
                "step_id": step_id or "",
                "content": '{"goal": "perf snapshot measurement"}',
            }
            ss_samples = await _timed(
                lambda: client.call_tool("submit_step", submit_args),
                spacing_ms,
                iterations,
            )

            # --- Step 6: teardown.
            await client.call_tool("delete_session", {"session_id": session_id})

    except Exception as exc:  # noqa: BLE001 — single catch-all by design
        message = str(exc).lower()
        if "401" in message or "unauthorized" in message or "authenticate" in message:
            return _fail(
                f"Horizon authentication failed at {endpoint}: {exc}. "
                f"Fork B endpoints require a browser-brokered "
                f"Horizon-org-member session. See "
                f"SECURITY.md#deployment-forks and {_RUNBOOK}."
            )
        if "timeout" in message or "timed out" in message:
            return _fail(
                f"Timed out connecting to {endpoint}: {exc}. Check "
                f"network connectivity and endpoint URL. Runbook: "
                f"{_RUNBOOK}."
            )
        if "5" in message and ("500" in message or "502" in message or "503" in message or "504" in message):
            return _fail(
                f"Horizon returned 5xx at {endpoint}: {exc}. Endpoint "
                f"may be experiencing issues; retry or contact Horizon "
                f"support. Runbook: {_RUNBOOK}."
            )
        return _fail(f"Unexpected error at {endpoint}: {exc}. Runbook: {_RUNBOOK}.")

    # Sanity floor: any sub-millisecond sample in the measured sets is
    # evidence the client is not hitting the real wire. Check across
    # every measured sample set, not just RTT floor.
    all_samples = rtt_samples + lw_samples + gs_samples + ss_samples
    floor_hits = [s for s in all_samples if s < _SANITY_FLOOR_MS]
    if floor_hits:
        return _fail(
            f"Sample <{_SANITY_FLOOR_MS} ms detected "
            f"(min={min(floor_hits):.3f} ms). This indicates a mock or "
            f"local-cache artifact, not a real wire hit. Confirm "
            f"{endpoint} is a reachable Horizon endpoint and that the "
            f"client is not intercepted by a local stub. Runbook: "
            f"{_RUNBOOK}."
        )

    # Compute medians + stddev.
    lw_med_us, lw_std_us = _summary_us(lw_samples)
    gs_med_us, gs_std_us = _summary_us(gs_samples)
    ss_med_us, ss_std_us = _summary_us(ss_samples)
    rtt_med_us = rtt_median_ms * 1000.0

    # Emit markdown table. Operator pastes verbatim into PERFORMANCE.md.
    rows = [
        (
            "list_workflows",
            iterations,
            rtt_med_us,
            lw_med_us,
            lw_std_us,
            _DEV_MEDIAN_US["list_workflows"],
        ),
        (
            "get_state",
            iterations,
            None,  # RTT floor reported once in the list_workflows row
            gs_med_us,
            gs_std_us,
            _DEV_MEDIAN_US["get_state"],
        ),
        (
            "submit_step",
            iterations,
            None,
            ss_med_us,
            ss_std_us,
            _DEV_MEDIAN_US["submit_step"],
        ),
    ]

    header = (
        "| Operation | Samples | RTT floor | Prod median | Prod stddev "
        "| Dev median | Server work (prod − RTT) | Ratio (server/dev) |"
    )
    divider = (
        "|-----------|---------|-----------|-------------|"
        "-------------|------------|--------------------------|"
        "--------------------|"
    )
    print(header)
    print(divider)
    for name, n, rtt, pmed, pstd, dmed in rows:
        # Server work = prod median − RTT floor. For rows after the
        # first, the RTT floor is the same captured value; we subtract
        # it but do not re-print it to keep the table readable.
        server_work_us = pmed - rtt_med_us
        ratio = server_work_us / dmed if dmed > 0 else 0.0
        rtt_cell = _fmt_us(rtt) if rtt is not None else "—"
        print(
            f"| {name} | {n} | {rtt_cell} | {_fmt_us(pmed)} | "
            f"{_fmt_us(pstd)} | {_fmt_us(dmed)} | "
            f"{_fmt_us(server_work_us)} | {ratio:.1f}× |"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--endpoint",
        default="https://megalos-writing.fastmcp.app",
        help="Horizon endpoint URL (default: %(default)s).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=10,
        help="Iterations per measured operation (default: %(default)s).",
    )
    parser.add_argument(
        "--spacing-ms",
        type=int,
        default=250,
        help=(
            "Delay between iterations in ms (default: %(default)s). "
            "250 ms = 4 calls/sec; well under per-session 2.0/s rate "
            "limit with burst 30. Do NOT lower to 100 ms — it brushes "
            "the limit and produces throttle artifacts at iteration ~6."
        ),
    )
    args = parser.parse_args(argv)

    if args.iterations < 2:
        return _fail("--iterations must be >= 2 for stddev computation.")
    if args.spacing_ms < 0:
        return _fail("--spacing-ms must be >= 0.")

    return asyncio.run(_run(args.endpoint, args.iterations, args.spacing_ms))


if __name__ == "__main__":
    sys.exit(main())
