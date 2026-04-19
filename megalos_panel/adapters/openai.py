"""OpenAI adapter for the panel utility.

Uses the OpenAI Chat Completions API (``client.chat.completions.create``) with
a single user-role message holding the request prompt. Returns the text of the
first choice's message. Provider-specific exceptions are translated into the
internal retry taxonomy so the retry layer stays SDK-agnostic.

This module imports the ``openai`` SDK at top-level — it is declared as a
[panel] optional extra. The package-level ``megalos_panel.adapters.__init__``
avoids importing this module unless an adapter is actually dispatched.
"""

import os

import openai

from megalos_panel.errors import RateLimitError, TransientError
from megalos_panel.types import PanelRequest


class OpenAIAdapter:
    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
    ) -> None:
        resolved = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        if not resolved:
            raise ValueError(
                "OpenAIAdapter requires an API key: pass api_key=... or set "
                "the OPENAI_API_KEY environment variable"
            )
        self.model = model
        self._client = openai.OpenAI(api_key=resolved)

    def invoke(self, request: PanelRequest) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": request.prompt}],
            )
        except openai.RateLimitError as exc:
            raise RateLimitError(str(exc)) from exc
        except openai.APITimeoutError as exc:
            raise TransientError(str(exc)) from exc
        except openai.APIConnectionError as exc:
            raise TransientError(str(exc)) from exc
        except openai.APIStatusError as exc:
            if 500 <= getattr(exc, "status_code", 0) < 600:
                raise TransientError(str(exc)) from exc
            raise

        choice = response.choices[0]
        content = choice.message.content
        return content if content is not None else ""
