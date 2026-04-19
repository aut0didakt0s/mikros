"""Unit tests for megalos_panel.retry constants.

Literal assertions are intentional — these values are the mechanical gate
check for contract commitment C. Value drift must fail the gate as signal,
not be softened into a range check.
"""

from megalos_panel import retry


def test_rate_limit_attempts_is_three():
    assert retry.RATE_LIMIT_ATTEMPTS == 3


def test_transient_attempts_is_five():
    assert retry.TRANSIENT_ATTEMPTS == 5


def test_backoff_cap_seconds_is_thirty():
    assert retry.BACKOFF_CAP_SECONDS == 30


def test_backoff_base_seconds_is_one():
    assert retry.BACKOFF_BASE_SECONDS == 1
