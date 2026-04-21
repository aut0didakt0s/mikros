"""Catalog-iter benchmarks for ``list_workflows``.

Two fixed catalog sizes:

- **N=3 (``bench_list_workflows_n3``)** — production proxy. The in-repo
  deployment ships one ``example.yaml`` and delegates real catalogs to
  the three public domain repos (``megalos-writing``, ``-analysis``,
  ``-professional``) — so "prod N=3" is the shape an operator sees in
  each domain deployment today. We load three synthetic minimal-YAML
  workflows to stand in for that shape without pulling in external
  repos.
- **N=20 (``bench_list_workflows_n20``)** — synthetic scaling probe.
  Loads 20 minimal-YAML workflows from ``benchmarks/fixtures/workflows_n20/``.
  Answers "does iteration cost scale linearly with catalog size?"

Both benchmarks swap out the live app's ``WORKFLOWS`` dict for the
duration of the test so the measurement reflects only iteration cost
(not the cost of loading YAML off disk). Restoration is handled by a
fixture on teardown.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from megalos_server.main import WORKFLOWS
from megalos_server.schema import load_workflow
from tests.conftest import call_tool


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "workflows_n20"


def _synthetic_wf(name: str) -> dict:
    """Return a minimal in-memory workflow dict for the N=3 bench.

    Shape matches what ``load_workflow`` produces for the N=20 YAML
    fixtures — one step, bare schema — so the two benchmarks measure
    the same iteration shape at different N."""
    return {
        "name": name,
        "description": f"Synthetic prod-proxy workflow {name}.",
        "category": "bench",
        "output_format": "text",
        "steps": [
            {
                "id": "only",
                "title": "Only step",
                "directive_template": "Stub.",
                "gates": ["done"],
                "anti_patterns": ["none"],
            }
        ],
    }


@pytest.fixture
def _install_n3() -> Iterator[None]:
    names = ["bench_prod_writing", "bench_prod_analysis", "bench_prod_professional"]
    for n in names:
        WORKFLOWS[n] = _synthetic_wf(n)
    yield
    for n in names:
        WORKFLOWS.pop(n, None)


@pytest.fixture
def _install_n20() -> Iterator[None]:
    loaded: list[str] = []
    for yaml_path in sorted(_FIXTURE_DIR.glob("*.yaml")):
        wf = load_workflow(str(yaml_path))
        WORKFLOWS[wf["name"]] = wf
        loaded.append(wf["name"])
    assert len(loaded) == 20, f"expected 20 fixtures, loaded {len(loaded)}"
    yield
    for n in loaded:
        WORKFLOWS.pop(n, None)


def bench_list_workflows_n3(benchmark, _install_n3) -> None:  # type: ignore[no-untyped-def]
    """Time ``list_workflows`` with three synthetic prod-proxy workflows.

    Production domain servers (writing/analysis/professional) each
    ship a handful of workflows; the in-repo reference app ships one.
    Three is the realistic small-catalog case today."""

    def _call() -> None:
        r = call_tool("list_workflows", {})
        assert r["total"] >= 3, r  # ``canonical`` fixture + 3 synthetics

    benchmark(_call)


def bench_list_workflows_n20(benchmark, _install_n20) -> None:  # type: ignore[no-untyped-def]
    """Time ``list_workflows`` with 20 synthetic minimal-YAML workflows.

    Answers: does iteration cost scale linearly with N? A super-linear
    result here would be the scaling surprise this benchmark exists to
    surface."""

    def _call() -> None:
        r = call_tool("list_workflows", {})
        assert r["total"] >= 20, r

    benchmark(_call)
