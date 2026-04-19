"""Retry budget constants for panel provider calls.

These literals are the contract: 3 attempts for rate-limit (429) errors,
5 attempts for transient network errors (timeout, connection reset, 5xx),
exponential backoff capped at 30 seconds with a 1-second base. The
retry_with_backoff helper lives in a later task; this module intentionally
contains constants only.
"""

RATE_LIMIT_ATTEMPTS = 3
TRANSIENT_ATTEMPTS = 5
BACKOFF_CAP_SECONDS = 30
BACKOFF_BASE_SECONDS = 1
