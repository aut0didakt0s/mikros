"""Unit tests for megalos_panel.errors.PanelProviderError.

Verifies the class is named as the mechanical gate expects (contract
commitment C) and that the three attributes round-trip through raise/catch.
"""

import pytest

from megalos_panel.errors import PanelProviderError


def test_panel_provider_error_is_exception_subclass():
    assert issubclass(PanelProviderError, Exception)


def test_panel_provider_error_carries_fields():
    with pytest.raises(PanelProviderError) as exc_info:
        raise PanelProviderError(
            model="claude-opus-4-7", attempts=3, last_error="429 rate limited"
        )
    err = exc_info.value
    assert err.model == "claude-opus-4-7"
    assert err.attempts == 3
    assert err.last_error == "429 rate limited"


def test_panel_provider_error_message_mentions_fields():
    err = PanelProviderError(model="gpt-5", attempts=5, last_error="timeout")
    msg = str(err)
    assert "gpt-5" in msg
    assert "5" in msg
    assert "timeout" in msg
