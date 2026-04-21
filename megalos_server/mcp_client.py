"""Synchronous MCP client wrapper with outcome taxonomy and structured logging.

Public API:
    call(server_name, tool, args, registry, timeout=None) -> CallOutcome

Every invocation spins up a fresh FastMCP ``Client`` via ``asyncio.run`` — no
connection pooling, no cached sessions. This is deliberate: megalos_server is
100% sync and async-coloring one module to share a loop would touch every
call path in the server. Cold-start latency is measured at slice-close; if
the p95 is judged intolerable, the replan adds a connection cache in T04.

The outcome taxonomy is a tagged union of flat dataclasses — no inheritance,
no abstract base class, no strategy pattern. Callers discriminate with
``isinstance`` (or ``match``), which is boring and obvious.

Logging: exactly one ``info`` log per call on the ``megalos_server.mcp``
logger, carrying ``server``, ``tool``, ``duration_ms``, ``outcome``,
``arg_fingerprint`` (sha256 of sorted-key JSON of args, first 8 hex chars).
One ``debug`` log with ``handshake_ms``. Raw args and raw result content
are never logged.

Arg-schema validation (call-time)
---------------------------------
Before each ``tools/call`` network round-trip, caller-supplied args are
validated against the tool's ``inputSchema`` — pulled on first use via a
``tools/list`` request and cached as a compiled ``jsonschema.Draft7Validator``
in a module-global dict keyed by ``(server_name, tool_name)``.

Cache policy: **process lifetime, no TTL, no invalidation.** A server schema
change requires a megalos restart. Rationale:

- Stale schemas are benign. If a server tightens a field, the cached
  validator still accepts old args; the server then rejects the call and
  surfaces as ``ToolExecutionError`` / ``SchemaValidationError`` — no data
  corruption, no silent success.
- Horizon scale-to-zero means megalos processes rarely outlive schema
  staleness in practice; the first ``tools/call`` post-restart self-heals
  the cache.
- No TTL keeps the code obvious: one module-global dict, two helpers, zero
  clock arithmetic. Three strikes before complexity.

``jsonschema`` is already transitively pinned via ``fastmcp`` (which uses it
for MCP protocol validation); this module is its first *direct* runtime use
in megalos. No new dependency line in ``pyproject.toml``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Union

import httpx
import jsonschema
import mcp.types
from fastmcp import Client
from fastmcp.client.auth import BearerAuth
from fastmcp.client.client import CallToolResult
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError

from megalos_server.mcp_registry import Registry, ServerConfig

_log = logging.getLogger("megalos_server.mcp")

# Default hard cap when neither the caller nor the registry supplies one.
# 30s matches the plan and is long enough for any sane tool the stub exposes.
_DEFAULT_TIMEOUT_S: float = 30.0

# JSON-RPC codes that indicate the *params* the client supplied failed
# server-side schema validation. Anything else from McpError is treated as
# a generic protocol-level error — conservative default.
_SCHEMA_ERROR_CODES: frozenset[int] = frozenset({mcp.types.INVALID_PARAMS})


# Module-global per-process inputSchema validator cache. Keyed by
# ``(server_name, tool_name)``; value is a compiled ``Draft7Validator``.
# Populated on first successful fetch-and-compile for a pair; never evicted
# or invalidated during a process's lifetime. See module docstring for
# policy rationale.
_validator_cache: dict[tuple[str, str], jsonschema.Draft7Validator] = {}


# --- Outcome taxonomy ------------------------------------------------------
#
# Flat dataclasses, no inheritance. ``kind`` is carried as a string field on
# each class for simple serialization/logging; discrimination at the call
# site uses ``isinstance``.


@dataclass(frozen=True)
class Ok:
    """Tool call succeeded and returned text content."""

    value: str
    duration_ms: float
    kind: str = "ok"


@dataclass(frozen=True)
class ToolExecutionError:
    """Server ran the tool but the tool itself signalled failure."""

    message: str
    duration_ms: float
    kind: str = "tool_execution_error"


@dataclass(frozen=True)
class TransportError:
    """Could not reach the server, or auth env var was missing."""

    detail: str
    duration_ms: float
    kind: str = "transport_error"


@dataclass(frozen=True)
class ProtocolError:
    """Server reachable but the response violated the MCP envelope contract."""

    detail: str
    duration_ms: float
    kind: str = "protocol_error"


@dataclass(frozen=True)
class SchemaValidationError:
    """Server rejected the caller's args against the tool's inputSchema."""

    detail: str
    duration_ms: float
    kind: str = "schema_validation_error"


@dataclass(frozen=True)
class TimeoutError:  # noqa: A001  (intentional: MCP-level timeout outcome)
    """The request exceeded the effective timeout budget."""

    duration_ms: float
    kind: str = "timeout"


CallOutcome = Union[
    Ok,
    ToolExecutionError,
    TransportError,
    ProtocolError,
    SchemaValidationError,
    TimeoutError,
]


# --- Public API ------------------------------------------------------------


def call(
    server_name: str,
    tool: str,
    args: dict[str, Any],
    registry: Registry,
    timeout: float | None = None,
) -> CallOutcome:
    """Call ``tool`` on the server named ``server_name``.

    Synchronous. Internally wraps an async FastMCP call in ``asyncio.run``.

    Effective timeout = ``timeout`` param → ``ServerConfig.timeout_default`` →
    ``_DEFAULT_TIMEOUT_S``.

    On first call for a given ``(server_name, tool)`` pair this also issues a
    ``tools/list`` request to fetch the tool's ``inputSchema``, which is
    compiled and cached for the process lifetime. Validation failures short-
    circuit the ``tools/call`` network trip and surface as
    ``SchemaValidationError``.

    Returns one of the ``CallOutcome`` variants; never raises for any
    expected error class (registry misses surface as ``UnknownServer`` from
    ``registry.get`` — not caught here, caller's responsibility).
    """
    cfg = registry.get(server_name)
    effective_timeout = (
        timeout
        if timeout is not None
        else (cfg.timeout_default if cfg.timeout_default is not None else _DEFAULT_TIMEOUT_S)
    )

    arg_fp = _arg_fingerprint(args)
    start = time.monotonic()
    try:
        outcome = asyncio.run(
            _call_with_validation_async(
                server_name, cfg, tool, args, effective_timeout, registry
            )
        )
    except Exception as exc:  # noqa: BLE001 — conservative catch-all
        duration_ms = (time.monotonic() - start) * 1000.0
        _log.exception(
            "unexpected exception in MCP call",
            extra={"server": server_name, "tool": tool},
        )
        outcome = ProtocolError(detail=repr(exc), duration_ms=duration_ms)

    _log.info(
        "mcp call",
        extra={
            "server": server_name,
            "tool": tool,
            "duration_ms": outcome.duration_ms,
            "outcome": outcome.kind,
            "arg_fingerprint": arg_fp,
        },
    )
    return outcome


# --- Internals -------------------------------------------------------------


async def _call_with_validation_async(
    server_name: str,
    cfg: ServerConfig,
    tool: str,
    args: dict[str, Any],
    timeout_s: float,
    registry: Registry,
) -> CallOutcome:
    """Cache-miss fetch + compile, then validate args, then ``tools/call``.

    On cache miss: issue ``tools/list``, locate the tool, compile its
    ``inputSchema`` into a ``Draft7Validator``, then write the cache entry.
    Fetch transport errors → ``TransportError`` (cache NOT written, next
    call retries). Malformed server schema → ``ProtocolError``.

    On cache hit or after miss-resolution: validate args. Failure →
    ``SchemaValidationError`` (no ``tools/call`` round-trip). Success →
    delegate to ``_call_async`` for the actual tool invocation.
    """
    start = time.monotonic()
    key = (server_name, tool)

    validator = _validator_cache.get(key)
    if validator is None:
        fetch_start = time.monotonic()
        try:
            schema = await _fetch_input_schema(server_name, tool, registry, timeout_s)
        except _SchemaFetchTransportError as exc:
            return TransportError(
                detail=exc.detail,
                duration_ms=(time.monotonic() - start) * 1000.0,
            )
        except _SchemaFetchProtocolError as exc:
            return ProtocolError(
                detail=exc.detail,
                duration_ms=(time.monotonic() - start) * 1000.0,
            )
        fetch_ms = (time.monotonic() - fetch_start) * 1000.0
        try:
            validator = jsonschema.Draft7Validator(schema)
        except jsonschema.exceptions.SchemaError as exc:
            return ProtocolError(
                detail=f"server inputSchema invalid: {exc.message}",
                duration_ms=(time.monotonic() - start) * 1000.0,
            )
        _validator_cache[key] = validator
        _log.debug(
            "mcp inputschema fetch",
            extra={
                "server": server_name,
                "tool": tool,
                "cache_hit": False,
                "inputschema_fetch_ms": fetch_ms,
            },
        )
    else:
        _log.debug(
            "mcp inputschema fetch",
            extra={
                "server": server_name,
                "tool": tool,
                "cache_hit": True,
            },
        )

    validation_outcome = _validate_args(validator, args)
    if validation_outcome is not None:
        return SchemaValidationError(
            detail=validation_outcome,
            duration_ms=(time.monotonic() - start) * 1000.0,
        )

    return await _call_async(cfg, tool, args, timeout_s)


class _SchemaFetchTransportError(Exception):
    """Signals ``tools/list`` could not reach the server (no cache write)."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class _SchemaFetchProtocolError(Exception):
    """Signals the server's ``tools/list`` response was malformed."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


async def _fetch_input_schema(
    server_name: str,
    tool_name: str,
    registry: Registry,
    timeout_s: float,
) -> dict[str, Any]:
    """Issue ``tools/list`` and return ``tool_name``'s ``inputSchema``.

    Classifies failures for the caller by raising a private exception:
    transport/unreachable → ``_SchemaFetchTransportError``;
    protocol-level malformed schema → ``_SchemaFetchProtocolError``.
    """
    cfg = registry.get(server_name)
    token = os.environ.get(cfg.auth.token_env)
    if token is None:
        raise _SchemaFetchTransportError(
            f"auth env var ${cfg.auth.token_env} not set"
        )

    try:
        client = Client(cfg.url, auth=BearerAuth(token), timeout=timeout_s)
        async with client:
            tools = await asyncio.wait_for(client.list_tools(), timeout=timeout_s)
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        raise _SchemaFetchTransportError(
            f"tools/list timed out for {cfg.url}: {exc}"
        ) from exc
    except httpx.HTTPError as exc:
        raise _SchemaFetchTransportError(
            f"tools/list http error for {cfg.url}: {exc}"
        ) from exc
    except RuntimeError as exc:
        cause = exc.__cause__
        if isinstance(cause, httpx.HTTPError | asyncio.TimeoutError):
            raise _SchemaFetchTransportError(
                f"tools/list connect error for {cfg.url}: {cause}"
            ) from exc
        msg = str(exc)
        if "failed to connect" in msg.lower() or "connection" in msg.lower():
            raise _SchemaFetchTransportError(
                f"tools/list connect error for {cfg.url}: {msg}"
            ) from exc
        raise
    except McpError as exc:
        raise _SchemaFetchProtocolError(
            f"server inputSchema invalid: mcp error {exc}"
        ) from exc

    match = next((t for t in tools if t.name == tool_name), None)
    if match is None:
        raise _SchemaFetchProtocolError(
            f"server inputSchema invalid: tool {tool_name!r} not listed"
        )

    schema = match.inputSchema
    if not isinstance(schema, dict) or "type" not in schema:
        raise _SchemaFetchProtocolError(
            f"server inputSchema invalid: not a dict with 'type' "
            f"(got {type(schema).__name__})"
        )
    return schema


def _validate_args(
    validator: jsonschema.Draft7Validator, args: dict[str, Any]
) -> str | None:
    """Run ``validator.validate(args)``. Returns a formatted detail string on
    failure, or ``None`` on success.
    """
    try:
        validator.validate(args)
    except jsonschema.ValidationError as exc:
        return f"{exc.json_path}: {exc.message}"
    return None


async def _call_async(
    cfg: ServerConfig,
    tool: str,
    args: dict[str, Any],
    timeout_s: float,
) -> CallOutcome:
    """Perform one MCP call; classify the result into a ``CallOutcome``.

    Always returns a ``CallOutcome``; all expected exception classes are
    caught and mapped. Unexpected ones propagate to ``call()``'s outer
    catch-all, which maps them to ``ProtocolError``.
    """
    start = time.monotonic()

    # Resolve auth env var at call time — missing var is actionable against
    # this specific call, so it surfaces as a transport-class error rather
    # than a startup failure.
    token = os.environ.get(cfg.auth.token_env)
    if token is None:
        duration_ms = (time.monotonic() - start) * 1000.0
        return TransportError(
            detail=f"auth env var ${cfg.auth.token_env} not set",
            duration_ms=duration_ms,
        )

    handshake_start = time.monotonic()
    try:
        client = Client(cfg.url, auth=BearerAuth(token), timeout=timeout_s)
        async with client:
            handshake_ms = (time.monotonic() - handshake_start) * 1000.0
            _log.debug(
                "mcp handshake",
                extra={"server": cfg.name, "tool": tool, "handshake_ms": handshake_ms},
            )
            # raise_on_error=False: we discriminate tool-errors from
            # transport/protocol errors at the outcome-class level; we
            # don't want FastMCP to raise ToolError on the success path.
            result = await asyncio.wait_for(
                client.call_tool(tool, args, raise_on_error=False),
                timeout=timeout_s,
            )
    except asyncio.TimeoutError:
        return TimeoutError(duration_ms=(time.monotonic() - start) * 1000.0)
    except httpx.ReadTimeout:
        return TimeoutError(duration_ms=(time.monotonic() - start) * 1000.0)
    except httpx.ConnectTimeout:
        return TransportError(
            detail=f"connect timeout to {cfg.url}",
            duration_ms=(time.monotonic() - start) * 1000.0,
        )
    except httpx.ConnectError as exc:
        return TransportError(
            detail=f"connect error to {cfg.url}: {exc}",
            duration_ms=(time.monotonic() - start) * 1000.0,
        )
    except httpx.HTTPError as exc:
        # Any other httpx transport-level failure (non-2xx after retries,
        # read error mid-stream, etc.). Errs toward transport since the
        # TCP/HTTP layer is implicated, not the JSON-RPC envelope.
        return TransportError(
            detail=f"http error to {cfg.url}: {exc}",
            duration_ms=(time.monotonic() - start) * 1000.0,
        )
    except RuntimeError as exc:
        # FastMCP wraps most connect failures in ``RuntimeError("Client
        # failed to connect: ...")``, chaining the original httpx error as
        # ``__cause__``. We unwrap one level to classify precisely; if the
        # cause is an httpx connect/timeout error we surface TransportError
        # or TimeoutError, otherwise we conservatively pick ProtocolError.
        duration_ms = (time.monotonic() - start) * 1000.0
        cause = exc.__cause__
        if isinstance(cause, httpx.ConnectTimeout | httpx.ReadTimeout):
            return TimeoutError(duration_ms=duration_ms)
        if isinstance(cause, httpx.HTTPError):
            return TransportError(
                detail=f"connect error to {cfg.url}: {cause}",
                duration_ms=duration_ms,
            )
        msg = str(exc)
        if "failed to connect" in msg.lower() or "connection" in msg.lower():
            return TransportError(
                detail=f"connect error to {cfg.url}: {msg}",
                duration_ms=duration_ms,
            )
        return ProtocolError(detail=repr(exc), duration_ms=duration_ms)
    except McpError as exc:
        # JSON-RPC-level error from the server. INVALID_PARAMS is a schema
        # validation error against the tool's inputSchema; other codes are
        # generic protocol errors.
        code = getattr(getattr(exc, "error", None), "code", None)
        message = getattr(getattr(exc, "error", None), "message", str(exc))
        duration_ms = (time.monotonic() - start) * 1000.0
        if code in _SCHEMA_ERROR_CODES:
            return SchemaValidationError(detail=message, duration_ms=duration_ms)
        return ProtocolError(
            detail=f"mcp error code={code}: {message}",
            duration_ms=duration_ms,
        )
    except ToolError as exc:
        # Shouldn't fire with raise_on_error=False, but FastMCP may raise
        # ToolError from client-side schema rejection paths too. Treat the
        # text as a tool execution error — conservative, surfaces as an
        # actionable tool-level failure to the caller.
        return ToolExecutionError(
            message=str(exc),
            duration_ms=(time.monotonic() - start) * 1000.0,
        )

    return _classify_result(result, start)


def _classify_result(result: CallToolResult, start: float) -> CallOutcome:
    """Map a parsed FastMCP ``CallToolResult`` into a ``CallOutcome``."""
    duration_ms = (time.monotonic() - start) * 1000.0

    if result.is_error:
        # Error text lives in the first text content block, if any.
        text = _flatten_text_blocks(result.content)
        if text is None:
            return ToolExecutionError(
                message="tool returned isError with no text content",
                duration_ms=duration_ms,
            )
        return ToolExecutionError(message=text, duration_ms=duration_ms)

    # Success path: v1 accepts only text content blocks. Anything else
    # surfaces as a ProtocolError — explicit M006-v1 constraint.
    for block in result.content:
        if not isinstance(block, mcp.types.TextContent):
            return ProtocolError(
                detail="v1 supports text content only",
                duration_ms=duration_ms,
            )

    text = _flatten_text_blocks(result.content)
    if text is None:
        # Success with no content blocks at all — treat as empty string.
        return Ok(value="", duration_ms=duration_ms)
    return Ok(value=text, duration_ms=duration_ms)


def _flatten_text_blocks(content: list[Any]) -> str | None:
    """Concatenate the ``.text`` of every ``TextContent`` block, or None."""
    parts = [b.text for b in content if isinstance(b, mcp.types.TextContent)]
    if not parts:
        return None
    return "".join(parts)


def _arg_fingerprint(args: dict[str, Any]) -> str:
    """First 8 hex chars of sha256(sorted-key JSON of args).

    Deterministic across calls with the same args; reveals nothing about
    the values themselves. Falls back to a literal ``"unhashable"`` tag if
    args aren't JSON-serializable — the logger should still emit a value.
    """
    try:
        blob = json.dumps(args, sort_keys=True, default=str).encode("utf-8")
    except Exception:  # noqa: BLE001 — fingerprint must never crash the call
        return "unhashab"
    return hashlib.sha256(blob).hexdigest()[:8]
