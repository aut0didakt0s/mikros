"""Panel provider error surface.

PanelProviderError is raised when a provider call exhausts its retry budget.
Carries the model identifier, the number of attempts made, and the last
underlying error message for downstream diagnostics.

RateLimitError and TransientError are the internal taxonomy emitted by adapter
implementations. Adapters translate provider-specific SDK exceptions into one
of these two classes; the retry layer distinguishes them to pick the right
attempt budget (rate-limit budget vs. transient-network budget). Both are
internal and never surface to callers — retry either succeeds or wraps the
last attempt into PanelProviderError.
"""


class PanelProviderError(Exception):
    def __init__(self, model: str, attempts: int, last_error: str) -> None:
        super().__init__(
            f"panel provider error: model={model} attempts={attempts} last_error={last_error}"
        )
        self.model = model
        self.attempts = attempts
        self.last_error = last_error


class RateLimitError(Exception):
    """Raised by adapters when the provider returns a 429 rate-limit signal."""


class TransientError(Exception):
    """Raised by adapters for retryable network errors (timeout, connection reset, 5xx)."""
