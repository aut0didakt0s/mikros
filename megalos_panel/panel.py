"""Public entry point composing adapters, retry, and fan-out.

panel_query is the single public composition surface of megalos_panel. It
validates that every request's ``model`` resolves to a registered adapter
up-front (so an unknown model raises ValueError deterministically, before
any worker thread starts), then fans out one worker per request. Each worker
runs ``adapter.invoke(request)`` under ``retry_with_backoff`` with the
project-wide constants (rate-limit / transient budgets and the 30-second
backoff cap). A successful worker returns ``PanelResult(selection=raw_text,
raw_response=raw_text, error=None)``; a ``PanelProviderError`` becomes
``PanelResult(selection='', raw_response='', error=str(exc))`` — the error
surface is per-request, not fatal to the batch.

If a ``record_writer`` is supplied, the function writes one JSON record per
request+result pair carrying ``request_id, model, prompt, selection,
raw_response, error, attempts, elapsed_ms, timestamp``. The writer is used
from the main thread after fan_out returns (single-writer-per-run contract —
see record.py). Attempts come from PanelProviderError.attempts on failure or
a closure-local counter on success; elapsed_ms is wall-clock monotonic time
measured around the retry invocation.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from . import adapters
from .concurrency import fan_out
from .errors import PanelProviderError
from .retry import (
    BACKOFF_CAP_SECONDS,
    RATE_LIMIT_ATTEMPTS,
    TRANSIENT_ATTEMPTS,
    retry_with_backoff,
)
from .types import PanelRequest, PanelResult

if TYPE_CHECKING:
    from .record import RecordWriter


def panel_query(
    requests: list[PanelRequest],
    *,
    record_writer: "RecordWriter | None" = None,
    max_workers: int = 8,
) -> dict[str, PanelResult]:
    """Dispatch ``requests`` across provider adapters concurrently.

    Returns a dict keyed by ``request.request_id`` with one ``PanelResult``
    per input request. Unknown models raise ``ValueError`` before any worker
    starts. Provider exhaustion surfaces as ``PanelResult.error`` rather than
    an exception, so a single bad request does not abort the batch.
    """
    if not requests:
        return {}

    # Up-front validation: resolve every model before fan-out so an unknown
    # model fails deterministically rather than racing with worker threads.
    adapter_classes: dict[str, type[adapters.Adapter]] = {}
    for req in requests:
        if req.model not in adapter_classes:
            adapter_classes[req.model] = adapters.dispatch(req.model)

    # Instantiate one adapter per distinct model (not per request) — adapter
    # construction reads env keys and builds an SDK client; reuse is cheap
    # and correct because invoke() takes the request.
    adapter_instances: dict[str, adapters.Adapter] = {
        model: cls() for model, cls in adapter_classes.items()
    }

    # Per-request metadata captured by the closures and surfaced into records.
    meta: dict[str, dict[str, object]] = {}

    def per_request(request: PanelRequest) -> PanelResult:
        adapter = adapter_instances[request.model]
        attempts = 0

        def call_once() -> str:
            nonlocal attempts
            attempts += 1
            return adapter.invoke(request)

        start = time.monotonic()
        try:
            raw_text = retry_with_backoff(
                call_once,
                rate_limit_attempts=RATE_LIMIT_ATTEMPTS,
                transient_attempts=TRANSIENT_ATTEMPTS,
                backoff_cap=BACKOFF_CAP_SECONDS,
                model=request.model,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            meta[request.request_id] = {
                "attempts": attempts,
                "elapsed_ms": elapsed_ms,
            }
            return PanelResult(
                selection=raw_text,
                raw_response=raw_text,
                error=None,
            )
        except PanelProviderError as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            meta[request.request_id] = {
                "attempts": exc.attempts,
                "elapsed_ms": elapsed_ms,
            }
            return PanelResult(
                selection="",
                raw_response="",
                error=str(exc),
            )

    results = fan_out(requests, per_request, max_workers=max_workers)

    if record_writer is not None:
        for req in requests:
            result = results[req.request_id]
            request_meta = meta.get(
                req.request_id,
                {"attempts": 0, "elapsed_ms": 0},
            )
            record_writer.write(
                {
                    "request_id": req.request_id,
                    "model": req.model,
                    "prompt": req.prompt,
                    "selection": result.selection,
                    "raw_response": result.raw_response,
                    "error": result.error,
                    "attempts": request_meta["attempts"],
                    "elapsed_ms": request_meta["elapsed_ms"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

    return results
