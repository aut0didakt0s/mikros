"""Contract tests for ``megalos_server.session_canon.normalize_session_id``.

One golden case per bypass class (case / whitespace / Unicode NFC-NFD),
plus idempotence, backward-compat on URL-safe tokens, and a combined
case-exercise. The contract is a design artifact: a future regression in
``normalize_session_id`` fails a real test here rather than silently
re-opening the rate-limit bypass surfaced by T03.

BACKWARD-COMPAT ANCHOR — READ BEFORE "OPTIMISING":
``test_url_safe_token_idempotent_fold`` is the load-bearing proof that
normalisation is a NO-OP for legitimate session flows. The real session
id generator (``secrets.token_urlsafe(32)``) produces URL-safe base64,
which contains no case-variant letters-in-both-forms, no non-ASCII code
points, no whitespace — every live session_id round-trips unchanged
through NFC + casefold + strip. Normalisation only bites on attacker-
supplied keys (the three bypass classes T03 surfaced). A future
maintainer tempted to "skip normalisation for performance" on the hot
path would re-open the exact bypass this milestone closed; the
round-trip test is what keeps that option off the table.
"""

from __future__ import annotations

import secrets
import unicodedata

from megalos_server.session_canon import normalize_session_id


# ---------------------------------------------------------------------------
# Case folding
# ---------------------------------------------------------------------------


def test_case_variants_collapse_to_casefold_form():
    """Case-different strings collapse to a single canonical form."""
    assert normalize_session_id("ABCdef") == normalize_session_id("abcdef")
    assert normalize_session_id("ABCdef") == "abcdef"


def test_mixed_case_unicode_collapses_via_casefold():
    """casefold folds Unicode-aware cases (e.g. German sharp-s)."""
    # 'STRASSE' and 'straße' are case-equivalent under casefold (both -> 'strasse').
    assert normalize_session_id("STRASSE") == normalize_session_id("straße")


# ---------------------------------------------------------------------------
# Whitespace stripping
# ---------------------------------------------------------------------------


def test_leading_trailing_spaces_stripped():
    assert normalize_session_id(" abc ") == "abc"


def test_tabs_and_newlines_stripped():
    assert normalize_session_id("\tabc\n") == "abc"
    assert normalize_session_id("  abc\t\n ") == "abc"


# ---------------------------------------------------------------------------
# Unicode NFC / NFD collapse
# ---------------------------------------------------------------------------


def test_nfc_and_nfd_encodings_collapse():
    """Byte-distinct NFC and NFD encodings of the same glyph string collapse."""
    nfc = unicodedata.normalize("NFC", "caf\u00e9")
    nfd = unicodedata.normalize("NFD", "cafe\u0301")
    # Setup invariant: the inputs really are byte-distinct.
    assert nfc != nfd
    assert normalize_session_id(nfc) == normalize_session_id(nfd)


# ---------------------------------------------------------------------------
# Idempotence — canonical form is a fixed point
# ---------------------------------------------------------------------------


def test_idempotence_on_case_variant():
    x = "ABCdef"
    once = normalize_session_id(x)
    twice = normalize_session_id(once)
    assert once == twice


def test_idempotence_on_whitespace_variant():
    x = "  abc\t"
    once = normalize_session_id(x)
    twice = normalize_session_id(once)
    assert once == twice


def test_idempotence_on_unicode_variant():
    x = "cafe\u0301"  # NFD form
    once = normalize_session_id(x)
    twice = normalize_session_id(once)
    assert once == twice


# ---------------------------------------------------------------------------
# URL-safe token property: idempotent fold (becomes canonical on first write)
# ---------------------------------------------------------------------------


def test_url_safe_tokens_fold_idempotently():
    """``secrets.token_urlsafe(32)`` output contains uppercase letters, so
    casefold DOES transform it — but the transformed form is itself
    idempotent under the normaliser. ``state.create_session`` writes the
    normalised form as the DB row key; every subsequent lookup normalises
    the caller-supplied arg identically, so the write and all lookups
    agree on the same canonical key.

    This is NOT a "round-trip unchanged" property — if it were, case
    variants of URL-safe tokens would key distinct rows, which is exactly
    the bypass the normaliser is closing. The property that matters is
    idempotence: once normalised, the form is stable."""
    for _ in range(10):
        tok = secrets.token_urlsafe(32)
        once = normalize_session_id(tok)
        twice = normalize_session_id(once)
        assert once == twice
        # Different case variants of the same token collapse.
        assert normalize_session_id(tok.upper()) == once
        assert normalize_session_id(tok.lower()) == once


# ---------------------------------------------------------------------------
# Combined bypass classes
# ---------------------------------------------------------------------------


def test_combined_case_unicode_whitespace_collapse():
    """A string combining all three bypass classes collapses to the same
    canonical form as the minimal version."""
    combined = " CAF\u00c9 "  # uppercase NFC with surrounding whitespace
    minimal = "cafe\u0301"    # NFD, lowercase, no whitespace
    assert normalize_session_id(combined) == normalize_session_id(minimal)
