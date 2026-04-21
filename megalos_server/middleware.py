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

On denial the middleware SHORT-CIRCUITS BEFORE DISPATCH: it returns a
``rate_limited`` error envelope wrapped in ``ToolResult`` and never
calls ``call_next``. This is load-bearing — wrapping the response on
the way out would leave DB writes committed and sessions created
before the envelope fired, which would make the rate limit decorative.
The envelope shape matches ``ValidationErrorMiddleware`` so every
error category flows through the same tool-response path. The envelope
carries ``code``, ``error``, ``retry_after_ms``, and ``scope``; when
the failing axis is the session axis the envelope also carries
``session_fingerprint`` (log-safe identifier, never the raw
session_id). NO bucket-capacity, current-count, or raw-IP fields — those
would hand an attacker telemetry.

Every denial also emits a structured WARN log
(``event: rate_limit_exceeded``, scope, identity, retry_after_ms) via
``emit_rate_limit_warn``. Dedupe by (scope, identity, 60s window) so a
sustained attack produces one log line per minute per (scope, identity)
rather than one line per request — keeps the three-minute incident-
response signal intact without flooding log aggregation.

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
    emit_rate_limit_warn,
    hash_ip,
)
from .state import _compute_fingerprint


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
#
# DRIFT SURFACE: this allowlist is a single-string constant. Any future
# session-creating tool added to the surface (e.g. "start_workflow_v2",
# "clone_session", a bulk-start helper) must be added here explicitly, OR
# the per-IP-session-create axis bypasses it. Keep the audit at
# ci/no_raw_session_id_in_logs.sh precedent in mind: when adding any tool
# whose purpose is to create a session, extend this constant to a frozenset
# and update the comparison below. The session-create classifier is
# semantic, not syntactic — there is no reliable @decorator signal today.
_SESSION_CREATE_TOOL = "start_workflow"

# FastMCP transport string values (Context.transport returns one of these or None).
_HTTP_TRANSPORTS = frozenset({"sse", "streamable-http", "http"})


class RateLimitMiddleware(Middleware):
    """Transport-aware rate-limit gate.

    Consults ``RateLimiter.try_consume`` on each tool call across the axes
    appropriate for the transport + tool. On denial, returns a
    ``rate_limited`` error envelope wrapped in ``ToolResult`` (no
    ``call_next``) and emits a deduped WARN log. See module docstring.
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
            envelope = _build_rate_limited_envelope(scope, retry_after_ms, session_id)
            _emit_deny_warn(scope, retry_after_ms, session_id, ip)
            return ToolResult(structured_content=envelope)

        return await call_next(context)


def _build_rate_limited_envelope(
    scope: str, retry_after_ms: float, session_id: str | None
) -> dict[str, Any]:
    """Build the ``rate_limited`` error envelope.

    Always carries ``code``, ``error``, ``retry_after_ms``, ``scope``. When
    the failing axis is the session axis and ``session_id`` is a valid
    string, attaches ``session_fingerprint`` (hashed — never the raw
    session_id). NO bucket capacity, current token count, or raw IP
    fields: those would hand an attacker telemetry. The grep gate
    (ci/no_raw_session_id_in_logs.sh) stays green because the fingerprint
    key name is ``session_fingerprint``, not ``session_id``.
    """
    extras: dict[str, Any] = {"retry_after_ms": retry_after_ms, "scope": scope}
    if scope == AXIS_SESSION and isinstance(session_id, str) and session_id:
        extras["session_fingerprint"] = _compute_fingerprint(session_id)
    return error_response("rate_limited", "rate limit exceeded", **extras)


def _emit_deny_warn(
    scope: str, retry_after_ms: float, session_id: str | None, ip: str | None
) -> None:
    """Emit the deduped WARN log line for a denial.

    Derives the fingerprint identity per axis:
    - session axis      -> session_fingerprint (sha256/12 of session_id)
    - ip / ip_session_* -> ip_fingerprint      (sha256/12 of raw IP)

    If the expected identity field is absent (e.g. session axis but
    session_id somehow falsy by the time we got here), we fall back to a
    sentinel so the dedupe key is stable and the log line still emits.
    """
    if scope == AXIS_SESSION and isinstance(session_id, str) and session_id:
        identity = _compute_fingerprint(session_id)
        identity_kind = "session_fingerprint"
    elif scope in (AXIS_IP, AXIS_IP_SESSION_CREATE) and isinstance(ip, str) and ip:
        identity = hash_ip(ip)
        identity_kind = "ip_fingerprint"
    else:
        # Should not happen: denial implies the axis's key was present
        # upstream. Keep a stable fallback so the log line isn't lost.
        identity = "unknown"
        identity_kind = "session_fingerprint" if scope == AXIS_SESSION else "ip_fingerprint"
    emit_rate_limit_warn(scope, identity, identity_kind, retry_after_ms)


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


