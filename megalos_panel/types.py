"""Contract types for the cross-model panel utility.

PanelRequest and PanelResult are the typed surface exchanged between callers
and panel_query. A stable request_id correlates each request to its result
across the batch; default is a fresh uuid4 hex so authors rarely need to set
one explicitly. Selection parsing is a downstream concern — PanelResult.selection
carries the raw selected text, and error carries the provider-exhaustion reason
when a request could not be answered.
"""

from dataclasses import dataclass, field
from uuid import uuid4


def _new_request_id() -> str:
    return uuid4().hex


@dataclass
class PanelRequest:
    prompt: str
    model: str
    request_id: str = field(default_factory=_new_request_id)


@dataclass
class PanelResult:
    selection: str
    raw_response: str
    error: str | None
