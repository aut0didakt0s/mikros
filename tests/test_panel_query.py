"""Unit tests for panel_query composition.

Covers the load-bearing contract of the public entry point:

1. Up-front validation: unknown models raise ValueError before any worker
   runs, so callers see deterministic failure mode for typos.
2. Successful multi-request dispatch: every request yields a PanelResult
   keyed by request_id; selection/raw_response carry the adapter's text.
3. Per-request failure isolation: a PanelProviderError surfaces as
   PanelResult.error and does not abort peer requests in the batch.
4. Record-writer integration: one record per request with the documented
   field set (request_id, model, prompt, selection, raw_response, error,
   attempts, elapsed_ms, timestamp).
5. Empty input returns an empty dict without touching the adapter layer.

Adapter behavior is injected via a fake registered through
``megalos_panel.adapters.dispatch``. Retry logic is exercised indirectly — the
retry helper itself is covered by test_panel_retry.py and not re-tested
here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

import megalos_panel.adapters as adapters_mod
import megalos_panel.panel as panel_mod
from megalos_panel.errors import RateLimitError
from megalos_panel.panel import panel_query
from megalos_panel.record import RecordReader, RecordWriter
from megalos_panel.types import PanelRequest


# --- Fake adapter plumbing --------------------------------------------------


class _FakeAdapter:
    """Behavior-injectable stand-in for a provider adapter.

    The class-level ``behavior`` maps a model string to a callable that
    receives a PanelRequest and either returns a string (success) or raises
    an exception. Classification exceptions (RateLimitError / TransientError)
    exercise the retry path; arbitrary exceptions flow through
    retry_with_backoff's non-retryable branch.
    """

    behavior: dict[str, Callable[[PanelRequest], str]] = {}
    calls: list[tuple[str, str]] = []

    def __init__(self) -> None:
        pass

    def invoke(self, request: PanelRequest) -> str:
        _FakeAdapter.calls.append((request.model, request.request_id))
        fn = _FakeAdapter.behavior[request.model]
        return fn(request)


@pytest.fixture(autouse=True)
def reset_fake_adapter() -> None:
    _FakeAdapter.behavior = {}
    _FakeAdapter.calls = []


@pytest.fixture
def patch_dispatch(monkeypatch: pytest.MonkeyPatch) -> Callable[[set[str]], None]:
    """Install a dispatch() that returns ``_FakeAdapter`` for known models.

    The caller supplies the set of models it wants to be treated as known.
    Unknown models raise ValueError matching the real dispatch contract.
    """

    def _install(known: set[str]) -> None:
        def _fake_dispatch(model: str) -> type:
            if model in known:
                return _FakeAdapter
            raise ValueError(
                f"no panel adapter registered for model {model!r}; known: {sorted(known)}"
            )

        monkeypatch.setattr(adapters_mod, "dispatch", _fake_dispatch)

    return _install


# --- Empty batch ------------------------------------------------------------


def test_panel_query_empty_list_returns_empty_dict() -> None:
    """No requests means no adapter dispatch and an empty result dict."""
    assert panel_query([]) == {}


# --- Up-front validation ----------------------------------------------------


def test_panel_query_unknown_model_raises_before_fanout(
    patch_dispatch: Callable[[set[str]], None],
) -> None:
    patch_dispatch({"claude-opus-4-7"})
    reqs = [
        PanelRequest(prompt="a", model="claude-opus-4-7", request_id="r1"),
        PanelRequest(prompt="b", model="llama-9000", request_id="r2"),
    ]
    with pytest.raises(ValueError, match="no panel adapter"):
        panel_query(reqs)
    # Validation happens before any worker runs — no adapter call occurred.
    assert _FakeAdapter.calls == []


# --- Successful dispatch ----------------------------------------------------


def test_panel_query_successful_multi_request(
    patch_dispatch: Callable[[set[str]], None],
) -> None:
    patch_dispatch({"claude-opus-4-7", "gpt-4o"})
    _FakeAdapter.behavior = {
        "claude-opus-4-7": lambda r: f"claude-said: {r.prompt}",
        "gpt-4o": lambda r: f"gpt-said: {r.prompt}",
    }
    reqs = [
        PanelRequest(prompt="pick A", model="claude-opus-4-7", request_id="r1"),
        PanelRequest(prompt="pick B", model="gpt-4o", request_id="r2"),
        PanelRequest(prompt="pick C", model="claude-opus-4-7", request_id="r3"),
    ]
    out = panel_query(reqs, max_workers=2)
    assert set(out) == {"r1", "r2", "r3"}
    assert out["r1"].selection == "claude-said: pick A"
    assert out["r1"].raw_response == "claude-said: pick A"
    assert out["r1"].error is None
    assert out["r2"].selection == "gpt-said: pick B"
    assert out["r3"].selection == "claude-said: pick C"
    assert all(r.error is None for r in out.values())


# --- Mixed success / failure ------------------------------------------------


def test_panel_query_mixed_success_and_failure(
    patch_dispatch: Callable[[set[str]], None],
) -> None:
    """A PanelProviderError surfaces as PanelResult.error; peers still succeed."""
    patch_dispatch({"claude-opus-4-7", "gpt-4o"})

    def _boom(_req: PanelRequest) -> str:
        # Non-retryable generic exception is wrapped into PanelProviderError
        # by retry_with_backoff with attempts=1.
        raise RuntimeError("provider refused")

    _FakeAdapter.behavior = {
        "claude-opus-4-7": lambda r: "ok",
        "gpt-4o": _boom,
    }
    reqs = [
        PanelRequest(prompt="a", model="claude-opus-4-7", request_id="good"),
        PanelRequest(prompt="b", model="gpt-4o", request_id="bad"),
    ]
    out = panel_query(reqs)
    assert out["good"].error is None
    assert out["good"].selection == "ok"
    assert out["bad"].error is not None
    assert "provider refused" in out["bad"].error
    assert out["bad"].selection == ""
    assert out["bad"].raw_response == ""


def test_panel_query_retry_exhaustion_surfaces_as_panel_result_error(
    patch_dispatch: Callable[[set[str]], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhausted rate-limit budget becomes a PanelResult.error, not a raise."""
    # Make backoff sleep a no-op so the test doesn't pause for 30+ seconds.
    monkeypatch.setattr(panel_mod.time, "sleep", lambda _s: None, raising=False)
    # retry.time.sleep is what gets called; patch it directly.
    import megalos_panel.retry as retry_mod
    monkeypatch.setattr(retry_mod.time, "sleep", lambda _s: None)

    patch_dispatch({"claude-opus-4-7"})

    def _rate_limited(_req: PanelRequest) -> str:
        raise RateLimitError("429 too many requests")

    _FakeAdapter.behavior = {"claude-opus-4-7": _rate_limited}
    reqs = [PanelRequest(prompt="a", model="claude-opus-4-7", request_id="r1")]
    out = panel_query(reqs)
    assert out["r1"].error is not None
    assert "429" in out["r1"].error or "rate" in out["r1"].error.lower()
    assert out["r1"].selection == ""


# --- Record writer integration ----------------------------------------------


def test_panel_query_writes_one_record_per_request(
    patch_dispatch: Callable[[set[str]], None],
    tmp_path: Path,
) -> None:
    patch_dispatch({"claude-opus-4-7", "gpt-4o"})
    _FakeAdapter.behavior = {
        "claude-opus-4-7": lambda r: "claude-answer",
        "gpt-4o": lambda r: "gpt-answer",
    }
    reqs = [
        PanelRequest(prompt="p1", model="claude-opus-4-7", request_id="r1"),
        PanelRequest(prompt="p2", model="gpt-4o", request_id="r2"),
    ]
    with RecordWriter(tmp_path) as writer:
        panel_query(reqs, record_writer=writer)

    written_path = writer.path
    assert written_path is not None
    # First line is the schema marker; subsequent lines are request records.
    records = list(RecordReader(written_path))
    assert len(records) == 2
    by_id = {r["request_id"]: r for r in records}
    assert set(by_id) == {"r1", "r2"}

    expected_fields = {
        "request_id",
        "model",
        "prompt",
        "selection",
        "raw_response",
        "error",
        "attempts",
        "elapsed_ms",
        "timestamp",
    }
    for rec in records:
        assert set(rec) == expected_fields
        assert rec["error"] is None
        assert rec["attempts"] == 1
        assert isinstance(rec["elapsed_ms"], int)
        assert rec["elapsed_ms"] >= 0
        # Timestamp is ISO-8601 with timezone.
        assert "T" in rec["timestamp"]

    r1 = by_id["r1"]
    assert r1["model"] == "claude-opus-4-7"
    assert r1["prompt"] == "p1"
    assert r1["selection"] == "claude-answer"
    assert r1["raw_response"] == "claude-answer"


def test_panel_query_record_writer_captures_failures(
    patch_dispatch: Callable[[set[str]], None],
    tmp_path: Path,
) -> None:
    patch_dispatch({"claude-opus-4-7"})

    def _boom(_req: PanelRequest) -> str:
        raise RuntimeError("provider dead")

    _FakeAdapter.behavior = {"claude-opus-4-7": _boom}
    reqs = [PanelRequest(prompt="p", model="claude-opus-4-7", request_id="r1")]
    with RecordWriter(tmp_path) as writer:
        panel_query(reqs, record_writer=writer)

    assert writer.path is not None
    with writer.path.open() as fh:
        lines = [json.loads(line) for line in fh.readlines()]
    # Line 0 is schema marker; line 1 is the one record.
    assert lines[0] == {"schema_version": "1"}
    rec = lines[1]
    assert rec["error"] is not None
    assert "provider dead" in rec["error"]
    assert rec["selection"] == ""
    assert rec["raw_response"] == ""
    assert rec["attempts"] == 1


# --- Adapter reuse ---------------------------------------------------------


def test_panel_query_reuses_adapter_per_distinct_model(
    patch_dispatch: Callable[[set[str]], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two requests against the same model should share one adapter instance.

    Re-instantiating an adapter per request wastes an SDK client
    construction each time. The composition contract is one adapter per
    distinct model per panel_query call.
    """
    patch_dispatch({"claude-opus-4-7"})
    _FakeAdapter.behavior = {"claude-opus-4-7": lambda r: "ok"}

    instance_count = 0
    original_init = _FakeAdapter.__init__

    def counting_init(self: _FakeAdapter) -> None:
        nonlocal instance_count
        instance_count += 1
        original_init(self)

    monkeypatch.setattr(_FakeAdapter, "__init__", counting_init)

    reqs = [
        PanelRequest(prompt=f"p{i}", model="claude-opus-4-7", request_id=f"r{i}")
        for i in range(5)
    ]
    panel_query(reqs)
    assert instance_count == 1
