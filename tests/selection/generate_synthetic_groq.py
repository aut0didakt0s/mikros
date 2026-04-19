"""Synthetic workflow-selection fixture generator — single-model authorship.

Purpose
-------

Sibling of ``tests.selection.generate_synthetic`` (the cross-model generator
kept on disk as archived institutional memory from the D014 era). This
module implements the single-model authoring protocol adopted in D026: one
panel model authors each candidate pair in a single ``panel_query`` round,
with no propose/filter split. Default authoring model is
``groq/llama-3.3-70b-versatile`` (D026), but the generator itself is
provider-agnostic — the operator may pass any model string registered in
``megalos_panel.adapters`` via ``--authoring-model``.

Only the **dry-run** code path is exercised here. Live generation is the
responsibility of the downstream live-run task; invoking this script
without ``--dry-run`` exits non-zero with a scoped error directing the
operator there.

Contract tie-in
---------------

Prompt text, prompt-template version, valid-band set, and band parsing
are imported from the cross-model generator module. That keeps both
generators on the same prompt revision so comparisons stay honest, and
avoids the drift trap of two prompt copies. Panel calls flow through
``megalos_panel.panel_query`` with ``PanelRequest`` instances; adapters
are never imported here. Tests pin the mock to the public surface via
``unittest.mock.create_autospec(panel_query)``.

The dry-run plan header keys mirror the cross-model generator's header
exactly so the downstream concordance-gate parser can read either
generator's output without special-casing. Per-batch lines carry
``authoring_model=<model>`` in place of the cross-model ``proposer=... filter=...``
field pair; every other header field is byte-compatible.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import TextIO

from megalos_panel import PanelRequest, panel_query
from tests.selection.generate_synthetic import (
    PROMPT_TEMPLATE_VERSION,
    SYNTHETIC_GENERATOR_PROMPTS,
    VALID_BANDS,
    _expand_bands,
)

# Default authoring model per D026. The ``groq/`` prefix routes through
# the GroqAdapter registered in ``megalos_panel.adapters``. Overridable
# via ``--authoring-model`` — the generator itself is prefix-agnostic.
DEFAULT_AUTHORING_MODEL = "groq/llama-3.3-70b-versatile"

# Number of candidate pairs requested per panel_query batch. Matches
# the cross-model generator so batch arithmetic is identical.
PAIRS_PER_BATCH = 5


SCOPE_ERROR = (
    "Scope error: live generation requires provider keys and is the "
    "live-run task's responsibility. Use --dry-run to verify the code "
    "path, or dispatch the live-run task for live execution."
)


@dataclass(frozen=True)
class BatchPlan:
    """One batch of ``PAIRS_PER_BATCH`` candidate pairs for a single band.

    Single-model variant: one ``panel_query`` call per batch against
    ``authoring_model``, producing ``pairs`` candidate pairs. No filter
    round — the operator-authored anchors (T02) remain the scoring
    ground truth.
    """

    band: int
    batch_index: int
    pairs: int
    authoring_model: str
    authoring_prompt: str


def _plan_batches(
    bands: list[int], n_per_band: int, authoring_model: str
) -> list[BatchPlan]:
    """Expand (bands, n_per_band) into an ordered list of BatchPlan entries.

    Deterministic and seed-independent: single-model authorship has no
    role alternation to randomize. ``seed`` is still accepted at the CLI
    for symmetry with the cross-model generator and for future use (e.g.
    shuffling prompt variants) but does not affect the plan today.
    """
    planned: list[BatchPlan] = []
    global_batch_index = 0
    for band in bands:
        remaining = n_per_band
        while remaining > 0:
            size = min(PAIRS_PER_BATCH, remaining)
            planned.append(
                BatchPlan(
                    band=band,
                    batch_index=global_batch_index,
                    pairs=size,
                    authoring_model=authoring_model,
                    authoring_prompt=SYNTHETIC_GENERATOR_PROMPTS[band],
                )
            )
            remaining -= size
            global_batch_index += 1
    return planned


def _render_dry_run_plan(
    batches: list[BatchPlan],
    *,
    n_per_band: int,
    bands: list[int],
    seed: int,
    run_id: str | None,
    authoring_model: str,
    out: TextIO,
) -> None:
    """Print the execution plan in a shape compatible with the cross-model
    generator's output. Header keys match byte-for-byte so the downstream
    parser can read either generator's dry-run output without branching.
    Per-batch lines carry ``authoring_model`` in place of the cross-model
    ``proposer``/``filter`` pair.
    """
    total_calls = len(batches)  # one call per batch in the single-model path
    total_requests = sum(b.pairs for b in batches)
    print(f"prompt_template_version: {PROMPT_TEMPLATE_VERSION}", file=out)
    print(f"run_id: {run_id or '<unset>'}", file=out)
    print(f"seed: {seed}", file=out)
    print(f"bands: {bands}", file=out)
    print(f"n_per_band: {n_per_band}", file=out)
    print(f"authoring_model: {authoring_model}", file=out)
    print(f"total_batches: {len(batches)}", file=out)
    print(f"total_panel_query_calls: {total_calls}", file=out)
    print(f"total_panel_requests: {total_requests}", file=out)
    print(f"pairs_per_batch: {PAIRS_PER_BATCH}", file=out)
    print("", file=out)
    for batch in batches:
        print(
            f"batch {batch.batch_index}: band={batch.band} pairs={batch.pairs} "
            f"authoring_model={batch.authoring_model}",
            file=out,
        )
        print(f"  authoring_prompt: {batch.authoring_prompt}", file=out)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate_synthetic_groq",
        description=(
            "Author synthetic workflow-selection pairs under the "
            "single-model authoring protocol. Dry-run only in this task scope."
        ),
    )
    parser.add_argument(
        "--band",
        default="all",
        help="Closeness band: 1, 2, 3, or 'all' (default: all).",
    )
    parser.add_argument(
        "--n-per-band",
        type=int,
        default=20,
        help="Number of pairs to author per band (default: 20).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the execution plan without invoking panel_query.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Reserved for future use; recorded in the plan header (default: 0).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional run identifier recorded in the plan header.",
    )
    parser.add_argument(
        "--authoring-model",
        default=DEFAULT_AUTHORING_MODEL,
        help=(
            "Panel model string used to author each candidate pair "
            f"(default: {DEFAULT_AUTHORING_MODEL}). Any prefix registered "
            "in megalos_panel.adapters is accepted."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code.

    ``argv`` defaults to ``sys.argv[1:]`` when omitted so the tests can
    drive the parser without spawning subprocesses.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.n_per_band <= 0:
        parser.error("--n-per-band must be positive")

    try:
        bands = _expand_bands(args.band)
    except ValueError as exc:
        parser.error(str(exc))

    if not args.dry_run:
        # Hard gate: this module is sandbox-safe. Live generation is the
        # live-run task's job.
        print(SCOPE_ERROR, file=sys.stderr)
        return 2

    batches = _plan_batches(bands, args.n_per_band, args.authoring_model)
    _render_dry_run_plan(
        batches,
        n_per_band=args.n_per_band,
        bands=bands,
        seed=args.seed,
        run_id=args.run_id,
        authoring_model=args.authoring_model,
        out=sys.stdout,
    )

    # ``panel_query`` and ``PanelRequest`` are imported at module scope so
    # the dry-run path exercises the import contract even when no call is
    # made. Reference them here so static analyzers don't mark them unused
    # and so the read is visible in coverage. ``VALID_BANDS`` similarly
    # anchors the import from the cross-model module.
    _ = (panel_query, PanelRequest, VALID_BANDS)

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI dispatch
    raise SystemExit(main())
