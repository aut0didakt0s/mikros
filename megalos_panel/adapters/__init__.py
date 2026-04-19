"""Provider adapter protocol and registry.

Adapters translate a PanelRequest into a single provider-specific API call and
return the assistant's text. They also classify provider exceptions into the
internal retry taxonomy (megalos_panel.errors.RateLimitError / TransientError)
so the retry layer can pick the right attempt budget without knowing about any
particular SDK.

This module stays SDK-free so that ``from megalos_panel.adapters import
Adapter, ADAPTERS, dispatch`` works without the [panel] extras installed.
ClaudeAdapter and OpenAIAdapter live in sibling modules that import their SDKs
at module top; those are only loaded when dispatch() actually needs them. The
SDK-free invariant is enforced by tests/test_panel_adapters_hermeticity.py.
"""

from functools import cache
from importlib import import_module
from typing import Protocol

from megalos_panel.types import PanelRequest


class Adapter(Protocol):
    def invoke(self, request: PanelRequest) -> str: ...


ADAPTERS: dict[str, str] = {
    "claude-": "megalos_panel.adapters.claude:ClaudeAdapter",
    "gpt-": "megalos_panel.adapters.openai:OpenAIAdapter",
}


@cache
def _load_adapter_class(prefix: str) -> type[Adapter]:
    module_path, _, class_name = ADAPTERS[prefix].partition(":")
    try:
        module = import_module(module_path)
    except ModuleNotFoundError as exc:
        missing = exc.name or module_path
        raise ModuleNotFoundError(
            f"panel adapter for {prefix!r} requires the {missing!r} SDK; "
            f"install with `pip install megalos[panel]` (or `uv sync --extra panel`)"
        ) from exc
    return getattr(module, class_name)  # type: ignore[no-any-return]


def dispatch(model: str) -> type[Adapter]:
    """Resolve an adapter class for ``model`` by longest-prefix match.

    Raises ValueError if no registered prefix matches. Longest-prefix wins so
    a more specific prefix (e.g. a hypothetical ``claude-opus-`` override)
    beats the generic ``claude-`` handler. Raises ModuleNotFoundError with an
    actionable install hint if the matched prefix's SDK is not installed.
    """
    matches = [p for p in ADAPTERS if model.startswith(p)]
    if not matches:
        raise ValueError(
            f"no panel adapter registered for model {model!r}; "
            f"known prefixes: {sorted(ADAPTERS)}"
        )
    return _load_adapter_class(max(matches, key=len))


__all__ = ["ADAPTERS", "Adapter", "dispatch"]
