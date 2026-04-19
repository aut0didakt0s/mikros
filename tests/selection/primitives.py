"""Analysis primitives for workflow-selection measurement.

This module implements the statistical primitives used to evaluate whether a
workflow-selection measurement run passes the shipping gate.

Derivations
-----------

Wilson score lower bound (95% two-sided, z = 1.96):

    lower = (p + z^2/(2n) - z * sqrt(p(1-p)/n + z^2/(4n^2))) / (1 + z^2/n)

Cohen's kappa (binary / binary-like collapse):

    kappa = (p - chance) / (1 - chance)

Per-pair -> per-scenario kappa rescaling (per decision D018):

The measurement pipeline collects two kinds of accuracy estimates that live
in different kappa spaces:

    - Per-scenario live runs choose 1-of-N workflows; chance = 1/N.
    - Per-pair synthetic diagnostics pick between two candidates; chance = 1/2.

Uncorrected raw-accuracy comparison conflates those baselines. We rescale the
per-pair kappa into per-scenario-equivalent kappa before gap arithmetic:

    1. Recover raw per-pair accuracy:     p_pair = 0.5 + 0.5 * kappa_pair
    2. Treat p_pair as the honest per-scenario accuracy estimate. The
       assumption is that a model's discrimination skill transfers across
       the unit of measurement (pair vs scenario); D018 accepts this as the
       working convention.
    3. Re-apply the kappa formula with per-scenario chance = 1/n_categories
       (default 6 for the current catalog size):

           kappa_per_scenario = (p_pair - 1/n_categories) / (1 - 1/n_categories)

Gate thresholds (per D018)
--------------------------

    - Live-catalog Wilson CI lower bound (on raw accuracy) >= 90%, N >= 60.
    - Chance-adjusted gap (live_kappa - synth_kappa_per_scenario) <= 25 pp.

GATE_LIVE_KAPPA_FLOOR arithmetic:

    floor_raw = 0.90
    chance   = 1 / 6
    floor    = (0.90 - 1/6) / (1 - 1/6)
             = 0.733333... / 0.833333...
             ~= 0.88

We keep both the raw-accuracy Wilson CI (the authoritative D018 form) and the
kappa-floor projection; the raw form is what the gate reports, the kappa form
is the chance-adjusted diagnostic.
"""

from __future__ import annotations

import math

__all__ = [
    "GATE_GAP_MAX_PP",
    "GATE_LIVE_KAPPA_FLOOR",
    "GATE_LIVE_RAW_FLOOR",
    "cohens_kappa_binary",
    "evaluate_gate",
    "gap_in_per_scenario_kappa",
    "rescale_per_pair_to_per_scenario",
    "wilson_ci_lower",
]


# --- Gate constants (D018) -------------------------------------------------

# Wilson CI lower bound on raw per-scenario accuracy must clear this floor.
GATE_LIVE_RAW_FLOOR: float = 0.90

# Chance-adjusted kappa floor implied by the raw floor @ n_categories = 6.
# (0.90 - 1/6) / (1 - 1/6) = 0.733333... / 0.833333... ~= 0.88
GATE_LIVE_KAPPA_FLOOR: float = (0.90 - 1.0 / 6.0) / (1.0 - 1.0 / 6.0)

# Maximum allowed gap, in percentage points, between live per-scenario kappa
# and synthetic per-scenario-rescaled kappa.
GATE_GAP_MAX_PP: float = 25.0


# --- Primitive functions ---------------------------------------------------


def wilson_ci_lower(successes: int, n: int, confidence: float = 0.95) -> float:
    """Wilson score interval lower bound for a binomial proportion.

    Args:
        successes: number of observed successes (0 <= successes <= n).
        n: number of trials (n >= 1).
        confidence: two-sided confidence level; only 0.95 is supported
            (D018 fixes this). Other values raise NotImplementedError.

    Returns:
        Lower bound of the Wilson score interval for the success proportion.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if successes < 0 or successes > n:
        raise ValueError("successes must satisfy 0 <= successes <= n")
    if confidence != 0.95:
        raise NotImplementedError(
            "wilson_ci_lower only supports confidence=0.95 (z=1.96); "
            "D018 fixes the measurement convention at 95%."
        )

    z = 1.96
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = p + z2 / (2.0 * n)
    margin = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return (centre - margin) / denom


def cohens_kappa_binary(successes: int, n: int, chance_rate: float) -> float:
    """Cohen's kappa for a collapsed correct/incorrect outcome.

    Args:
        successes: number of correct outcomes.
        n: number of trials.
        chance_rate: baseline accuracy expected by chance (e.g. 1/6 for
            per-scenario 6-way, 0.5 for per-pair forced choice).

    Returns:
        Chance-adjusted agreement: (p - chance) / (1 - chance).
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0.0 <= chance_rate < 1.0:
        raise ValueError("chance_rate must be in [0, 1)")
    p = successes / n
    return (p - chance_rate) / (1.0 - chance_rate)


def rescale_per_pair_to_per_scenario(
    kappa_pair: float, n_categories: int = 6
) -> float:
    """Rescale a per-pair kappa (chance 0.5) to a per-scenario-equivalent kappa.

    Honesty assumption (see module docstring + D018): the per-pair raw
    accuracy recovered from kappa is reused as the per-scenario raw
    accuracy, i.e. discrimination skill transfers across the unit of
    measurement. This is the working convention, not an established fact.
    """
    if n_categories < 2:
        raise ValueError("n_categories must be >= 2")
    p_pair = 0.5 + 0.5 * kappa_pair
    chance = 1.0 / n_categories
    return (p_pair - chance) / (1.0 - chance)


def gap_in_per_scenario_kappa(
    live_successes: int,
    live_n: int,
    synth_pair_successes: int,
    synth_pair_n: int,
    n_categories: int = 6,
) -> dict:
    """Compute the chance-adjusted gap between live and synthetic kappas.

    Returns a dict with live kappa, synth per-pair kappa, synth rescaled
    per-scenario kappa, and the gap expressed in percentage points.
    """
    live_kappa = cohens_kappa_binary(live_successes, live_n, 1.0 / n_categories)
    synth_kappa_pair = cohens_kappa_binary(synth_pair_successes, synth_pair_n, 0.5)
    synth_kappa_per_scenario = rescale_per_pair_to_per_scenario(
        synth_kappa_pair, n_categories=n_categories
    )
    gap_pp = (live_kappa - synth_kappa_per_scenario) * 100.0
    return {
        "live_kappa": live_kappa,
        "synth_kappa_pair": synth_kappa_pair,
        "synth_kappa_per_scenario": synth_kappa_per_scenario,
        "gap_pp": gap_pp,
    }


def evaluate_gate(
    live_successes: int,
    live_n: int,
    synth_pair_successes: int,
    synth_pair_n: int,
    n_categories: int = 6,
) -> dict:
    """Apply the D018 gate to a measurement run.

    Gate criteria:
        1. Wilson CI lower bound on live raw accuracy >= GATE_LIVE_RAW_FLOOR.
        2. (live_kappa - synth_kappa_per_scenario) * 100 <= GATE_GAP_MAX_PP.

    Returns a dict of the measured quantities plus three booleans:
        gate_live_pass, gate_gap_pass, overall_pass.
    """
    wilson_lower_raw = wilson_ci_lower(live_successes, live_n)
    live_kappa = cohens_kappa_binary(live_successes, live_n, 1.0 / n_categories)
    synth_kappa_pair = cohens_kappa_binary(synth_pair_successes, synth_pair_n, 0.5)
    synth_kappa_per_scenario = rescale_per_pair_to_per_scenario(
        synth_kappa_pair, n_categories=n_categories
    )
    gap_pp = (live_kappa - synth_kappa_per_scenario) * 100.0

    gate_live_pass = wilson_lower_raw >= GATE_LIVE_RAW_FLOOR
    gate_gap_pass = gap_pp <= GATE_GAP_MAX_PP
    overall_pass = gate_live_pass and gate_gap_pass

    return {
        "live_wilson_lower_raw": wilson_lower_raw,
        "live_kappa_estimate": live_kappa,
        "synth_kappa_per_scenario": synth_kappa_per_scenario,
        "gap_pp": gap_pp,
        "gate_live_pass": gate_live_pass,
        "gate_gap_pass": gate_gap_pass,
        "overall_pass": overall_pass,
    }


