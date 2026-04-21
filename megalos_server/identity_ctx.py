"""Per-request contextvar carrying the caller's Identity.

FastMCP's ``MiddlewareContext.fastmcp_context.set_state`` only reaches tools
that explicitly accept a ``ctx`` parameter. megalos' tool surface doesn't —
threading ``ctx`` through every tool signature is a mechanical change that
collides with the iron rule (each tool site already nears its context budget)
and produces noise for a future-ish seam.

A contextvar is the standard Python pattern for per-request state that
crosses function boundaries without signature change. FastMCP dispatches
each tool call on its own asyncio task, so the contextvar's copy-on-write
semantics keep per-request isolation intact.

CallerIdentityMiddleware.on_request sets the var at the framework boundary
and resets it on exit; tools read via ``caller_identity_var.get()``. Default
is ANONYMOUS_IDENTITY for direct-invocation code paths (tests that bypass
the middleware, in-process programmatic use) so reads never raise
LookupError."""

from contextvars import ContextVar

from .identity import ANONYMOUS_IDENTITY, Identity

caller_identity_var: ContextVar[Identity] = ContextVar(
    "megalos_caller_identity", default=ANONYMOUS_IDENTITY
)


__all__ = ["caller_identity_var"]
