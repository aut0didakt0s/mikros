"""CSV schema + helpers for workflow-selection measurement outputs.

Two CSVs are produced per measurement run:

    - Per-scenario rows: one row per scenario x model attempt.
    - Summary rows: one row per (measurement_run, model) aggregate.

Column definitions live here as module-level tuples so downstream code
(runner, measure, analysis notebooks) depends on a single source of truth.
Writers use only stdlib `csv` — no pandas.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Mapping

__all__ = [
    "PER_SCENARIO_COLUMNS",
    "SUMMARY_COLUMNS",
    "read_csv_rows",
    "write_per_scenario_csv",
    "write_summary_csv",
]


PER_SCENARIO_COLUMNS: tuple[str, ...] = (
    "scenario_id",
    "band",
    "model",
    "prompt",
    "correct_selection",
    "model_selection",
    "correct",
    "attempts",
    "elapsed_ms",
    "timestamp",
)


SUMMARY_COLUMNS: tuple[str, ...] = (
    "measurement_run_id",
    "model",
    "scenario_count",
    "successes",
    "raw_accuracy",
    "kappa",
    "wilson_lower",
    "synth_kappa_per_scenario",
    "gap_pp",
    "gate_live_pass",
    "gate_gap_pass",
    "overall_pass",
    "timestamp",
)


def write_per_scenario_csv(
    path: str | Path, rows: Iterable[Mapping[str, object]]
) -> None:
    """Write per-scenario rows to `path`. Header matches PER_SCENARIO_COLUMNS."""
    _write_csv(path, PER_SCENARIO_COLUMNS, rows)


def write_summary_csv(
    path: str | Path, rows: Iterable[Mapping[str, object]]
) -> None:
    """Write summary rows to `path`. Header matches SUMMARY_COLUMNS."""
    _write_csv(path, SUMMARY_COLUMNS, rows)


def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    """Read a CSV back as a list of dicts (strings only; caller re-types)."""
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_csv(
    path: str | Path,
    columns: tuple[str, ...],
    rows: Iterable[Mapping[str, object]],
) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            missing = [c for c in columns if c not in row]
            if missing:
                raise ValueError(
                    f"row missing required columns: {sorted(missing)}"
                )
            extra = [k for k in row.keys() if k not in columns]
            if extra:
                raise ValueError(
                    f"row carries unknown columns: {sorted(extra)}"
                )
            writer.writerow(dict(row))
