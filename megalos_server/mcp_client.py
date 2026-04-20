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
        outcome = asyncio.run(_call_async(cfg, tool, args, effective_timeout))
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
