"""FastMCP middleware: validation-error normalization + caller-identity seam + rate-limit gate.

ValidationErrorMiddleware — FastMCP validates tool arguments against a
pydantic model BEFORE the tool body runs. When that validation fails (None
for a required arg, wrong type for a str arg, etc.) it raises
pydantic.ValidationError, which the per-tool _trap_errors decorator never
sees because the body is never entered. This middleware sits at the
framework boundary, catches that exception, and returns the same
{status, code, field, error} shape that _check_str would have emitted if
the call had reached the tool body. The wire-level error contract is
therefore uniform whether the rejection happens in pydantic or in the
tool's own _check_str / size-cap / KeyError handling.

CallerIdentityMiddleware — populates the per-request ``caller_identity`` in
two places: (1) ``context.fastmcp_context.set_state(...)`` for tools that
accept a ``ctx`` parameter, and (2) the ``caller_identity_var`` contextvar
in ``megalos_server.identity_ctx`` so tools that don't accept ``ctx`` can
read the caller identity without changing their signature. Today every
request is tagged ANONYMOUS_IDENTITY — the seam becomes load-bearing in
Phase G when bearer auth attaches a concrete subject here.

RateLimitMiddleware — consults the RateLimiter primitive on every tool
call. Transport-aware: stdio -> session axis only; HTTP -> session + IP
axes; the session-create tool (``start_workflow``) additionally consults
the ip_session_create axis BEFORE the others. Transport is detected
explicitly via ``ctx.fastmcp_context.transport`` — never inferred from
IP presence.

T01 decision on denial path: when any consulted axis denies, T01 DOES
NOT construct the full ``rate_limited`` error envelope — that contract
lands in T02 alongside the tool-surface integration. T01's middleware
hook is a pass-through: it calls ``try_consume`` for observation
(ensuring the seam is exercised so T03's adversarial suite has something
to probe) and always calls ``call_next``. The deny metadata (scope +
retry_after_ms) is attached to ``ctx.fastmcp_context`` state under key
``rate_limit_denied`` so T02 can consume it when it wires the envelope.
State attachment is best-effort — if fastmcp_context is None (happens in
some in-process test dispatch paths) the middleware silently proceeds.

Note: _check_str / _trap_errors / oversize-payload checks in tools.py remain —
they handle empty-string and oversize cases that pydantic doesn't touch, and
serve as belt-and-suspenders for None/wrong-type if this hook is ever bypassed.
"""

from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext  # type: ignore[import-not-found]
from fastmcp.tools import ToolResult  # type: ignore[attr-defined]
from pydantic import ValidationError

from .errors import error_response
from .identity import ANONYMOUS_IDENTITY
from .identity_ctx import caller_identity_var
from .ratelimit import (
    AXIS_IP,
    AXIS_IP_SESSION_CREATE,
    AXIS_SESSION,
    RateLimiter,
)


class ValidationErrorMiddleware(Middleware):
    """Convert pydantic.ValidationError raised by tool dispatch to error_response."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        try:
            return await call_next(context)
        except ValidationError as exc:
            errs = exc.errors()
            if errs and errs[0].get("loc"):
                field = str(errs[0]["loc"][0])
                msg = str(errs[0].get("msg", str(exc)))
            else:
                field = "unknown"
                msg = str(exc)
            return ToolResult(
                structured_content=error_response("invalid_argument", msg, field=field),
            )


class CallerIdentityMiddleware(Middleware):
    """Attach ``caller_identity`` to the per-request context + contextvar.

    Today's constant is ANONYMOUS_IDENTITY. Phase G bearer-auth lands here
    without touching tool signatures: replace the ``identity`` expression
    below with a subject-bearing Identity derived from the validated bearer
    token. The access-check sites in tools.py stay unchanged."""

    async def on_request(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        identity = ANONYMOUS_IDENTITY
        # The caller-identity seam is read via the ``caller_identity_var``
        # contextvar (see megalos_server/identity_ctx.py) rather than
        # ``Context.set_state``. The contextvar works across every transport
        # (HTTP, stdio, in-process test dispatch) without requiring an
        # MCP session id; ``set_state`` requires one and raises in in-process
        # unit-test dispatch. No tool in the surface accepts a ``ctx``
        # parameter, so skipping the set_state call loses no observable
        # behavior today. Phase G can add an explicit ``set_state`` call
        # here if a tool ever needs to read the identity off the MCP-native
        # surface.
        token = caller_identity_var.set(identity)
        try:
            return await call_next(context)
        finally:
            caller_identity_var.reset(token)


# Tool name that creates a new session; consulted for the ip_session_create axis.
_SESSION_CREATE_TOOL = "start_workflow"

# FastMCP transport string values (Context.transport returns one of these or None).
_HTTP_TRANSPORTS = frozenset({"sse", "streamable-http", "http"})


class RateLimitMiddleware(Middleware):
    """Transport-aware rate-limit gate.

    Consults ``RateLimiter.try_consume`` on each tool call across the axes
    appropriate for the transport + tool. T01 does not construct the
    ``rate_limited`` error envelope itself — see module docstring.
    """

    def __init__(self, limiter: RateLimiter):
        self._limiter = limiter

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        # ``context.message`` is a ``CallToolRequestParams`` with ``name``
        # and ``arguments``. Pull both defensively — if the runtime ever
        # hands us a different shape we pass through rather than crash.
        msg = context.message
        tool_name = getattr(msg, "name", None) or ""
        arguments = getattr(msg, "arguments", None) or {}

        transport = _detect_transport(context)
        is_http = transport == "http"
        ip = _extract_ip(context) if is_http else None
        raw_sid = arguments.get("session_id") if isinstance(arguments, dict) else None
        # Only gate on session_id when it's a non-empty string. Pydantic
        # validation (ValidationErrorMiddleware) rejects non-string values
        # later; the limiter must not key a bucket on a dict/list/None.
        session_id = raw_sid if isinstance(raw_sid, str) and raw_sid else None

        denied: tuple[str, float] | None = None

        # Consult ip_session_create FIRST on session-create tool + HTTP.
        # The plan calls for this ordering so session-create bursts are
        # gated before any other bucket is touched.
        if tool_name == _SESSION_CREATE_TOOL and is_http and ip:
            allowed, retry_after_ms = self._limiter.try_consume(AXIS_IP_SESSION_CREATE, ip)
            if not allowed:
                denied = (AXIS_IP_SESSION_CREATE, retry_after_ms)

        # Session axis: only when we have a session_id to key on. Tools
        # without a session_id arg (e.g. list_workflows) skip this axis;
        # IP-axis-only gating applies for them. Stdio -> session-only
        # (if session_id present).
        if denied is None and session_id:
            allowed, retry_after_ms = self._limiter.try_consume(AXIS_SESSION, session_id)
            if not allowed:
                denied = (AXIS_SESSION, retry_after_ms)

        # IP axis: HTTP only.
        if denied is None and is_http and ip:
            allowed, retry_after_ms = self._limiter.try_consume(AXIS_IP, ip)
            if not allowed:
                denied = (AXIS_IP, retry_after_ms)

        if denied is not None:
            scope, retry_after_ms = denied
            _attach_denied_metadata(context, scope, retry_after_ms)

        return await call_next(context)


def _detect_transport(context: MiddlewareContext[Any]) -> str:
    """Return ``"stdio"``, ``"http"``, or ``"unknown"`` based on the
    FastMCP context. Detection is explicit: we read ``ctx.transport`` and
    bucket ``sse``/``streamable-http``/``http`` into ``"http"``."""
    fmctx = context.fastmcp_context
    if fmctx is None:
        return "unknown"
    transport = getattr(fmctx, "transport", None)
    if transport is None:
        return "unknown"
    if transport == "stdio":
        return "stdio"
    if transport in _HTTP_TRANSPORTS:
        return "http"
    return "unknown"


def _extract_ip(context: MiddlewareContext[Any]) -> str | None:
    """Best-effort remote-IP extraction from the active HTTP request.

    Uses ``get_http_request().client.host`` when available. Returns None
    on any failure (no active HTTP request, missing client tuple, etc.)
    — IP-axis gating is skipped in that case, which is the safe default
    for T01's observation-only posture. Proxy-header support
    (X-Forwarded-For) is deferred; document in docs/rate-limits.md when
    Phase G requires a fronted deployment.
    """
    try:
        from fastmcp.server.dependencies import get_http_request  # type: ignore[import-not-found]

        request = get_http_request()
    except Exception:
        return None
    client = getattr(request, "client", None)
    if client is None:
        return None
    host = getattr(client, "host", None)
    return host if isinstance(host, str) and host else None


def _attach_denied_metadata(
    context: MiddlewareContext[Any], scope: str, retry_after_ms: float
) -> None:
    """Best-effort: stash deny metadata on fastmcp_context state for T02
    to consume. Silently no-ops if the context is unavailable (some
    in-process test paths)."""
    fmctx = context.fastmcp_context
    if fmctx is None:
        return
    set_state = getattr(fmctx, "set_state", None)
    if set_state is None:
        return
    try:
        set_state(
            "rate_limit_denied",
            {"scope": scope, "retry_after_ms": retry_after_ms},
        )
    except Exception:
        # set_state can raise in in-process dispatch (no MCP session).
        # T01 ships observation-only; a missed attachment is non-fatal.
        pass
