"""Sub-workflow stack benchmarks: depth 1 vs. at-cap depth 3.

The stack cap is committed at ``max_stack_depth = 3`` per the README
and ``tests/test_session_stack_push.py``. We benchmark both the
single-level case (one push + one pop) and the at-cap case (three
pushes + three pops). Diff between the two catches non-linear
regressions in stack-depth handling — e.g., an accidental O(depth²)
walk in resume/own_frame lookup would show up here even when the
per-turn benchmarks are clean.

Depth 5 would require bypassing the cap (synthetic and wrong). Lock
at {1, 3}.

Fixtures: two minimal workflows, registered for the duration of the
benchmark, each with enough steps that push/pop flows do not prematurely
complete the outer. Registration happens via ``WORKFLOWS`` mutation,
mirroring the in-repo test pattern in ``test_session_stack_push.py``.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from megalos_server import state
from megalos_server.main import WORKFLOWS
from tests.conftest import call_tool


_OUTER = "bench_push_outer"
_DIGRESSION = "bench_push_digression"


def _outer_wf() -> dict:
    return {
        "name": _OUTER,
        "description": "bench outer linear workflow",
        "category": "bench",
        "output_format": "text",
        "steps": [
            {"id": "o1", "title": "O1", "directive_template": "o1",
             "gates": ["done"], "anti_patterns": []},
            {"id": "o2", "title": "O2", "directive_template": "o2",
             "gates": ["done"], "anti_patterns": []},
        ],
    }


def _digression_wf() -> dict:
    return {
        "name": _DIGRESSION,
        "description": "bench digression workflow",
        "category": "bench",
        "output_format": "text",
        "steps": [
            {"id": "d1", "title": "D1", "directive_template": "d1",
             "gates": ["done"], "anti_patterns": []},
        ],
    }


@pytest.fixture(autouse=True)
def _register_push_fixtures() -> Iterator[None]:
    WORKFLOWS[_OUTER] = _outer_wf()
    WORKFLOWS[_DIGRESSION] = _digression_wf()
    yield
    WORKFLOWS.pop(_OUTER, None)
    WORKFLOWS.pop(_DIGRESSION, None)


def _start_outer() -> str:
    state.clear_sessions()
    r = call_tool("start_workflow", {"workflow_type": _OUTER, "context": "bench"})
    return r["session_id"]


def bench_subworkflow_depth_1(benchmark) -> None:  # type: ignore[no-untyped-def]
    """One push_flow + one pop_flow. Baseline stack mechanism cost.

    Each iteration runs against a fresh outer session: push a
    digression, then pop it. Times the full push+pop round-trip.
    Compare vs. depth_3 to catch non-linear regressions."""

    def _setup() -> tuple[tuple, dict]:
        sid = _start_outer()
        return (sid,), {}

    def _push_pop_once(outer_sid: str) -> None:
        push = call_tool(
            "push_flow",
            {
                "session_id": outer_sid,
                "workflow_type": _DIGRESSION,
                "paused_at_step": "o1",
                "context": "bench-digress",
            },
        )
        assert push.get("code") is None, push
        child_sid = push["session_id"]
        pop = call_tool("pop_flow", {"session_id": child_sid})
        assert pop.get("code") is None, pop

    benchmark.pedantic(
        _push_pop_once, setup=_setup, rounds=50, iterations=1, warmup_rounds=3
    )


def bench_subworkflow_depth_3(benchmark) -> None:  # type: ignore[no-untyped-def]
    """Three pushes + three pops — at the ``max_stack_depth = 3`` cap.

    Each round: outer → d1 (depth 1) → d2 (depth 2) → d3 (depth 3),
    then pop three times back to outer. Times the full round-trip.

    Diff vs. depth_1 catches regressions in stack-walking. A linear
    relationship is expected; a super-linear relationship at depth=3
    would be the kind of scaling surprise this benchmark exists to
    surface."""

    def _setup() -> tuple[tuple, dict]:
        sid = _start_outer()
        return (sid,), {}

    def _push3_pop3(outer_sid: str) -> None:
        # Depth 1: push onto outer.
        p1 = call_tool(
            "push_flow",
            {
                "session_id": outer_sid,
                "workflow_type": _DIGRESSION,
                "paused_at_step": "o1",
                "context": "bench-d1",
            },
        )
        assert p1.get("code") is None, p1
        sid1 = p1["session_id"]
        # Depth 2: push onto sid1. The digression's only step id is "d1";
        # push_flow's ``paused_at_step`` defensive echo expects the
        # caller to pass sid1.current_step which is "d1" (fresh top).
        p2 = call_tool(
            "push_flow",
            {
                "session_id": sid1,
                "workflow_type": _DIGRESSION,
                "paused_at_step": "d1",
                "context": "bench-d2",
            },
        )
        assert p2.get("code") is None, p2
        sid2 = p2["session_id"]
        # Depth 3 (at cap).
        p3 = call_tool(
            "push_flow",
            {
                "session_id": sid2,
                "workflow_type": _DIGRESSION,
                "paused_at_step": "d1",
                "context": "bench-d3",
            },
        )
        assert p3.get("code") is None, p3
        sid3 = p3["session_id"]
        # Pop back down.
        for sid in (sid3, sid2, sid1):
            r = call_tool("pop_flow", {"session_id": sid})
            assert r.get("code") is None, r

    benchmark.pedantic(
        _push3_pop3, setup=_setup, rounds=50, iterations=1, warmup_rounds=3
    )
