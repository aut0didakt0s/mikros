"""Identity value object for the session owner/caller access-check seam.

Today every request is anonymous: both the caller's identity (set by
middleware into the per-request context) and the session's owner_identity
(attached at session-dict materialization time) carry the single shape
``{"kind": "anonymous"}``. The access-check at every session-scoped tool
compares ``caller_identity == session["owner_identity"]`` and is therefore a
structural no-op today.

The seam exists so that a future Phase G bearer-auth path can land without
re-architecting the session model or touching every tool site a second time.
When bearer auth arrives, the shape extends to carry a subject claim:

    {"kind": "bearer", "subject": "<stable caller id>"}

and the same ``==`` comparison becomes load-bearing — mismatched owners are
rejected via ``cross_session_access_denied``. Until that lands the error code
is present, tested, and structurally unreachable.

Kept as a ``TypedDict`` with ``total=False`` so the optional ``subject`` key
is type-legal on ``anonymous`` values (which omit it) without forcing a
discriminated-union dance in every comparison site.
"""

from typing import Literal, TypedDict


class Identity(TypedDict, total=False):
    """Caller / session-owner identity. ``kind`` is always present; ``subject``
    is only set when ``kind == "bearer"`` (Phase G; not emitted today)."""

    kind: Literal["anonymous", "bearer"]
    subject: str


ANONYMOUS_IDENTITY: Identity = {"kind": "anonymous"}


__all__ = ["Identity", "ANONYMOUS_IDENTITY"]
