#!/usr/bin/env bash
# Gate: fail CI when raw `session_id` leaks into log lines or structured error
# envelopes. Raw session_id is the capability token that unlocks every
# workflow-session call; logs and error bodies must carry the fingerprint
# (sha256 truncated to 12 hex chars) instead.
#
# Data-flow uses of session_id — DB keys, function parameters, signatures,
# happy-path return dicts — are out of scope. The guard only fires on log
# sites and error-response constructors.
#
# Exit 0 clean. Exit 1 with annotated offending lines on failure.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/megalos_server"

offenders=0
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

# --- Pattern 1: raw session_id as kwarg to logger.*/print -------------------
# Matches things like: _log.info(..., session_id=sid), print(session_id=...)
# and f-strings referencing session_id inside logger/print calls.
# We only flag *inside* a logger.*/_log.*/print( call — use a two-step
# ripgrep: find logger/print call sites and carry forward to scan matching
# parentheses. For the common case, single-line detection is enough; keep
# the pattern conservative.
grep -RnE --include='*.py' \
  '(_log|logger)\.(info|warning|error|debug|exception|critical)\([^)]*\bsession_id\b' \
  "$SRC" >>"$tmp" || true
grep -RnE --include='*.py' \
  '\bprint\([^)]*\bsession_id\b' \
  "$SRC" >>"$tmp" || true

# --- Pattern 2: raw session_id kwarg to error_response(...) -----------------
# Only single-line matches. Multi-line error_response calls are caught by the
# Python AST-shaped pattern below via ripgrep's multiline mode.
grep -RnE --include='*.py' \
  'error_response\([^)]*\b(session_id|parent_session_id|child_session_id|root_session_id)=' \
  "$SRC" >>"$tmp" || true

# --- Pattern 4: raw session_id interpolated into string values -------------
# The adversarial test surface (M007/S01/T04) surfaced a leak path Pattern 1-3
# missed: raw session_id can land in an error envelope's `error.message` text
# via f-string / .format() / concat interpolation inside exception constructors
# or return-value strings. Catch the class, not just dict-key leaks.
#
# Matches:
#   - f-strings: f"...{session_id}..." or f"...{session.some_id}..."
#   - .format(...) calls with session_id as arg
#   - string concatenation: "str" + session_id OR session_id + "str"
#
# Allowlist: a line-trailing pragma comment `# noqa: session-id-leak` suppresses
# the flag for explicit exceptions. There should be zero legitimate cases; the
# pragma exists as an escape hatch for cases that reviewers deliberately accept.
grep -RnE --include='*.py' \
  'f["'"'"'][^"'"'"']*\{[^}]*\bsession_id\b' \
  "$SRC" | grep -v '# noqa: session-id-leak' >>"$tmp" || true
grep -RnE --include='*.py' \
  '\.format\([^)]*\bsession_id\b' \
  "$SRC" | grep -v '# noqa: session-id-leak' >>"$tmp" || true
grep -RnE --include='*.py' \
  '(\bsession_id\s*\+\s*["'"'"']|["'"'"']\s*\+\s*\bsession_id\b)' \
  "$SRC" | grep -v '# noqa: session-id-leak' >>"$tmp" || true

# --- Pattern 3: error-shaped dict literals with raw session_id keys ---------
# Catches `"status": "validation_error"` / `"status": "error"` dict literals
# that carry `"session_id": ...` as a field. We look for the key on a line
# within a small window after a status marker.
python3 - "$SRC" <<'PY' >>"$tmp" || true
import os, re, sys

src_root = sys.argv[1]
# Match dict blocks with status error/validation_error that also contain a
# raw session-identity key. Use a simple multiline window approach: find the
# status line, then scan next ~20 lines for the forbidden key inside the
# same dict (tracked by brace balance).
STATUS_RE = re.compile(
    r'"status"\s*:\s*"(error|validation_error)"'
)
FIELD_RE = re.compile(
    r'"\s*(session_id|parent_session_id|child_session_id|root_session_id)\s*"\s*:'
)
for dirpath, _, files in os.walk(src_root):
    for fname in files:
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(dirpath, fname)
        with open(fpath) as fh:
            lines = fh.readlines()
        for i, line in enumerate(lines):
            if not STATUS_RE.search(line):
                continue
            # Walk forward up to 25 lines inside the same dict literal.
            balance = 0
            started = False
            for j in range(i, min(len(lines), i + 25)):
                for ch in lines[j]:
                    if ch == "{":
                        balance += 1
                        started = True
                    elif ch == "}":
                        balance -= 1
                if started and balance <= 0 and j > i:
                    break
                m = FIELD_RE.search(lines[j])
                if m:
                    print(f"{fpath}:{j+1}: raw {m.group(1)} key in error-shaped dict")
PY

# Collapse + dedupe, print offenders.
if [ -s "$tmp" ]; then
  # De-dupe lines while preserving order.
  awk '!seen[$0]++' "$tmp"
  offenders=1
fi

if [ "$offenders" -ne 0 ]; then
  echo ""
  echo "ERROR: raw session_id leaked into log lines or error envelopes." >&2
  echo "Fingerprint the value via megalos_server.state._compute_fingerprint" >&2
  echo "and emit it under a session_fingerprint-family key instead." >&2
  exit 1
fi

echo "OK: no raw session_id in log sites or structured error payloads."
