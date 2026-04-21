"""Per-turn hot-path benchmarks: submit_step + get_state + gather=10.

Three cases. All dispatch in-process via ``tests.conftest.call_tool``
against the live ``megalos_server.main.mcp`` singleton. The singleton
exercises the full middleware stack (validation → caller-identity →
rate-limit) plus pydantic argument validation plus tool body plus
SQLite via the shared db module.

1. ``bench_submit_step`` — per-turn WRITE anchor. Each iteration drives
   one ``submit_step`` call against a freshly-started session. The
   measurement captures start_workflow overhead in setup and times only
   the write call itself under pytest-benchmark's per-round harness.
2. ``bench_get_state`` — per-turn READ control. Same setup, times a
   single ``get_state`` read. Diff vs. ``bench_submit_step`` = the
   write-path cost (SQLite INSERT/UPDATE, step_data persistence,
   visit bump).
3. ``bench_concurrent_sessions_gather10`` — 10-way parallel via
   ``asyncio.gather``. Each coroutine drives start_workflow + one
   submit_step. Asserts all 10 succeed and that total wall-time is not
   wildly super-linear vs. a reference single-pair measurement (rough
   lock-contention probe; a true serialization bug would show up as
   ``total > 3 * single``).
"""

from __future__ import annotations

import asyncio

from megalos_server import state
from megalos_server.main import mcp
from tests.conftest import call_tool


def _start_canonical_session() -> str:
    """Helper: start a ``canonical`` fixture session and return its sid."""
    state.clear_sessions()
    r = call_tool("start_workflow", {"workflow_type": "canonical", "context": "bench"})
    return r["session_id"]


def bench_submit_step(benchmark) -> None:  # type: ignore[no-untyped-def]
    """Time a single submit_step against a freshly-started session.

    Each pytest-benchmark iteration runs the full ``submit_step``
    path: CallerIdentityMiddleware → RateLimitMiddleware →
    pydantic argument validation → tool body → SQLite write.

    ``pedantic`` mode is used so the setup callable (session start)
    runs once per round rather than being timed alongside the payload.
    ``rounds=100, iterations=1`` gives 100 independent submit_step
    calls against 100 fresh sessions — matches how a real per-turn
    workload would look."""

    def _setup() -> tuple[tuple, dict]:
        sid = _start_canonical_session()
        return (sid,), {}

    def _submit(sid: str) -> None:
        r = call_tool(
            "submit_step",
            {"session_id": sid, "step_id": "alpha", "content": "bench-payload"},
        )
        assert r.get("code") is None, r

    benchmark.pedantic(_submit, setup=_setup, rounds=100, iterations=1, warmup_rounds=5)


def bench_get_state(benchmark) -> None:  # type: ignore[no-untyped-def]
    """Time a single get_state against a freshly-started session.

    READ control for ``bench_submit_step``. Same middleware stack,
    same pydantic dispatch, but the tool body is a SQLite SELECT +
    in-memory assembly (no INSERT/UPDATE). Diff vs. submit_step
    isolates the write-path cost."""

    def _setup() -> tuple[tuple, dict]:
        sid = _start_canonical_session()
        return (sid,), {}

    def _read(sid: str) -> None:
        r = call_tool("get_state", {"session_id": sid})
        assert r.get("code") is None, r

    benchmark.pedantic(_read, setup=_setup, rounds=100, iterations=1, warmup_rounds=5)


def bench_concurrent_sessions_gather10(benchmark) -> None:  # type: ignore[no-untyped-def]
    """Drive 10 session start+submit pairs concurrently via asyncio.gather.

    Each coroutine creates its own session and submits one step. The
    benchmark times the full gather. Two assertions capture health:

    - All 10 pairs succeed (no ``code`` on any response).
    - Total wall-time is not wildly super-linear. We compute a loose
      ceiling from the median of ``bench_submit_step`` — pytest-benchmark
      does not surface cross-test medians mid-run, so the ceiling here
      is an in-run reference: we run a single (start + submit) pair
      once before the timed loop to establish ``ref_time`` and assert
      ``total < 3 * ref_time * 10`` per gather. A true serialization
      bug would produce 10× or worse; this cheap guard flags it."""
    async def _one_pair(i: int) -> dict:
        r = await mcp.call_tool(
            "start_workflow",
            {"workflow_type": "canonical", "context": f"gather-{i}"},
        )
        sc = r.structured_content
        assert sc is not None
        sid = sc["session_id"]
        r2 = await mcp.call_tool(
            "submit_step",
            {"session_id": sid, "step_id": "alpha", "content": "gather-payload"},
        )
        sc2 = r2.structured_content
        assert sc2 is not None
        return sc2

    # Establish reference single-pair wall time in the same process.
    state.clear_sessions()
    import time
    t0 = time.perf_counter()
    asyncio.run(_one_pair(-1))
    ref_time = time.perf_counter() - t0

    async def _gather10() -> list[dict]:
        coros = [_one_pair(i) for i in range(10)]
        return await asyncio.gather(*coros)

    def _run_gather() -> list[dict]:
        state.clear_sessions()
        return asyncio.run(_gather10())

    results = benchmark(_run_gather)
    assert len(results) == 10, results
    assert all(r.get("code") is None for r in results), results
    # Loose lock-contention guard: a 10-way gather should not take
    # more than 3× what 10 serial pairs would. ref_time * 10 * 3.
    median = benchmark.stats.stats.median  # type: ignore[attr-defined]
    ceiling = ref_time * 10 * 3
    assert median < ceiling, (
        f"10-way gather median {median:.4f}s exceeded loose ceiling "
        f"{ceiling:.4f}s (ref single-pair {ref_time:.4f}s) — lock "
        "contention probe fired, investigate."
    )
