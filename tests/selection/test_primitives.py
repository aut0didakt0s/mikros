"""Unit tests for workflow-selection analysis primitives and CSV schema."""

from __future__ import annotations

import math

import pytest  # type: ignore[import-not-found]

from tests.selection.csv_schema import (
    PER_SCENARIO_COLUMNS,
    SUMMARY_COLUMNS,
    read_csv_rows,
    write_per_scenario_csv,
    write_summary_csv,
)
from tests.selection.primitives import (
    GATE_GAP_MAX_PP,
    GATE_LIVE_KAPPA_FLOOR,
    GATE_LIVE_RAW_FLOOR,
    cohens_kappa_binary,
    evaluate_gate,
    gap_in_per_scenario_kappa,
    rescale_per_pair_to_per_scenario,
    wilson_ci_lower,
)


# --- Wilson CI -------------------------------------------------------------


def test_wilson_lower_known_case_90_of_100():
    # Standard reference value: p=0.90, n=100, 95% two-sided -> ~0.825.
    lower = wilson_ci_lower(90, 100)
    assert 0.82 < lower < 0.83
    assert math.isclose(lower, 0.8256, abs_tol=0.002)


def test_wilson_lower_perfect_run_bounded_below_one():
    # p = 1.0 case: Wilson gives a meaningful lower bound < 1.
    lower = wilson_ci_lower(20, 20)
    assert 0.0 < lower < 1.0


def test_wilson_lower_zero_successes_is_zero_floor():
    # p = 0.0 case: lower bound should be 0 (cannot go negative).
    lower = wilson_ci_lower(0, 50)
    assert lower >= 0.0
    assert lower < 0.05


def test_wilson_lower_rejects_bad_inputs():
    with pytest.raises(ValueError):
        wilson_ci_lower(10, 0)
    with pytest.raises(ValueError):
        wilson_ci_lower(-1, 10)
    with pytest.raises(ValueError):
        wilson_ci_lower(11, 10)
    with pytest.raises(NotImplementedError):
        wilson_ci_lower(9, 10, confidence=0.99)


# --- Cohen's kappa ---------------------------------------------------------


def test_cohens_kappa_binary_known_case_p90_chance_one_sixth():
    # Matches the GATE_LIVE_KAPPA_FLOOR derivation: (0.90 - 1/6) / (5/6) ~= 0.88.
    kappa = cohens_kappa_binary(90, 100, 1.0 / 6.0)
    assert math.isclose(kappa, 0.88, abs_tol=0.005)


def test_cohens_kappa_at_chance_rate_is_zero():
    # p == chance => kappa == 0.
    kappa = cohens_kappa_binary(50, 100, 0.5)
    assert math.isclose(kappa, 0.0, abs_tol=1e-12)


def test_cohens_kappa_perfect_is_one():
    assert math.isclose(cohens_kappa_binary(100, 100, 0.5), 1.0)


def test_cohens_kappa_rejects_bad_inputs():
    with pytest.raises(ValueError):
        cohens_kappa_binary(5, 0, 0.5)
    with pytest.raises(ValueError):
        cohens_kappa_binary(5, 10, 1.0)


# --- Rescaling -------------------------------------------------------------


def test_rescale_per_pair_to_per_scenario_known_case():
    # kappa_pair = 0.40 -> p_pair = 0.70 -> per-scenario kappa @ n=6:
    # (0.70 - 1/6) / (5/6) = 0.5333 / 0.8333 = 0.64
    result = rescale_per_pair_to_per_scenario(0.40, n_categories=6)
    assert math.isclose(result, 0.64, abs_tol=0.005)


def test_rescale_round_trip_via_raw_accuracy():
    # Going kappa_pair -> p_pair and independently computing per-scenario kappa
    # from the same p should match the rescaler's output.
    kappa_pair = 0.55
    p_pair = 0.5 + 0.5 * kappa_pair
    chance_scenario = 1.0 / 6.0
    expected = (p_pair - chance_scenario) / (1.0 - chance_scenario)
    assert math.isclose(
        rescale_per_pair_to_per_scenario(kappa_pair, n_categories=6),
        expected,
        abs_tol=1e-12,
    )


def test_rescale_rejects_bad_n_categories():
    with pytest.raises(ValueError):
        rescale_per_pair_to_per_scenario(0.4, n_categories=1)


# --- Gap arithmetic --------------------------------------------------------


def test_gap_compute_hand_calc():
    # live: p=0.90, chance=1/6 => kappa=0.88
    # synth pair: p=0.70, chance=0.5 => kappa_pair=0.40
    # synth per-scenario: rescale(0.40, 6) = 0.64
    # gap_pp = (0.88 - 0.64) * 100 = 24
    result = gap_in_per_scenario_kappa(
        live_successes=90,
        live_n=100,
        synth_pair_successes=70,
        synth_pair_n=100,
        n_categories=6,
    )
    assert math.isclose(result["live_kappa"], 0.88, abs_tol=0.005)
    assert math.isclose(result["synth_kappa_pair"], 0.40, abs_tol=1e-12)
    assert math.isclose(result["synth_kappa_per_scenario"], 0.64, abs_tol=0.005)
    assert math.isclose(result["gap_pp"], 24.0, abs_tol=0.5)


# --- Gate constants --------------------------------------------------------


def test_gate_constants_match_d018():
    assert GATE_LIVE_RAW_FLOOR == 0.90
    assert math.isclose(GATE_LIVE_KAPPA_FLOOR, 0.88, abs_tol=0.005)
    assert GATE_GAP_MAX_PP == 25.0


# --- evaluate_gate ---------------------------------------------------------


def test_evaluate_gate_pass_case():
    # live: 190/200 @ p=0.95 -> Wilson lower ~0.91, clears 0.90 floor.
    # synth_pair: 90/100 -> kappa_pair=0.80 -> per-scenario kappa ~0.88.
    # live kappa ~0.94, gap_pp ~6 -> both checks pass.
    result = evaluate_gate(
        live_successes=190,
        live_n=200,
        synth_pair_successes=90,
        synth_pair_n=100,
        n_categories=6,
    )
    assert result["gate_live_pass"] is True
    assert result["gate_gap_pass"] is True
    assert result["overall_pass"] is True
    assert result["live_wilson_lower_raw"] >= GATE_LIVE_RAW_FLOOR
    assert result["gap_pp"] <= GATE_GAP_MAX_PP


def test_evaluate_gate_live_fail_case():
    # live: 80/100 @ p=0.80 -> Wilson lower ~0.71, fails the 0.90 floor.
    result = evaluate_gate(
        live_successes=80,
        live_n=100,
        synth_pair_successes=90,
        synth_pair_n=100,
        n_categories=6,
    )
    assert result["gate_live_pass"] is False
    assert result["overall_pass"] is False


def test_evaluate_gate_gap_fail_case():
    # live: 190/200 passes live gate; synth_pair 60/100 -> kappa_pair=0.20
    # -> per-scenario kappa ~0.52; gap ~42pp -> fails gap gate.
    result = evaluate_gate(
        live_successes=190,
        live_n=200,
        synth_pair_successes=60,
        synth_pair_n=100,
        n_categories=6,
    )
    assert result["gate_live_pass"] is True
    assert result["gate_gap_pass"] is False
    assert result["overall_pass"] is False
    assert result["gap_pp"] > GATE_GAP_MAX_PP


def test_evaluate_gate_return_shape():
    # All advertised keys must be present on every call.
    result = evaluate_gate(190, 200, 90, 100)
    expected_keys = {
        "live_wilson_lower_raw",
        "live_kappa_estimate",
        "synth_kappa_per_scenario",
        "gap_pp",
        "gate_live_pass",
        "gate_gap_pass",
        "overall_pass",
    }
    assert set(result.keys()) == expected_keys


# --- CSV schema ------------------------------------------------------------


def _sample_per_scenario_row(scenario_id: str = "scn-001") -> dict:
    return {
        "scenario_id": scenario_id,
        "band": "A",
        "model": "claude",
        "prompt": "hello world",
        "correct_selection": "workflow_a",
        "model_selection": "workflow_a",
        "correct": "1",
        "attempts": "1",
        "elapsed_ms": "1234",
        "timestamp": "2026-04-19T00:00:00Z",
    }


def _sample_summary_row() -> dict:
    return {
        "measurement_run_id": "run-abc",
        "model": "claude",
        "scenario_count": "200",
        "successes": "190",
        "raw_accuracy": "0.95",
        "kappa": "0.94",
        "wilson_lower": "0.91",
        "synth_kappa_per_scenario": "0.88",
        "gap_pp": "6.0",
        "gate_live_pass": "true",
        "gate_gap_pass": "true",
        "overall_pass": "true",
        "timestamp": "2026-04-19T00:00:00Z",
    }


def test_per_scenario_csv_round_trip(tmp_path):
    rows = [
        _sample_per_scenario_row("scn-001"),
        _sample_per_scenario_row("scn-002"),
    ]
    path = tmp_path / "per_scenario.csv"
    write_per_scenario_csv(path, rows)
    read_back = read_csv_rows(path)
    assert len(read_back) == 2
    assert read_back[0]["scenario_id"] == "scn-001"
    assert read_back[1]["scenario_id"] == "scn-002"
    # Column set must match the declared schema exactly.
    assert set(read_back[0].keys()) == set(PER_SCENARIO_COLUMNS)


def test_summary_csv_round_trip(tmp_path):
    path = tmp_path / "summary.csv"
    write_summary_csv(path, [_sample_summary_row()])
    read_back = read_csv_rows(path)
    assert len(read_back) == 1
    assert read_back[0]["measurement_run_id"] == "run-abc"
    assert set(read_back[0].keys()) == set(SUMMARY_COLUMNS)


def test_csv_writer_rejects_missing_columns(tmp_path):
    row = _sample_per_scenario_row()
    del row["band"]
    with pytest.raises(ValueError):
        write_per_scenario_csv(tmp_path / "bad.csv", [row])


def test_csv_writer_rejects_unknown_columns(tmp_path):
    row = _sample_per_scenario_row()
    row["extra_field"] = "nope"
    with pytest.raises(ValueError):
        write_per_scenario_csv(tmp_path / "bad.csv", [row])
