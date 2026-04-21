"""Tests for megalos_server.mcp_client.

Covers each ``CallOutcome`` variant, structured-log fields, and cold-start
latency recording. The stub FastMCP server (T02 fixture) supplies the
happy path and most error shapes; ``ProtocolError`` paths that the stub
cannot produce (non-text content, malformed envelopes) are covered via
mock injection.
"""

from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path
from typing import Any

import pytest  # type: ignore[import-not-found]

from megalos_server import mcp_client
from megalos_server.mcp_client import (
    CallOutcome,
    Ok,
    ProtocolError,
    SchemaValidationError,
    TimeoutError as McpTimeoutError,
    ToolExecutionError,
    TransportError,
)
from megalos_server.mcp_registry import AuthConfig, Registry, ServerConfig
from tests.fixtures.mcp_stub import mcp_stub_server  # noqa: F401


# --- Latency recording -----------------------------------------------------
#
# A single module-level list accumulates duration_ms from every ``Ok``
# outcome produced in this file. A session-scoped finalizer writes one
# JSONL file and emits p50/p95/max. This keeps the measurement machinery
# local to T03 — no cross-file coupling, no plugin layer.

_LATENCY_SAMPLES: list[dict[str, Any]] = []
_LATENCY_PATH = Path("runs/m006_s01_t03_latency.jsonl")


def _record_if_ok(outcome: CallOutcome, test_name: str) -> None:
    if isinstance(outcome, Ok):
        _LATENCY_SAMPLES.append(
            {"test_name": test_name, "duration_ms": outcome.duration_ms}
        )


@pytest.fixture(scope="module", autouse=True)
def _flush_latency_jsonl() -> Any:
    """Write collected Ok latencies to JSONL and emit p50/p95/max summary."""
    yield
    if not _LATENCY_SAMPLES:
        return
    _LATENCY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LATENCY_PATH.open("w", encoding="utf-8") as fh:
        for sample in _LATENCY_SAMPLES:
            fh.write(json.dumps(sample) + "\n")

    durations = sorted(s["duration_ms"] for s in _LATENCY_SAMPLES)
    p50 = statistics.median(durations)
    # Nearest-rank p95: index ceil(0.95 * n) - 1, clamped to [0, n-1].
    # Avoids statistics.quantiles' linear interpolation producing a p95
    # above the observed max with small sample counts.
    mx = durations[-1]
    import math

    p95_idx = max(0, min(len(durations) - 1, math.ceil(0.95 * len(durations)) - 1))
    p95 = durations[p95_idx]
    print(
        f"\n[mcp_client cold-start latency] n={len(durations)} "
        f"p50={p50:.1f}ms p95={p95:.1f}ms max={mx:.1f}ms → {_LATENCY_PATH}"
    )


# --- Registry helpers ------------------------------------------------------


def _registry_for_stub(stub_url: str, *, token_env: str = "STUB_TOKEN") -> Registry:
    """Build a one-entry Registry pointing at the given stub URL."""
    return Registry(
        servers={
            "stub": ServerConfig(
                name="stub",
                url=stub_url,
                transport="http",
                auth=AuthConfig(type="bearer", token_env=token_env),
                timeout_default=None,
            )
        }
    )


@pytest.fixture(autouse=True)
def _stub_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the default STUB_TOKEN env var is set; individual tests that
    test auth-missing behavior override by pointing at a different var."""
    monkeypatch.setenv("STUB_TOKEN", "test-token")


@pytest.fixture(autouse=True)
def _clear_validator_cache() -> Any:
    """Reset the per-process inputSchema validator cache around each test so
    tests do not leak cache entries to one another."""
    mcp_client._validator_cache.clear()
    yield
    mcp_client._validator_cache.clear()


def _prime_cache_permissive(server_name: str, tool_name: str) -> None:
    """Seed ``_validator_cache`` with a schema that accepts any object.

    Used by tests that exercise downstream ``_call_async`` paths via a fake
    ``Client``; they don't need the fetch step to run, so a wildcard object
    schema keeps the plumbing out of their way.
    """
    import jsonschema as _js

    mcp_client._validator_cache[(server_name, tool_name)] = _js.Draft7Validator(
        {"type": "object"}
    )


# --- Outcome-class coverage -------------------------------------------------


def test_ok(mcp_stub_server, request) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    reg = _registry_for_stub(mcp_stub_server.url)
    outcome = mcp_client.call("stub", "echo", {"value": "hello"}, reg)
    assert isinstance(outcome, Ok), outcome
    assert outcome.value == "hello"
    assert outcome.duration_ms > 0
    _record_if_ok(outcome, request.node.name)


def test_tool_execution_error(mcp_stub_server, request) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    reg = _registry_for_stub(mcp_stub_server.url)
    outcome = mcp_client.call("stub", "fail", {"message": "boom"}, reg)
    assert isinstance(outcome, ToolExecutionError), outcome
    assert "boom" in outcome.message


def test_schema_validation_error(mcp_stub_server, request) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    reg = _registry_for_stub(mcp_stub_server.url)
    outcome = mcp_client.call(
        "stub", "schema_required", {"count": "not-an-int"}, reg
    )
    assert isinstance(outcome, SchemaValidationError), outcome
    assert "count" in outcome.detail.lower() or "int" in outcome.detail.lower()


def test_timeout(mcp_stub_server, request) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    reg = _registry_for_stub(mcp_stub_server.url)
    outcome = mcp_client.call(
        "stub", "sleep", {"seconds": 2.0}, reg, timeout=0.5
    )
    assert isinstance(outcome, McpTimeoutError), outcome
    # Duration should be close to the timeout budget, not the full 2s.
    assert outcome.duration_ms < 1500.0


def test_transport_error() -> None:
    # Port 1 on localhost: reserved and reliably refuses connections.
    reg = Registry(
        servers={
            "dead": ServerConfig(
                name="dead",
                url="http://127.0.0.1:1/mcp/",
                transport="http",
                auth=AuthConfig(type="bearer", token_env="STUB_TOKEN"),
                timeout_default=2.0,
            )
        }
    )
    outcome = mcp_client.call("dead", "echo", {"value": "x"}, reg)
    assert isinstance(outcome, TransportError), outcome


def test_auth_env_var_missing(
    mcp_stub_server, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    monkeypatch.delenv("ABSENT_VAR", raising=False)
    reg = Registry(
        servers={
            "stub": ServerConfig(
                name="stub",
                url=mcp_stub_server.url,
                transport="http",
                auth=AuthConfig(type="bearer", token_env="ABSENT_VAR"),
                timeout_default=None,
            )
        }
    )
    outcome = mcp_client.call("stub", "echo", {"value": "x"}, reg)
    assert isinstance(outcome, TransportError), outcome
    assert "ABSENT_VAR" in outcome.detail
    assert "auth env var" in outcome.detail


def test_protocol_error_via_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Injected malformed envelope → ProtocolError.

    Directly monkeypatches ``_call_async`` to return a result type the
    classifier cannot handle; the outer ``call()`` catches the raised
    AttributeError and maps it to a conservative ProtocolError.
    """

    class FakeBadResult:  # neither CallToolResult nor anything classifier expects
        pass

    async def _fake(*_a: Any, **_kw: Any) -> Any:
        # Shortcut through the classifier with a bad object.
        return mcp_client._classify_result(FakeBadResult(), 0.0)  # type: ignore[arg-type]

    monkeypatch.setattr(mcp_client, "_call_async", _fake)
    _prime_cache_permissive("stub", "echo")
    reg = _registry_for_stub("http://127.0.0.1:9/mcp/")
    outcome = mcp_client.call("stub", "echo", {"value": "x"}, reg)
    assert isinstance(outcome, ProtocolError), outcome


def test_non_text_content_becomes_protocol_error() -> None:
    """Simulate a success result carrying an image content block."""
    import mcp.types  # local import: only this test needs it

    from fastmcp.client.client import CallToolResult as FastCallToolResult

    image_block = mcp.types.ImageContent(
        type="image", data="aGVsbG8=", mimeType="image/png"
    )
    fake_result = FastCallToolResult(
        content=[image_block],
        structured_content=None,
        meta=None,
        data=None,
        is_error=False,
    )
    outcome = mcp_client._classify_result(fake_result, 0.0)
    assert isinstance(outcome, ProtocolError), outcome
    assert outcome.detail == "v1 supports text content only"


def test_log_emission_fields(
    mcp_stub_server, caplog: pytest.LogCaptureFixture, request  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    reg = _registry_for_stub(mcp_stub_server.url)
    with caplog.at_level(logging.INFO, logger="megalos_server.mcp"):
        outcome = mcp_client.call("stub", "echo", {"value": "secret"}, reg)
    _record_if_ok(outcome, request.node.name)

    info_records = [
        r for r in caplog.records if r.levelno == logging.INFO and r.name == "megalos_server.mcp"
    ]
    assert len(info_records) == 1, [r.getMessage() for r in info_records]
    rec = info_records[0]
    # The raw value must never appear in the log record.
    assert "secret" not in rec.getMessage()
    # All committed fields are present.
    for field in ("server", "tool", "duration_ms", "outcome", "arg_fingerprint"):
        assert hasattr(rec, field), f"missing log field: {field}"
    assert rec.server == "stub"
    assert rec.tool == "echo"
    assert rec.outcome == "ok"
    assert isinstance(rec.arg_fingerprint, str)
    assert len(rec.arg_fingerprint) == 8
    assert all(c in "0123456789abcdef" for c in rec.arg_fingerprint)


def test_cold_start_latency_recording(mcp_stub_server, request) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    """Drive at least one Ok through the recorder, then verify the JSONL
    file exists (after module teardown). We can't assert on file contents
    mid-run because the finalizer flushes at module teardown; instead we
    assert on the in-memory buffer the finalizer drains."""
    reg = _registry_for_stub(mcp_stub_server.url)
    outcome = mcp_client.call("stub", "echo", {"value": "latency"}, reg)
    assert isinstance(outcome, Ok)
    _record_if_ok(outcome, request.node.name)
    # At least the sample we just recorded must be in the buffer.
    assert any(
        s["test_name"] == request.node.name and s["duration_ms"] > 0
        for s in _LATENCY_SAMPLES
    )


def test_raw_args_never_logged(
    mcp_stub_server, caplog: pytest.LogCaptureFixture, request  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    """Defense-in-depth: a sentinel value in args must not appear in any
    log record emitted during the call."""
    reg = _registry_for_stub(mcp_stub_server.url)
    sentinel = "SENTINEL_VALUE_XYZZY_12345"
    with caplog.at_level(logging.DEBUG, logger="megalos_server.mcp"):
        outcome = mcp_client.call("stub", "echo", {"value": sentinel}, reg)
    _record_if_ok(outcome, request.node.name)
    for rec in caplog.records:
        assert sentinel not in rec.getMessage()
        assert sentinel not in str(getattr(rec, "args", ""))


def test_arg_fingerprint_deterministic() -> None:
    fp1 = mcp_client._arg_fingerprint({"a": 1, "b": 2})
    fp2 = mcp_client._arg_fingerprint({"b": 2, "a": 1})
    fp3 = mcp_client._arg_fingerprint({"a": 1, "b": 3})
    assert fp1 == fp2, "key order must not change fingerprint"
    assert fp1 != fp3, "different values must produce different fingerprint"
    assert len(fp1) == 8


def test_arg_fingerprint_unhashable_arg() -> None:
    class Weird:
        pass

    # default=str in json.dumps keeps this path JSON-serializable; force
    # a failure by using a value whose str() itself fails.
    class ExplodingRepr:
        def __repr__(self) -> str:
            raise RuntimeError("boom")

        def __str__(self) -> str:
            raise RuntimeError("boom")

    fp = mcp_client._arg_fingerprint({"bad": ExplodingRepr()})
    assert fp == "unhashab"


def test_unknown_server_raises() -> None:
    from megalos_server.mcp_registry import UnknownServer

    reg = Registry(servers={})
    with pytest.raises(UnknownServer):
        mcp_client.call("missing", "echo", {}, reg)


# --- Exception-mapping coverage via a fake FastMCP Client -----------------
#
# The live stub can't easily produce every exception class in the mapping
# table (httpx.ReadTimeout mid-stream, bare httpx.ConnectError outside the
# RuntimeError wrap, McpError with INVALID_PARAMS, etc). These tests
# inject a fake Client whose ``async with`` entry or ``call_tool`` raises
# the exception under test, so each branch of the mapping is exercised.


def _make_fake_client(
    *, connect_exc: Exception | None = None, call_exc: Exception | None = None
) -> type:
    """Build a Client-shaped class that raises the given exception either
    on ``__aenter__`` or on ``call_tool``.
    """

    class _FakeClient:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            if connect_exc is not None:
                raise connect_exc
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

        async def call_tool(self, *_a: Any, **_kw: Any) -> Any:
            if call_exc is not None:
                raise call_exc
            raise AssertionError("unexpected call_tool invocation in this test")

    return _FakeClient


def _run_with_fake(monkeypatch: pytest.MonkeyPatch, fake_cls: type) -> CallOutcome:
    monkeypatch.setattr(mcp_client, "Client", fake_cls)
    # Pre-seed cache so the fetch step is skipped; these tests target the
    # ``tools/call``-path exception-mapping table, not the fetch path.
    _prime_cache_permissive("stub", "echo")
    reg = _registry_for_stub("http://127.0.0.1:9/mcp/")
    return mcp_client.call("stub", "echo", {"value": "x"}, reg)


def test_httpx_connect_error_maps_to_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    fake = _make_fake_client(connect_exc=httpx.ConnectError("refused"))
    outcome = _run_with_fake(monkeypatch, fake)
    assert isinstance(outcome, TransportError), outcome
    assert "refused" in outcome.detail


def test_httpx_connect_timeout_maps_to_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    fake = _make_fake_client(connect_exc=httpx.ConnectTimeout("slow"))
    outcome = _run_with_fake(monkeypatch, fake)
    assert isinstance(outcome, TransportError), outcome
    assert "connect timeout" in outcome.detail


def test_httpx_read_timeout_maps_to_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    fake = _make_fake_client(call_exc=httpx.ReadTimeout("stalled"))
    outcome = _run_with_fake(monkeypatch, fake)
    assert isinstance(outcome, McpTimeoutError), outcome


def test_generic_httpx_error_maps_to_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    # TransportError is an httpx.HTTPError subclass but not a ConnectError
    # or timeout — exercises the generic HTTPError branch.
    fake = _make_fake_client(call_exc=httpx.DecodingError("bad bytes"))
    outcome = _run_with_fake(monkeypatch, fake)
    assert isinstance(outcome, TransportError), outcome
    assert "http error" in outcome.detail


def test_mcp_invalid_params_maps_to_schema_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mcp.types
    from mcp.shared.exceptions import McpError

    err = McpError(
        mcp.types.ErrorData(code=mcp.types.INVALID_PARAMS, message="bad count")
    )
    fake = _make_fake_client(call_exc=err)
    outcome = _run_with_fake(monkeypatch, fake)
    assert isinstance(outcome, SchemaValidationError), outcome
    assert "bad count" in outcome.detail


def test_mcp_other_code_maps_to_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp.types
    from mcp.shared.exceptions import McpError

    err = McpError(
        mcp.types.ErrorData(code=mcp.types.INTERNAL_ERROR, message="oops")
    )
    fake = _make_fake_client(call_exc=err)
    outcome = _run_with_fake(monkeypatch, fake)
    assert isinstance(outcome, ProtocolError), outcome
    assert "oops" in outcome.detail


def test_fastmcp_tool_error_maps_to_tool_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp.exceptions import ToolError

    fake = _make_fake_client(call_exc=ToolError("tool blew up"))
    outcome = _run_with_fake(monkeypatch, fake)
    assert isinstance(outcome, ToolExecutionError), outcome
    assert "tool blew up" in outcome.message


def test_runtime_error_with_httpx_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    inner = httpx.ConnectError("refused-inner")
    outer = RuntimeError("Client failed to connect: refused-inner")
    outer.__cause__ = inner
    fake = _make_fake_client(connect_exc=outer)
    outcome = _run_with_fake(monkeypatch, fake)
    assert isinstance(outcome, TransportError), outcome
    assert "refused-inner" in outcome.detail


def test_runtime_error_with_timeout_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    inner = httpx.ConnectTimeout("slow-inner")
    outer = RuntimeError("Client failed to connect: slow-inner")
    outer.__cause__ = inner
    fake = _make_fake_client(connect_exc=outer)
    outcome = _run_with_fake(monkeypatch, fake)
    assert isinstance(outcome, McpTimeoutError), outcome


def test_runtime_error_opaque_maps_to_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    # RuntimeError with no cause, no "connection" keyword → conservative
    # ProtocolError default.
    fake = _make_fake_client(connect_exc=RuntimeError("weird internal state"))
    outcome = _run_with_fake(monkeypatch, fake)
    assert isinstance(outcome, ProtocolError), outcome


def test_unexpected_exception_maps_to_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    # Any un-mapped exception class bubbles to the outer catch-all.
    fake = _make_fake_client(call_exc=ValueError("nope"))
    outcome = _run_with_fake(monkeypatch, fake)
    assert isinstance(outcome, ProtocolError), outcome
    assert "nope" in outcome.detail


def test_is_error_no_text_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """is_error=True with empty content → descriptive ToolExecutionError."""
    from fastmcp.client.client import CallToolResult as FastCallToolResult

    result = FastCallToolResult(
        content=[], structured_content=None, meta=None, data=None, is_error=True
    )
    outcome = mcp_client._classify_result(result, 0.0)
    assert isinstance(outcome, ToolExecutionError), outcome
    assert "no text content" in outcome.message


def test_ok_with_no_content_is_empty_string() -> None:
    from fastmcp.client.client import CallToolResult as FastCallToolResult

    result = FastCallToolResult(
        content=[], structured_content=None, meta=None, data=None, is_error=False
    )
    outcome = mcp_client._classify_result(result, 0.0)
    assert isinstance(outcome, Ok), outcome
    assert outcome.value == ""


def test_registry_timeout_default_used(
    mcp_stub_server, request  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    """If no explicit timeout is passed, the registry's timeout_default wins
    over the module-level 30s fallback. Use a tiny default to keep the test
    fast."""
    reg = Registry(
        servers={
            "stub": ServerConfig(
                name="stub",
                url=mcp_stub_server.url,
                transport="http",
                auth=AuthConfig(type="bearer", token_env="STUB_TOKEN"),
                timeout_default=0.3,
            )
        }
    )
    outcome = mcp_client.call("stub", "sleep", {"seconds": 2.0}, reg)
    assert isinstance(outcome, McpTimeoutError), outcome


# --- Call-time inputSchema validation + per-process validator cache -------
#
# These tests cover the six paths called out in the T01 plan: happy-path
# validation, two validation-failure shapes (type mismatch, missing
# required), cache-hit reuse, cache-miss on tools/list transport failure,
# and malformed server inputSchema.


def test_valid_args_pass_validation_and_call_succeeds(
    mcp_stub_server, request  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    """Valid args against ``schema_required``'s inputSchema → Ok; validator
    lands in the cache after the first successful fetch."""
    reg = _registry_for_stub(mcp_stub_server.url)
    outcome = mcp_client.call("stub", "schema_required", {"count": 7}, reg)
    assert isinstance(outcome, Ok), outcome
    assert outcome.value == "count=7"
    assert ("stub", "schema_required") in mcp_client._validator_cache
    _record_if_ok(outcome, request.node.name)


def test_type_mismatch_short_circuits_before_tools_call(
    mcp_stub_server, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    """Non-int ``count`` is rejected locally: the client never issues
    ``tools/call``. We assert this by priming a validator that rejects,
    then patching ``_call_async`` to blow up on any invocation."""
    import jsonschema as _js

    mcp_client._validator_cache[("stub", "schema_required")] = _js.Draft7Validator(
        {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        }
    )

    async def _explode(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("tools/call must not run on validation failure")

    monkeypatch.setattr(mcp_client, "_call_async", _explode)

    reg = _registry_for_stub(mcp_stub_server.url)
    outcome = mcp_client.call(
        "stub", "schema_required", {"count": "not-an-int"}, reg
    )
    assert isinstance(outcome, SchemaValidationError), outcome
    assert "count" in outcome.detail.lower() or "integer" in outcome.detail.lower()


def test_missing_required_field_surfaces_schema_validation_error(
    mcp_stub_server  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    """An args dict missing a required field is rejected client-side."""
    reg = _registry_for_stub(mcp_stub_server.url)
    outcome = mcp_client.call("stub", "schema_required", {}, reg)
    assert isinstance(outcome, SchemaValidationError), outcome
    assert "count" in outcome.detail.lower() or "required" in outcome.detail.lower()


def test_cache_hit_reuses_validator(
    mcp_stub_server, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    """Two calls for the same ``(server, tool)`` pair hit ``tools/list``
    exactly once. Instrumented via a wrapper around ``_fetch_input_schema``
    that increments a counter on each call."""
    original = mcp_client._fetch_input_schema
    counter = {"calls": 0}

    async def _counting_fetch(*args: Any, **kwargs: Any) -> Any:
        counter["calls"] += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(mcp_client, "_fetch_input_schema", _counting_fetch)

    reg = _registry_for_stub(mcp_stub_server.url)
    o1 = mcp_client.call("stub", "schema_required", {"count": 1}, reg)
    o2 = mcp_client.call("stub", "schema_required", {"count": 2}, reg)
    assert isinstance(o1, Ok) and isinstance(o2, Ok)
    assert counter["calls"] == 1, counter


def test_tools_list_transport_failure_leaves_cache_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``tools/list`` fails, no cache entry is written — so the next
    call retries the fetch rather than serving a stale validator."""
    import httpx as _httpx

    class _DeadClient:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            raise _httpx.ConnectError("refused")

        async def __aexit__(self, *_a: Any) -> None:
            return None

        async def list_tools(self) -> Any:
            raise AssertionError("unreachable")

        async def call_tool(self, *_a: Any, **_kw: Any) -> Any:
            raise AssertionError("unreachable")

    monkeypatch.setattr(mcp_client, "Client", _DeadClient)

    reg = _registry_for_stub("http://127.0.0.1:9/mcp/")
    outcome = mcp_client.call("stub", "echo", {"value": "x"}, reg)
    assert isinstance(outcome, TransportError), outcome
    assert ("stub", "echo") not in mcp_client._validator_cache

    # A second call also retries the fetch — still TransportError, still
    # no cache entry. Proves the miss-path does not memoize failures.
    outcome2 = mcp_client.call("stub", "echo", {"value": "x"}, reg)
    assert isinstance(outcome2, TransportError), outcome2
    assert ("stub", "echo") not in mcp_client._validator_cache


def test_malformed_server_input_schema_surfaces_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the server's ``tools/list`` response lists the tool but its
    ``inputSchema`` is not a dict with a ``type`` key, classify as
    ``ProtocolError`` (and do not cache anything)."""
    import mcp.types as _mt

    class _Tool:
        def __init__(self, name: str, input_schema: Any) -> None:
            self.name = name
            self.inputSchema = input_schema

    class _WeirdSchemaClient:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

        async def list_tools(self) -> list[Any]:
            # Schema missing ``type`` key → malformed per our policy.
            return [_Tool("echo", {"properties": {}})]

        async def call_tool(self, *_a: Any, **_kw: Any) -> Any:
            raise AssertionError("unreachable")

    monkeypatch.setattr(mcp_client, "Client", _WeirdSchemaClient)

    reg = _registry_for_stub("http://127.0.0.1:9/mcp/")
    outcome = mcp_client.call("stub", "echo", {"value": "x"}, reg)
    assert isinstance(outcome, ProtocolError), outcome
    assert "inputSchema" in outcome.detail
    assert ("stub", "echo") not in mcp_client._validator_cache

    # Suppress unused-import warning for mcp.types — kept to document intent.
    assert _mt is not None


def test_tool_not_listed_in_tools_list_surfaces_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the server's ``tools/list`` does not include the requested tool,
    treat it as a server-side protocol violation."""

    class _MissingToolClient:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

        async def list_tools(self) -> list[Any]:
            return []  # empty — target tool absent

        async def call_tool(self, *_a: Any, **_kw: Any) -> Any:
            raise AssertionError("unreachable")

    monkeypatch.setattr(mcp_client, "Client", _MissingToolClient)

    reg = _registry_for_stub("http://127.0.0.1:9/mcp/")
    outcome = mcp_client.call("stub", "echo", {"value": "x"}, reg)
    assert isinstance(outcome, ProtocolError), outcome
    assert "not listed" in outcome.detail


def test_uncompilable_schema_surfaces_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dict-shaped schema with a ``type`` key that jsonschema cannot
    compile (e.g. a bogus type keyword value) surfaces as ``ProtocolError``
    at the validator-compile step, not the fetch step."""

    class _Tool:
        def __init__(self, name: str, input_schema: Any) -> None:
            self.name = name
            self.inputSchema = input_schema

    class _BadSchemaClient:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

        async def list_tools(self) -> list[Any]:
            # "type" key is present so it passes the fetch-level shape check,
            # but the value is not a valid Draft7 type so Draft7Validator
            # raises SchemaError at compile time.
            return [_Tool("echo", {"type": "not-a-real-type"})]

        async def call_tool(self, *_a: Any, **_kw: Any) -> Any:
            raise AssertionError("unreachable")

    monkeypatch.setattr(mcp_client, "Client", _BadSchemaClient)

    # Make ``Draft7Validator(...)`` construction strict so the bogus type
    # is caught at compile rather than validate time.
    import jsonschema as _js

    original = _js.Draft7Validator

    def _strict_ctor(schema: Any, *a: Any, **kw: Any) -> Any:
        original.check_schema(schema)
        return original(schema, *a, **kw)

    monkeypatch.setattr(mcp_client.jsonschema, "Draft7Validator", _strict_ctor)

    reg = _registry_for_stub("http://127.0.0.1:9/mcp/")
    outcome = mcp_client.call("stub", "echo", {"value": "x"}, reg)
    assert isinstance(outcome, ProtocolError), outcome
    assert "inputSchema invalid" in outcome.detail
    assert ("stub", "echo") not in mcp_client._validator_cache


def test_validate_args_success_returns_none() -> None:
    """Unit test: ``_validate_args`` returns ``None`` on valid args."""
    import jsonschema as _js

    v = _js.Draft7Validator({"type": "object", "properties": {"x": {"type": "integer"}}})
    assert mcp_client._validate_args(v, {"x": 3}) is None


def test_validate_args_failure_formats_detail() -> None:
    """Unit test: ``_validate_args`` returns ``json_path: message`` on failure."""
    import jsonschema as _js

    v = _js.Draft7Validator(
        {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
    )
    detail = mcp_client._validate_args(v, {})
    assert detail is not None
    assert ":" in detail

