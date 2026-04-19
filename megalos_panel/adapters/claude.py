"""Claude (Anthropic) adapter for the panel utility.

Uses the Anthropic Messages API (``client.messages.create``) with a single
user-role message holding the request prompt. Returns the first text block of
the assistant response. Provider-specific exceptions are translated into the
internal retry taxonomy so the retry layer stays SDK-agnostic.

This module imports the ``anthropic`` SDK at top-level — it is declared as a
[panel] optional extra. The package-level ``megalos_panel.adapters.__init__``
avoids importing this module unless an adapter is actually dispatched.
"""

import os

import anthropic

from megalos_panel.errors import RateLimitError, TransientError
from megalos_panel.types import PanelRequest


class ClaudeAdapter:
    def __init__(
        self,
        model: str = "claude-opus-4-7",
        api_key: str | None = None,
    ) -> None:
        resolved = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
        if not resolved:
            raise ValueError(
                "ClaudeAdapter requires an API key: pass api_key=... or set "
                "the ANTHROPIC_API_KEY environment variable"
            )
        self.model = model
        self._client = anthropic.Anthropic(api_key=resolved)

    def invoke(self, request: PanelRequest) -> str:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": request.prompt}],
            )
        except anthropic.RateLimitError as exc:
            raise RateLimitError(str(exc)) from exc
        except anthropic.APITimeoutError as exc:
            raise TransientError(str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise TransientError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            if 500 <= getattr(exc, "status_code", 0) < 600:
                raise TransientError(str(exc)) from exc
            raise

        for block in response.content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                return text
        return ""
