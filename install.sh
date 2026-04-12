#!/usr/bin/env bash
# mikrós installer — copies the template into a target project and
# installs the runtime dependencies (caveman plugin, docmancer skill).
#
# Usage: ./install.sh /path/to/target-project
set -euo pipefail

TARGET="${1:?usage: ./install.sh /path/to/target-project}"

if [ ! -d "$TARGET" ]; then
  echo "mikrós install: target does not exist: $TARGET" >&2
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"

# --- 1. Copy template files ---------------------------------------------
cp -r "$HERE/.claude"  "$TARGET/.claude"
cp -r "$HERE/.mikros"  "$TARGET/.mikros"
cp    "$HERE/CLAUDE.md" "$TARGET/CLAUDE.md"
cp    "$HERE/.mcp.json" "$TARGET/.mcp.json"

# --- 2. Seed .mikros state from templates -------------------------------
cp "$TARGET/.mikros/templates/STATE.md.tmpl"     "$TARGET/.mikros/STATE.md"
cp "$TARGET/.mikros/templates/PROJECT.md.tmpl"   "$TARGET/.mikros/PROJECT.md"
cp "$TARGET/.mikros/templates/DECISIONS.md.tmpl" "$TARGET/.mikros/DECISIONS.md"

# --- 3. Install caveman plugin ------------------------------------------
# https://github.com/JuliusBrussee/caveman — output token reduction.
if command -v claude >/dev/null 2>&1; then
  claude plugin marketplace add JuliusBrussee/caveman
  claude plugin install caveman@caveman
else
  echo "mikrós install: 'claude' CLI not on PATH — skipping caveman plugin install" >&2
fi

# --- 4. Install docmancer skill for doc grounding -----------------------
# https://github.com/docmancer/docmancer — local doc retrieval.
if command -v docmancer >/dev/null 2>&1; then
  docmancer install claude-code
else
  echo "mikrós install: 'docmancer' CLI not on PATH — skipping docmancer skill install" >&2
fi

# --- 5. Compress CLAUDE.md via caveman-compress -------------------------
# Produces a compressed CLAUDE.md and keeps CLAUDE.original.md as the
# human-readable backup the user edits.
if command -v claude >/dev/null 2>&1; then
  ( cd "$TARGET" && claude -p "/caveman:compress CLAUDE.md" )
fi

echo
echo "mikrós installed into $TARGET"
echo "Caveman intensity default: Full"
echo "Next: cd $TARGET && claude, then /discuss to start your first milestone"
