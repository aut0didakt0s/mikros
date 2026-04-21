"""Mock-LLM contract for benchmark suite.

Benchmarks must dispatch tools in-process. Going through a real
``fastmcp.Client`` loop would pull in network I/O, provider calls, and
event-loop overhead that drown the signal we want to measure. This
module enforces that rule at collection time with a stdlib-only text
scan. If any benchmark source file references ``fastmcp.client`` or
``from fastmcp import Client``, the whole suite aborts loudly before a
single timing is recorded.

False positives (comments/docstrings mentioning the forbidden strings)
are the known failure mode. If they bite, upgrade to an AST walk — not
here. This file itself is excluded from the scan so it can describe the
rule it enforces.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_FORBIDDEN = ("fastmcp.client", "from fastmcp import Client")
_BENCH_DIR = Path(__file__).parent


def _scan_for_forbidden() -> list[tuple[Path, str]]:
    hits: list[tuple[Path, str]] = []
    for py_path in _BENCH_DIR.rglob("*.py"):
        if py_path.resolve() == Path(__file__).resolve():
            continue
        text = py_path.read_text(encoding="utf-8")
        for needle in _FORBIDDEN:
            if needle in text:
                hits.append((py_path, needle))
    return hits


_violations = _scan_for_forbidden()
if _violations:
    lines = [f"  {p}: contains {needle!r}" for p, needle in _violations]
    pytest.exit(
        "Benchmark mock-LLM contract violated — benchmarks must dispatch "
        "tools in-process, not through a fastmcp.Client loop:\n"
        + "\n".join(lines),
        returncode=1,
    )
