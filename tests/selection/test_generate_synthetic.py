"""Unit tests for the synthetic workflow-selection fixture generator CLI.

All tests drive ``tests.selection.generate_synthetic.main`` directly — no
subprocess — so we can capture stdout/stderr via ``capsys`` and mock
``panel_query`` via ``unittest.mock.create_autospec`` per D016.

The dry-run plan is the verification surface here: T04a ships no fixtures
and makes no live calls, so the tests assert on the structure and content
of the printed plan rather than on fixture output.
"""

from __future__ import annotations

from unittest.mock import create_autospec

import pytest  # type: ignore[import-not-found]

from megalos_panel import panel_query
from tests.selection import generate_synthetic
from tests.selection.generate_synthetic import (
    CLAUDE_MODEL,
    GPT_MODEL,
    PAIRS_PER_BATCH,
    PROMPT_TEMPLATE_VERSION,
    SYNTHETIC_GENERATOR_PROMPTS,
    T04A_SCOPE_ERROR,
    main,
)


# --- dry-run plan shape ---------------------------------------------------


def test_dry_run_all_bands_prints_full_plan(capsys):
    exit_code = main(
        ["--dry-run", "--band", "all", "--n-per-band", "20", "--seed", "42"]
    )
    assert exit_code == 0
    out = capsys.readouterr().out

    # Header lines are present verbatim.
    assert f"prompt_template_version: {PROMPT_TEMPLATE_VERSION}" in out
    assert "seed: 42" in out
    assert "bands: [1, 2, 3]" in out
    assert "n_per_band: 20" in out

    # 20 pairs * 3 bands / 5 per batch = 12 batches, 24 panel_query calls,
    # 120 total PanelRequests (60 propose + 60 filter).
    assert "total_batches: 12" in out
    assert "total_panel_query_calls: 24" in out
    assert "total_panel_requests: 120" in out
    assert f"pairs_per_batch: {PAIRS_PER_BATCH}" in out


def test_dry_run_single_band_limits_plan(capsys):
    exit_code = main(
        ["--dry-run", "--band", "1", "--n-per-band", "5", "--seed", "0"]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "bands: [1]" in out
    # 5 pairs fits in one batch.
    assert "total_batches: 1" in out
    assert "total_panel_query_calls: 2" in out


# --- cross-model role alternation ----------------------------------------


def test_dry_run_role_alternation_covers_both_directions(capsys):
    # 20 pairs per band over 3 bands = 12 batches, plenty of alternation.
    main(["--dry-run", "--band", "all", "--n-per-band", "20", "--seed", "42"])
    out = capsys.readouterr().out
    claude_proposer = (
        f"proposer={CLAUDE_MODEL} filter={GPT_MODEL}"
    )
    gpt_proposer = (
        f"proposer={GPT_MODEL} filter={CLAUDE_MODEL}"
    )
    # Both directions must appear — neither provider may be the sole author.
    assert claude_proposer in out
    assert gpt_proposer in out

    # Adjacent batches must differ in proposer — verifies alternation,
    # not just that both orientations show up somewhere.
    batch_lines = [line for line in out.splitlines() if line.startswith("batch ")]
    proposers = []
    for line in batch_lines:
        if claude_proposer in line:
            proposers.append(CLAUDE_MODEL)
        else:
            assert gpt_proposer in line
            proposers.append(GPT_MODEL)
    for a, b in zip(proposers, proposers[1:]):
        assert a != b


# --- band-specific prompt phrasing ---------------------------------------


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


def test_prompts_module_constant_keys_cover_every_band():
    assert set(SYNTHETIC_GENERATOR_PROMPTS.keys()) == {1, 2, 3}


# --- error paths ---------------------------------------------------------


def test_unknown_band_exits_nonzero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--dry-run", "--band", "4", "--n-per-band", "5"])
    assert excinfo.value.code != 0
    err = capsys.readouterr().err
    assert "unknown band" in err


def test_live_mode_refuses_with_t04a_scope_error(capsys):
    exit_code = main(["--band", "all", "--n-per-band", "20"])
    assert exit_code != 0
    err = capsys.readouterr().err
    assert T04A_SCOPE_ERROR in err
    assert "live generation requires" in err


def test_non_positive_n_per_band_rejected(capsys):
    with pytest.raises(SystemExit):
        main(["--dry-run", "--band", "all", "--n-per-band", "0"])


# --- determinism ---------------------------------------------------------


def test_seed_determinism(capsys):
    main(["--dry-run", "--band", "all", "--n-per-band", "10", "--seed", "42"])
    first = capsys.readouterr().out
    main(["--dry-run", "--band", "all", "--n-per-band", "10", "--seed", "42"])
    second = capsys.readouterr().out
    assert first == second


def test_different_seeds_can_produce_different_role_ordering(capsys):
    # Not required, but verifies the seed actually feeds alternation —
    # at least one of a small sweep should differ from seed=0.
    main(["--dry-run", "--band", "1", "--n-per-band", "5", "--seed", "0"])
    baseline = capsys.readouterr().out
    any_differ = False
    for seed in (1, 2, 3, 4):
        main(["--dry-run", "--band", "1", "--n-per-band", "5", "--seed", str(seed)])
        if capsys.readouterr().out != baseline:
            any_differ = True
            break
    assert any_differ


# --- mock contract (D016): autospec binds to panel_query signature --------


def test_panel_query_autospec_matches_public_signature(monkeypatch):
    """Dry-run does not call panel_query, but when T04b lands the live
    path the test double used by this module's tests must be bound to the
    real signature. Pin that invariant now so drift is caught early."""
    mock = create_autospec(panel_query)
    monkeypatch.setattr(generate_synthetic, "panel_query", mock)
    # Confirm the autospec accepts the keyword-only surface that
    # ``panel_query`` actually exposes. Wrong kwargs would raise TypeError.
    mock([], record_writer=None, max_workers=8)
    mock.assert_called_once()


def test_dry_run_does_not_invoke_panel_query(monkeypatch):
    mock = create_autospec(panel_query)
    monkeypatch.setattr(generate_synthetic, "panel_query", mock)
    exit_code = main(["--dry-run", "--band", "all", "--n-per-band", "20"])
    assert exit_code == 0
    mock.assert_not_called()
