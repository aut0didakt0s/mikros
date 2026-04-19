"""Live integration tests for panel_query against real provider APIs.

Marked ``@pytest.mark.live`` so the default test run excludes them (via the
``-m 'not live'`` addopts in pyproject.toml). Run explicitly with
``uv run pytest -m live tests/test_panel_query_live.py`` against a machine
that has ANTHROPIC_API_KEY and/or OPENAI_API_KEY set. Individual tests
skip when their required key is absent so a single-key environment still
exercises the half of the suite it can cover.

Per D017's staged-validation note, this suite is the behavioral-verification
layer — the unit suite ``test_panel_query.py`` verifies composition with
mocked adapters; this suite verifies the composition against real SDK
surfaces and real network paths. Rate-limit-exhaustion behavior is not
automated here (it would require burning quota); see docs/panel.md for the
manual probe procedure.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from megalos_panel import panel_query
from megalos_panel.record import RecordReader, RecordWriter
from megalos_panel.types import PanelRequest


pytestmark = pytest.mark.live


_CLAUDE_MODEL = "claude-opus-4-7"
_OPENAI_MODEL = "gpt-4o"
_TRIVIAL_PROMPT = "Reply with exactly the word OK."


def _require_key(name: str) -> None:
    if not os.environ.get(name):
        pytest.skip(f"{name} not set; skipping live test")


def test_live_claude_basic() -> None:
    _require_key("ANTHROPIC_API_KEY")
    reqs = [PanelRequest(prompt=_TRIVIAL_PROMPT, model=_CLAUDE_MODEL, request_id="live-claude")]
    out = panel_query(reqs)
    assert set(out) == {"live-claude"}
    result = out["live-claude"]
    assert result.error is None, f"unexpected error: {result.error}"
    assert result.selection, "expected non-empty selection"


def test_live_openai_basic() -> None:
    _require_key("OPENAI_API_KEY")
    reqs = [PanelRequest(prompt=_TRIVIAL_PROMPT, model=_OPENAI_MODEL, request_id="live-openai")]
    out = panel_query(reqs)
    assert set(out) == {"live-openai"}
    result = out["live-openai"]
    assert result.error is None, f"unexpected error: {result.error}"
    assert result.selection, "expected non-empty selection"


def test_live_mixed_batch() -> None:
    _require_key("ANTHROPIC_API_KEY")
    _require_key("OPENAI_API_KEY")
    reqs = [
        PanelRequest(prompt=_TRIVIAL_PROMPT, model=_CLAUDE_MODEL, request_id="mixed-claude"),
        PanelRequest(prompt=_TRIVIAL_PROMPT, model=_OPENAI_MODEL, request_id="mixed-openai"),
    ]
    out = panel_query(reqs)
    assert set(out) == {"mixed-claude", "mixed-openai"}
    for rid, result in out.items():
        assert result.error is None, f"{rid}: unexpected error {result.error}"
        assert result.selection, f"{rid}: expected non-empty selection"


def test_live_record_writer_integration(tmp_path: Path) -> None:
    _require_key("ANTHROPIC_API_KEY")
    reqs = [
        PanelRequest(prompt=_TRIVIAL_PROMPT, model=_CLAUDE_MODEL, request_id="live-rec"),
    ]
    with RecordWriter(tmp_path) as writer:
        panel_query(reqs, record_writer=writer)

    assert writer.path is not None
    records = list(RecordReader(writer.path))
    assert len(records) == 1
    rec = records[0]
    assert rec["request_id"] == "live-rec"
    assert rec["model"] == _CLAUDE_MODEL
    assert rec["prompt"] == _TRIVIAL_PROMPT
    assert rec["selection"]
    assert rec["raw_response"]
    assert rec["error"] is None
    assert rec["attempts"] >= 1
    assert rec["elapsed_ms"] >= 0
    assert "T" in rec["timestamp"]
