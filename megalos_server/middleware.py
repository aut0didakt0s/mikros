"""FastMCP middleware: validation-error normalization + caller-identity seam.

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
