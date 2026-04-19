"""Unit tests for the single-model synthetic workflow-selection generator CLI.

Mirrors the structure of ``test_generate_synthetic.py`` but scoped to the
single-model authoring path (D026). Cross-model-specific tests (role
alternation, filter round) have no analog here and are intentionally
absent. All tests drive ``tests.selection.generate_synthetic_groq.main``
directly — no subprocess — so we can capture stdout/stderr via ``capsys``
and mock ``panel_query`` via ``unittest.mock.create_autospec`` per D016.
"""

from __future__ import annotations

from unittest.mock import create_autospec

import pytest  # type: ignore[import-not-found]

from megalos_panel import panel_query
from tests.selection import generate_synthetic_groq
from tests.selection.generate_synthetic import (
    PROMPT_TEMPLATE_VERSION,
    SYNTHETIC_GENERATOR_PROMPTS,
)
from tests.selection.generate_synthetic_groq import (
    DEFAULT_AUTHORING_MODEL,
    PAIRS_PER_BATCH,
    SCOPE_ERROR,
    main,
)


# --- dry-run plan shape ---------------------------------------------------


def test_dry_run_all_bands_prints_full_plan(capsys):
    exit_code = main(
        ["--dry-run", "--band", "all", "--n-per-band", "20", "--seed", "42"]
    )
    assert exit_code == 0
    out = capsys.readouterr().out

    # Header lines are present verbatim and byte-compatible with the
    # cross-model generator's header (minus the added authoring_model line).
    assert f"prompt_template_version: {PROMPT_TEMPLATE_VERSION}" in out
    assert "seed: 42" in out
    assert "bands: [1, 2, 3]" in out
    assert "n_per_band: 20" in out
    assert f"authoring_model: {DEFAULT_AUTHORING_MODEL}" in out

    # 20 pairs * 3 bands / 5 per batch = 12 batches. Single-model path:
    # one panel_query call per batch = 12 calls, 60 total PanelRequests
    # (no filter round).
    assert "total_batches: 12" in out
    assert "total_panel_query_calls: 12" in out
    assert "total_panel_requests: 60" in out
    assert f"pairs_per_batch: {PAIRS_PER_BATCH}" in out


def test_dry_run_single_band_limits_plan(capsys):
    exit_code = main(
        ["--dry-run", "--band", "1", "--n-per-band", "5", "--seed", "0"]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "bands: [1]" in out
    # 5 pairs fits in one batch; one panel_query call total.
    assert "total_batches: 1" in out
    assert "total_panel_query_calls: 1" in out


# --- authoring-model surface ---------------------------------------------


def test_default_authoring_model_is_groq_llama_70b(capsys):
    main(["--dry-run", "--band", "1", "--n-per-band", "5"])
    out = capsys.readouterr().out
    assert "authoring_model: groq/llama-3.3-70b-versatile" in out
    # Per-batch line carries it too.
    assert "authoring_model=groq/llama-3.3-70b-versatile" in out


def test_custom_authoring_model_passes_through_unchanged(capsys):
    # Forward-compat: any model string registered in megalos_panel.adapters
    # should pass through. The generator must not special-case the groq/
    # prefix.
    main(
        [
            "--dry-run",
            "--band",
            "1",
            "--n-per-band",
            "5",
            "--authoring-model",
            "claude-opus-4-7",
        ]
    )
    out = capsys.readouterr().out
    assert "authoring_model: claude-opus-4-7" in out
    assert "authoring_model=claude-opus-4-7" in out


def test_non_groq_prefixed_model_accepted(capsys):
    # The generator is prefix-agnostic; strings without groq/ still work.
    main(
        [
            "--dry-run",
            "--band",
            "1",
            "--n-per-band",
            "5",
            "--authoring-model",
            "gpt-4o",
        ]
    )
    out = capsys.readouterr().out
    assert "authoring_model: gpt-4o" in out


# --- band-specific prompt phrasing (inherited from cross-model module) ----


def test_band1_prompt_contains_multi_marker_directive(capsys):
    main(["--dry-run", "--band", "1", "--n-per-band", "5"])
    out = capsys.readouterr().out
    assert "Band 1" in out
    assert "multiple markers" in out


def test_band2_prompt_contains_few_marker_directive(capsys):
    main(["--dry-run", "--band", "2", "--n-per-band", "5"])
    out = capsys.readouterr().out
    assert "Band 2" in out
    assert "few markers distinguish" in out


def test_band3_prompt_contains_one_minimal_marker_directive(capsys):
    main(["--dry-run", "--band", "3", "--n-per-band", "5"])
    out = capsys.readouterr().out
    assert "Band 3" in out
    assert "single minimal marker" in out


def test_prompts_imported_cover_every_band():
    # The module reuses SYNTHETIC_GENERATOR_PROMPTS by import, so this
    # pins the contract at the shared surface.
    assert set(SYNTHETIC_GENERATOR_PROMPTS.keys()) == {1, 2, 3}


# --- error paths ---------------------------------------------------------


def test_unknown_band_exits_nonzero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--dry-run", "--band", "4", "--n-per-band", "5"])
    assert excinfo.value.code != 0
    err = capsys.readouterr().err
    assert "unknown band" in err


def test_live_mode_refuses_with_scope_error(capsys):
    exit_code = main(["--band", "all", "--n-per-band", "20"])
    assert exit_code != 0
    err = capsys.readouterr().err
    assert SCOPE_ERROR in err
    assert "live generation requires" in err


def test_live_mode_does_not_invoke_panel_query(monkeypatch, capsys):
    # Belt-and-braces: even on the scope-error path, no panel_query call
    # must escape. Pin via autospec so a signature drift would fail fast.
    mock = create_autospec(panel_query)
    monkeypatch.setattr(generate_synthetic_groq, "panel_query", mock)
    exit_code = main(["--band", "all", "--n-per-band", "20"])
    assert exit_code != 0
    assert mock.call_count == 0


def test_non_positive_n_per_band_rejected(capsys):
    with pytest.raises(SystemExit):
        main(["--dry-run", "--band", "all", "--n-per-band", "0"])


def test_negative_n_per_band_rejected(capsys):
    with pytest.raises(SystemExit):
        main(["--dry-run", "--band", "all", "--n-per-band", "-1"])


# --- determinism ---------------------------------------------------------


def test_dry_run_output_is_deterministic(capsys):
    main(["--dry-run", "--band", "all", "--n-per-band", "10", "--seed", "42"])
    first = capsys.readouterr().out
    main(["--dry-run", "--band", "all", "--n-per-band", "10", "--seed", "42"])
    second = capsys.readouterr().out
    assert first == second


# --- mock contract (D016): autospec binds to panel_query signature --------


def test_panel_query_autospec_matches_public_signature(monkeypatch):
    """Dry-run does not call panel_query, but when live execution lands
    the test double used by this module's tests must be bound to the real
    signature. Pin that invariant now so drift is caught early."""
    mock = create_autospec(panel_query)
    monkeypatch.setattr(generate_synthetic_groq, "panel_query", mock)
    mock([], record_writer=None, max_workers=8)
    mock.assert_called_once()


def test_dry_run_does_not_invoke_panel_query(monkeypatch):
    mock = create_autospec(panel_query)
    monkeypatch.setattr(generate_synthetic_groq, "panel_query", mock)
    exit_code = main(["--dry-run", "--band", "all", "--n-per-band", "20"])
    assert exit_code == 0
    assert mock.call_count == 0
