"""Session-id canonicalisation for bucket and DB key normalisation.

This module exists because the session_id flows through two independent
layers that must agree on what "same session" means:

- ``megalos_server.state`` — DB row primary key for ``sessions`` /
  ``session_stack``; lookup key for every RMW helper.
- ``megalos_server.middleware.RateLimitMiddleware`` — bucket key for the
  per-session rate-limit axis; also the input to the session fingerprint
  carried in deny envelopes and WARN logs.

If the two layers disagree on canonical form — even slightly — an attacker
can drive distinct buckets for the same logical session by varying case,
Unicode composition, or whitespace. ``test_ratelimit_adversarial.py``
surfaced three concrete bypasses in that family (case, NFC/NFD, whitespace).

The fix-shape guidance is deliberate: ONE canonicaliser, imported by both
layers. A limiter-local normaliser is the anti-pattern — two normalisers
agree today and drift in six months, silently re-opening the bypass.

Layering: stdlib-only (``unicodedata``). No imports from other
``megalos_server`` modules. Importable from both ``state`` and
``middleware``/``ratelimit`` without introducing a cycle.

Scope: NFC + casefold + strip. Nothing else.
    - No length cap — callers size-check where it matters.
    - No charset validation — the limiter accepts any string.
    - No empty-rejection — ``middleware`` short-circuits empty after the
      ``isinstance(raw_sid, str) and raw_sid`` guard; ``state`` writes
      reject the empty key at their own boundaries.

Backward compatibility: ``secrets.token_urlsafe(32)`` emits URL-safe
base64 (no case variance, no combining marks, no whitespace), so every
session_id created by ``state.create_session`` round-trips through
``normalize_session_id`` unchanged. Only attacker-supplied keys
(whatever shape they arrive in) are actually folded.
"""

from __future__ import annotations

import unicodedata


def normalize_session_id(sid: str) -> str:
    """Canonical form used as the bucket key and the DB row key.

    Collapses three bypass classes that would otherwise key distinct
    buckets / DB rows for the same logical session:

      - **Unicode:** NFC-normalise so NFC ``"caf\u00e9"`` and NFD
        ``"cafe\u0301"`` collapse to a single byte sequence.
      - **Case:** casefold (Unicode-aware lowercase-ish) so ``"AbcDef"``
        and ``"abcdef"`` collapse.
      - **Whitespace:** strip leading/trailing whitespace so ``"abc"``
        and ``" abc\t"`` collapse.

    Order matters: NFC first (settle the byte representation), then
    casefold (fold case on the canonical form), then strip (remove
    leading/trailing whitespace from the final form). Any other order
    produces subtly different outputs on edge cases (e.g. combining
    marks around whitespace).

    Idempotent: ``normalize_session_id(normalize_session_id(x)) ==
    normalize_session_id(x)`` for all ``x``.

    Does NOT validate emptiness, length, or charset. Those are concerns
    of the caller:
      - ``state.create_session`` validates shape at the write boundary.
      - ``middleware.RateLimitMiddleware`` short-circuits empty after
        its ``isinstance(raw_sid, str) and raw_sid`` guard.
    """
    return unicodedata.normalize("NFC", sid).casefold().strip()


__all__ = ["normalize_session_id"]
