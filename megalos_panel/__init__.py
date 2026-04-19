"""Public API for the cross-model panel utility.

Only the entry point, contract types, and record IO are re-exported from the
top-level package. Adapter classes are intentionally internal: callers dispatch
by model string through ``panel_query``, not by picking an adapter class.

This module stays SDK-free — importing ``megalos_panel`` must not transitively
load ``anthropic`` or ``openai``. The invariant is enforced by
``tests/test_panel_top_level_hermeticity.py`` (top-level surface) and
``tests/test_panel_adapters_hermeticity.py`` (adapters package surface).
"""

from .errors import PanelProviderError
from .panel import panel_query
from .record import RecordReader, RecordWriter
from .types import PanelRequest, PanelResult

__all__ = [
    "PanelProviderError",
    "PanelRequest",
    "PanelResult",
    "RecordReader",
    "RecordWriter",
    "panel_query",
]
