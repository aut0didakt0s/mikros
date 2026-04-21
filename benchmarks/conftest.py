"""Mock-LLM contract + per-benchmark DB isolation for the benchmark suite.

Two responsibilities:

1. **Mock-LLM contract (collection-time).** Benchmarks must dispatch
   tools in-process. Going through a real ``fastmcp.Client`` loop would
   pull in network I/O, provider calls, and event-loop overhead that
   drown the signal we want to measure. A stdlib-only text scan aborts
   the suite if any benchmark source file references ``fastmcp.client``
   or ``from fastmcp import Client``.

   False positives (comments/docstrings mentioning the forbidden
   strings) are the known failure mode. If they bite, upgrade to an AST
   walk — not here. This file itself is excluded from the scan so it
   can describe the rule it enforces.

2. **Per-bench DB isolation (autouse fixture).** Every benchmark runs
   against its own file-backed SQLite DB under ``tmp_path``, mirroring
   the pattern in ``tests/conftest.py::_isolated_db``. File-backed (not
   ``:memory:``) to match production: FastMCP dispatches handlers via
   the asyncio executor, so cross-thread visibility needs a real file,
   and disk I/O is where real SQLite cost lives. The ``:memory:`` route
   would hide the exact cost benchmarks exist to measure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

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


@pytest.fixture(autouse=True)
def _bench_isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every benchmark runs against its own file-backed SQLite DB.

    Mirrors ``tests/conftest.py::_isolated_db``. Intentionally not
    imported from there — pytest autouse fixtures defined in a parent
    ``conftest.py`` are not picked up when the benchmark suite is run
    from its own pytest rootdir (``benchmarks/``). Duplicating the
    fixture here is the boring option; the alternative is a shared
    conftest tree with collection-time surprises.
    """
    from megalos_server import db  # local import — avoid import at collection time

    monkeypatch.setenv("MEGALOS_DB_PATH", str(tmp_path / "bench_session.db"))
    db._reset_for_test()
    yield
    db._reset_for_test()
