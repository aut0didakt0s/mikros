"""Smoke benchmarks proving the pytest-benchmark plumbing works.

Two cases:

1. ``bench_dict_get`` — a minimal-work call that exercises the benchmark
   harness without flirting with pytest-benchmark's timer-resolution floor
   (``lambda: 1+1`` produces noisy output on fast hardware). A dict
   ``.get()`` is a few nanoseconds of honest work; the timing itself is
   not the point, the plumbing is.

2. ``bench_create_app`` — instantiates the megalos FastMCP app in-process.
   Proves ``megalos_server`` imports resolve under the benchmark harness
   (validates the ``pythonpath = ..`` declaration in ``pytest.ini``) and
   gives S02 a known-good in-process measurement surface to build on.
"""

from __future__ import annotations

from megalos_server import create_app


def bench_dict_get(benchmark) -> None:  # type: ignore[no-untyped-def]
    d = {"a": 1, "b": 2, "c": 3}
    benchmark(lambda: d.get("a"))


def bench_create_app(benchmark) -> None:  # type: ignore[no-untyped-def]
    benchmark(create_app)
