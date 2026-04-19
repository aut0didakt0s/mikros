"""Panel provider error surface.

PanelProviderError is raised when a provider call exhausts its retry budget.
Carries the model identifier, the number of attempts made, and the last
underlying error message for downstream diagnostics.
"""


class PanelProviderError(Exception):
    def __init__(self, model: str, attempts: int, last_error: str) -> None:
        super().__init__(
            f"panel provider error: model={model} attempts={attempts} last_error={last_error}"
        )
        self.model = model
        self.attempts = attempts
        self.last_error = last_error
