"""Tests for megalos_server.mcp_client.

Covers each ``CallOutcome`` variant and structured-log fields. The stub
FastMCP server (T02 fixture) supplies the happy path and most error
shapes; ``ProtocolError`` paths that the stub cannot produce (non-text
content, malformed envelopes) are covered via mock injection.

Cold-start latency measurement lives in
``benchmarks/bench_mcp_client_cold_start.py``.
"""

from __future__ import annotations

import logging
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


def test_ok(mcp_stub_server) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    reg = _registry_for_stub(mcp_stub_server.url)
    outcome = mcp_client.call("stub", "echo", {"value": "hello"}, reg)
    assert isinstance(outcome, Ok), outcome
    assert outcome.value == "hello"
    assert outcome.duration_ms > 0


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
    mcp_stub_server, caplog: pytest.LogCaptureFixture  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    reg = _registry_for_stub(mcp_stub_server.url)
    with caplog.at_level(logging.INFO, logger="megalos_server.mcp"):
        mcp_client.call("stub", "echo", {"value": "secret"}, reg)

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


def test_raw_args_never_logged(
    mcp_stub_server, caplog: pytest.LogCaptureFixture  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    """Defense-in-depth: a sentinel value in args must not appear in any
    log record emitted during the call."""
    reg = _registry_for_stub(mcp_stub_server.url)
    sentinel = "SENTINEL_VALUE_XYZZY_12345"
    with caplog.at_level(logging.DEBUG, logger="megalos_server.mcp"):
        mcp_client.call("stub", "echo", {"value": sentinel}, reg)
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
    mcp_stub_server  # type: ignore[no-untyped-def]  # noqa: F811
) -> None:
    """Valid args against ``schema_required``'s inputSchema → Ok; validator
    lands in the cache after the first successful fetch."""
    reg = _registry_for_stub(mcp_stub_server.url)
    outcome = mcp_client.call("stub", "schema_required", {"count": 7}, reg)
    assert isinstance(outcome, Ok), outcome
    assert outcome.value == "count=7"
    assert ("stub", "schema_required") in mcp_client._validator_cache


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


# --- Retry loop (TransportError + TimeoutError only) ----------------------
#
# Uses a clock-fake pattern mirroring tests/test_panel_throttle.py: fake
# ``time.monotonic`` and ``time.sleep`` so retry spacing is asserted without
# real wall-clock waits, and so retry backoffs advance the fake clock into
# ``duration_ms`` computation deterministically.


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Mutable [now] container driving time.monotonic inside mcp_client."""
    state = [0.0]
    monkeypatch.setattr(mcp_client.time, "monotonic", lambda: state[0])
    return state


@pytest.fixture
def sleep_log(
    monkeypatch: pytest.MonkeyPatch, fake_clock: list[float]
) -> list[float]:
    """Replace time.sleep with a clock-advancing capture list."""
    captured: list[float] = []

    def fake_sleep(seconds: float) -> None:
        captured.append(seconds)
        fake_clock[0] += seconds

    monkeypatch.setattr(mcp_client.time, "sleep", fake_sleep)
    return captured


def _script_validation_outcomes(
    monkeypatch: pytest.MonkeyPatch, outcomes: list[CallOutcome]
) -> list[int]:
    """Replace ``_call_with_validation_async`` with a scripted sequence.

    Each successive call pops the next outcome from ``outcomes``. Returns a
    list used as a call counter so tests can assert attempt count.
    """
    call_counter: list[int] = []

    async def _scripted(*_a: Any, **_kw: Any) -> CallOutcome:
        call_counter.append(1)
        return outcomes.pop(0)

    monkeypatch.setattr(mcp_client, "_call_with_validation_async", _scripted)
    return call_counter


def test_retry_then_succeed_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
    fake_clock: list[float],
    sleep_log: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """First two attempts TransportError, third Ok; sleep_log == [0.2, 0.4]."""
    outcomes: list[CallOutcome] = [
        TransportError(detail="refused-1", duration_ms=0.0),
        TransportError(detail="refused-2", duration_ms=0.0),
        Ok(value="finally", duration_ms=0.0),
    ]
    counter = _script_validation_outcomes(monkeypatch, outcomes)
    reg = _registry_for_stub("http://127.0.0.1:9/mcp/")
    with caplog.at_level(logging.DEBUG, logger="megalos_server.mcp"):
        outcome = mcp_client.call("stub", "echo", {"value": "x"}, reg)
    assert isinstance(outcome, Ok), outcome
    assert outcome.value == "finally"
    assert sleep_log == [0.2, 0.4]
    assert len(counter) == 3
    # Retry transitions + terminal success.
    retry_records = [
        r for r in caplog.records
        if r.name == "megalos_server.mcp" and r.getMessage() == "mcp call retry"
    ]
    assert len(retry_records) == 2
    assert retry_records[0].attempt == 1
    assert retry_records[0].backoff_ms == 200.0
    assert retry_records[0].outcome_kind == "TransportError"
    assert retry_records[1].attempt == 2
    assert retry_records[1].backoff_ms == 400.0
    # arg_fingerprint threaded through every retry record.
    fps = {r.arg_fingerprint for r in retry_records}
    assert len(fps) == 1
    assert len(next(iter(fps))) == 8
    # Terminal success log carries total_ms + final attempt.
    terminal = [
        r for r in caplog.records
        if r.getMessage() == "mcp call terminal success"
    ]
    assert len(terminal) == 1
    assert terminal[0].attempt == 3
    assert hasattr(terminal[0], "total_ms")


def test_retry_then_succeed_on_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
    fake_clock: list[float],
    sleep_log: list[float],
) -> None:
    """First two attempts TimeoutError, third Ok; sleep_log == [0.2, 0.4]."""
    outcomes: list[CallOutcome] = [
        McpTimeoutError(duration_ms=0.0),
        McpTimeoutError(duration_ms=0.0),
        Ok(value="ok", duration_ms=0.0),
    ]
    counter = _script_validation_outcomes(monkeypatch, outcomes)
    reg = _registry_for_stub("http://127.0.0.1:9/mcp/")
    outcome = mcp_client.call("stub", "echo", {"value": "x"}, reg)
    assert isinstance(outcome, Ok), outcome
    assert sleep_log == [0.2, 0.4]
    assert len(counter) == 3


def test_retry_exhausts_on_repeated_transport_error(
    monkeypatch: pytest.MonkeyPatch,
    fake_clock: list[float],
    sleep_log: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """All three attempts TransportError → terminal TransportError.

    Three total attempts means two backoffs, so sleep_log == [0.2, 0.4].
    Terminal record logged at WARNING with detail + final attempt.
    """
    outcomes: list[CallOutcome] = [
        TransportError(detail="refused-a", duration_ms=0.0),
        TransportError(detail="refused-b", duration_ms=0.0),
        TransportError(detail="refused-c", duration_ms=0.0),
    ]
    counter = _script_validation_outcomes(monkeypatch, outcomes)
    reg = _registry_for_stub("http://127.0.0.1:9/mcp/")
    with caplog.at_level(logging.DEBUG, logger="megalos_server.mcp"):
        outcome = mcp_client.call("stub", "echo", {"value": "x"}, reg)
    assert isinstance(outcome, TransportError), outcome
    assert outcome.detail == "refused-c"
    assert sleep_log == [0.2, 0.4]
    assert len(counter) == 3
    terminal = [
        r for r in caplog.records
        if r.getMessage() == "mcp call terminal failure"
    ]
    assert len(terminal) == 1
    assert terminal[0].levelno == logging.WARNING
    assert terminal[0].attempt == 3
    assert terminal[0].detail == "refused-c"
    assert hasattr(terminal[0], "total_ms")


def test_no_retry_on_tool_execution_error(
    monkeypatch: pytest.MonkeyPatch,
    fake_clock: list[float],
    sleep_log: list[float],
) -> None:
    """ToolExecutionError is non-retriable: single attempt, no sleeps."""
    outcomes: list[CallOutcome] = [
        ToolExecutionError(message="boom", duration_ms=0.0),
    ]
    counter = _script_validation_outcomes(monkeypatch, outcomes)
    reg = _registry_for_stub("http://127.0.0.1:9/mcp/")
    outcome = mcp_client.call("stub", "echo", {"value": "x"}, reg)
    assert isinstance(outcome, ToolExecutionError), outcome
    assert sleep_log == []
    assert len(counter) == 1


def test_no_retry_on_schema_validation_error(
    monkeypatch: pytest.MonkeyPatch,
    fake_clock: list[float],
    sleep_log: list[float],
) -> None:
    """SchemaValidationError is non-retriable.

    In practice the T01 short-circuit produces this before the retry loop
    even sees a network call, but the loop must still treat it as terminal:
    a rejecting validator is deterministic across attempts.
    """
    import jsonschema as _js

    # Prime a rejecting validator so _call_with_validation_async produces
    # SchemaValidationError locally, without any network I/O.
    mcp_client._validator_cache[("stub", "echo")] = _js.Draft7Validator(
        {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        }
    )
    # Also block _call_async so if the retry loop somehow retried, we'd see
    # a loud failure rather than a silent re-validation.
    async def _explode(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("tools/call must not run on SchemaValidationError")

    monkeypatch.setattr(mcp_client, "_call_async", _explode)

    reg = _registry_for_stub("http://127.0.0.1:9/mcp/")
    outcome = mcp_client.call("stub", "echo", {"value": "not-an-int"}, reg)
    assert isinstance(outcome, SchemaValidationError), outcome
    assert sleep_log == []


def test_no_retry_on_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
    fake_clock: list[float],
    sleep_log: list[float],
) -> None:
    """ProtocolError is non-retriable: single attempt, no sleeps."""
    outcomes: list[CallOutcome] = [
        ProtocolError(detail="bad envelope", duration_ms=0.0),
    ]
    counter = _script_validation_outcomes(monkeypatch, outcomes)
    reg = _registry_for_stub("http://127.0.0.1:9/mcp/")
    outcome = mcp_client.call("stub", "echo", {"value": "x"}, reg)
    assert isinstance(outcome, ProtocolError), outcome
    assert sleep_log == []
    assert len(counter) == 1


def test_retry_log_fields_present(
    monkeypatch: pytest.MonkeyPatch,
    fake_clock: list[float],
    sleep_log: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each retry transition carries attempt, backoff_ms, arg_fingerprint."""
    outcomes: list[CallOutcome] = [
        TransportError(detail="refused-1", duration_ms=0.0),
        Ok(value="ok", duration_ms=0.0),
    ]
    _script_validation_outcomes(monkeypatch, outcomes)
    reg = _registry_for_stub("http://127.0.0.1:9/mcp/")
    with caplog.at_level(logging.INFO, logger="megalos_server.mcp"):
        outcome = mcp_client.call("stub", "echo", {"value": "x"}, reg)
    assert isinstance(outcome, Ok), outcome
    retry_records = [
        r for r in caplog.records if r.getMessage() == "mcp call retry"
    ]
    assert len(retry_records) == 1
    rec = retry_records[0]
    for field in ("attempt", "backoff_ms", "arg_fingerprint", "server", "tool", "outcome_kind"):
        assert hasattr(rec, field), f"missing log field: {field}"
    assert rec.attempt == 1
    assert rec.backoff_ms == 200.0
    assert rec.outcome_kind == "TransportError"
    assert len(rec.arg_fingerprint) == 8

