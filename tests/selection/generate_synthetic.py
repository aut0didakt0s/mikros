"""Synthetic workflow-selection fixture generator (CLI, dry-run only in T04a scope).

Purpose
-------

Author synthetic workflow pairs for the selection-measurement slice. Each pair
carries a closeness band (1, 2, or 3) and is produced under the cross-model
authoring protocol defined in ``docs/selection_authoring_protocol.md``: for
every candidate pair one panel model *proposes* the pair, and a different
panel model *filters* (accepts / rejects / rewrites for band-fit). Roles
alternate per batch so the authoring load is balanced across providers.

Only the **dry-run** code path is exercised in the sandbox-executable half
of the fixture-generator task. Invoking this script without ``--dry-run``
exits non-zero with a scoped error directing the operator to the live-run
task. The dry-run prints the full execution plan — prompts, batch
structure, panel_query call counts, and per-batch role assignments — so
the generator code path can be verified end-to-end without API keys.

Contract tie-in
---------------

Panel calls flow through ``megalos_panel.panel_query`` with
``PanelRequest`` instances. Adapters are **not** imported from this
module; the generator is provider-agnostic and speaks only the panel's
public surface. Tests pin the mock to that surface via
``unittest.mock.create_autospec(panel_query)``.
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from typing import TextIO

from megalos_panel import PanelRequest, panel_query

# Versioned so fixture metadata sidecars can pin the prompt revision that
# produced a given pair. Bump on any substantive change to prompt text.
PROMPT_TEMPLATE_VERSION = "generator-v1"

# Panel model identifiers — must match the prefixes registered in
# ``megalos_panel.adapters`` (``claude-*`` dispatches the Anthropic adapter,
# ``gpt-*`` the OpenAI one). Hard-coded here so the dry-run plan is
# deterministic and the tests can pin exact model strings.
CLAUDE_MODEL = "claude-opus-4-7"
GPT_MODEL = "gpt-4o"

# Number of candidate pairs requested per panel_query batch. A single
# generator run is sharded into batches so the panel's concurrent fan-out
# is bounded and role alternation has a natural grain.
PAIRS_PER_BATCH = 5

# Valid band codes. ``"all"`` expands to every band at CLI parse time.
VALID_BANDS: tuple[int, ...] = (1, 2, 3)


SYNTHETIC_GENERATOR_PROMPTS: dict[int, str] = {
    1: (
        "Propose a pair of workflow descriptions for closeness Band 1 "
        "(low closeness). The two workflows MUST differ in multiple "
        "markers across lexical and semantic dimensions — distinct "
        "domains, distinct outputs, distinct operations. A competent "
        "selector should find the choice near-trivial. Produce "
        "description_A and description_B, each one short paragraph."
    ),
    2: (
        "Propose a pair of workflow descriptions for closeness Band 2 "
        "(mid closeness). The two workflows belong to the same broad "
        "category and share most content; only a few markers distinguish "
        "them. Most phrases could appear in either description. Produce "
        "description_A and description_B, each one short paragraph."
    ),
    3: (
        "Propose a pair of workflow descriptions for closeness Band 3 "
        "(high closeness, near-identity). The descriptions overlap "
        "near-entirely; a single minimal marker distinguishes one from "
        "the other. Flip that one marker and the pair becomes "
        "indistinguishable. Produce description_A and description_B, "
        "each one short paragraph."
    ),
}


T04A_SCOPE_ERROR = (
    "T04a scope error: live generation requires provider keys and is "
    "T04b's responsibility. Use --dry-run to verify the code path, or "
    "dispatch T04b for live execution."
)


@dataclass(frozen=True)
class BatchPlan:
    """One batch of ``PAIRS_PER_BATCH`` candidate pairs for a single band.

    Each batch is two panel_query rounds:
      1. ``proposer_model`` proposes ``pairs`` candidate pairs.
      2. ``filter_model`` filters each proposed pair for band-fit.

    The two models are always distinct (D014 cross-model rule), and the
    proposer/filter assignment alternates from batch to batch so neither
    provider dominates authorship.
    """

    band: int
    batch_index: int
    pairs: int
    proposer_model: str
    filter_model: str
    propose_prompt: str
    filter_prompt: str


def _filter_prompt_for(band: int) -> str:
    """Prompt used on the filter leg of a batch.

    Filtering is explicitly band-anchored: the filter model evaluates
    whether the candidate pair matches the band's marker profile, and
    rewrites the weaker side if not. The wording is deliberately a
    function of the same band spec so drift stays visible.
    """
    base = SYNTHETIC_GENERATOR_PROMPTS[band]
    return (
        f"Filter the candidate pair against this specification:\n\n"
        f"{base}\n\n"
        f"If the candidate meets the specification, emit it unchanged. "
        f"Otherwise rewrite the weaker side until it does."
    )


def _plan_batches(bands: list[int], n_per_band: int, seed: int) -> list[BatchPlan]:
    """Expand (bands, n_per_band) into an ordered list of BatchPlan entries.

    Role alternation is deterministic in ``seed`` — a global batch counter
    (not per-band) drives the Claude↔GPT swap, so across a full run each
    provider appears in both roles. Seed only affects the role ordering;
    the prompts themselves are fixed strings keyed on band.
    """
    rng = random.Random(seed)
    # Start with a seeded coin flip so seed changes produce a different
    # role ordering but the alternation step stays mechanical.
    claude_first = rng.random() < 0.5

    planned: list[BatchPlan] = []
    global_batch_index = 0
    for band in bands:
        remaining = n_per_band
        while remaining > 0:
            size = min(PAIRS_PER_BATCH, remaining)
            if (global_batch_index % 2 == 0) == claude_first:
                proposer, filt = CLAUDE_MODEL, GPT_MODEL
            else:
                proposer, filt = GPT_MODEL, CLAUDE_MODEL
            planned.append(
                BatchPlan(
                    band=band,
                    batch_index=global_batch_index,
                    pairs=size,
                    proposer_model=proposer,
                    filter_model=filt,
                    propose_prompt=SYNTHETIC_GENERATOR_PROMPTS[band],
                    filter_prompt=_filter_prompt_for(band),
                )
            )
            remaining -= size
            global_batch_index += 1
    return planned


def _expand_bands(arg: str) -> list[int]:
    """Parse the ``--band`` CLI value into an ordered list of bands."""
    if arg == "all":
        return list(VALID_BANDS)
    try:
        band = int(arg)
    except ValueError as exc:
        raise ValueError(f"unknown band: {arg!r}") from exc
    if band not in VALID_BANDS:
        raise ValueError(f"unknown band: {arg!r}")
    return [band]


def _render_dry_run_plan(
    batches: list[BatchPlan],
    *,
    n_per_band: int,
    bands: list[int],
    seed: int,
    run_id: str | None,
    out: TextIO,
) -> None:
    """Print the execution plan in a shape the tests can assert on.

    Each batch prints its index, band, proposer/filter assignment, the
    PanelRequest count that would be emitted on each leg, and the
    prompt text (truncated-free — full text, because the tests match
    band-specific phrases exactly).
    """
    total_calls = len(batches) * 2  # one propose + one filter per batch
    total_requests = sum(b.pairs for b in batches) * 2
    print(f"prompt_template_version: {PROMPT_TEMPLATE_VERSION}", file=out)
    print(f"run_id: {run_id or '<unset>'}", file=out)
    print(f"seed: {seed}", file=out)
    print(f"bands: {bands}", file=out)
    print(f"n_per_band: {n_per_band}", file=out)
    print(f"total_batches: {len(batches)}", file=out)
    print(f"total_panel_query_calls: {total_calls}", file=out)
    print(f"total_panel_requests: {total_requests}", file=out)
    print(f"pairs_per_batch: {PAIRS_PER_BATCH}", file=out)
    print("", file=out)
    for batch in batches:
        print(
            f"batch {batch.batch_index}: band={batch.band} pairs={batch.pairs} "
            f"proposer={batch.proposer_model} filter={batch.filter_model}",
            file=out,
        )
        print(f"  propose_prompt: {batch.propose_prompt}", file=out)
        print(f"  filter_prompt: {batch.filter_prompt}", file=out)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate_synthetic",
        description=(
            "Author synthetic workflow-selection pairs under the "
            "cross-model generator protocol. Dry-run only in this task scope."
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
        help="Seed for role-alternation ordering (default: 0).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional run identifier recorded in the plan header.",
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
        # Hard gate: T04a is sandbox-safe. Live generation is T04b.
        print(T04A_SCOPE_ERROR, file=sys.stderr)
        return 2

    batches = _plan_batches(bands, args.n_per_band, args.seed)
    _render_dry_run_plan(
        batches,
        n_per_band=args.n_per_band,
        bands=bands,
        seed=args.seed,
        run_id=args.run_id,
        out=sys.stdout,
    )

    # ``panel_query`` and ``PanelRequest`` are imported at module scope so
    # the dry-run path exercises the import contract even when no call is
    # made. Reference them here so static analyzers don't mark them unused
    # and so the read is visible in coverage.
    _ = (panel_query, PanelRequest)

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI dispatch
    raise SystemExit(main())
