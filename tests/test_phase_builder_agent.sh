#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

FILE=".claude/agents/phase-builder.md"

assert_file_exists "$FILE" "phase-builder.md exists"

if ! python3 tests/validate_frontmatter.py "$FILE" name description tools isolation maxTurns skills 2>/dev/null; then
  TESTS_RUN=$((TESTS_RUN + 1))
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAIL: phase-builder.md frontmatter missing required keys" >&2
fi

assert_file_contains "$FILE" "phase-builder"                "name is phase-builder"
assert_file_contains "$FILE" "worktree"                     "isolation is worktree"
assert_file_contains "$FILE" "maxTurns: 30"                 "maxTurns is 30"
assert_file_contains "$FILE" "simplicity-guard"             "preloads simplicity-guard skill"
assert_file_contains "$FILE" "pre-loaded"                   "mentions pre-loaded dispatch"
assert_file_contains "$FILE" "Must-haves"                   "mentions Must-haves"
assert_file_contains "$FILE" "### Files modified"           "required summary format: Files modified"
assert_file_contains "$FILE" "### Decisions"                "required summary format: Decisions"
assert_file_contains "$FILE" "### Verification output"      "required summary format: Verification output"

# docmancer grounding must be wired: tool allowed + discipline section present.
assert_file_contains "$FILE" "Bash(docmancer:*)"             "allowlist: docmancer"
assert_file_contains "$FILE" "## Grounding discipline"       "grounding discipline section present"
assert_file_contains "$FILE" "always query docmancer"        "grounding discipline: always query"
assert_file_contains "$FILE" 'docmancer query "<topic>"'     "grounding discipline: query example"

# Tool allowlist must cover mainstream languages. Spot-check one binary per ecosystem.
assert_file_contains "$FILE" "Bash(git:*)"         "allowlist: git"
assert_file_contains "$FILE" "Bash(ruff:*)"        "allowlist: Python (ruff)"
assert_file_contains "$FILE" "Bash(pytest:*)"      "allowlist: Python (pytest)"
assert_file_contains "$FILE" "Bash(npm:*)"         "allowlist: JS (npm)"
assert_file_contains "$FILE" "Bash(tsc:*)"         "allowlist: TS (tsc)"
assert_file_contains "$FILE" "Bash(cargo:*)"       "allowlist: Rust (cargo)"
assert_file_contains "$FILE" "Bash(go:*)"          "allowlist: Go (go)"
assert_file_contains "$FILE" "Bash(bundle:*)"      "allowlist: Ruby (bundle)"
assert_file_contains "$FILE" "Bash(mvn:*)"         "allowlist: Java (mvn)"
assert_file_contains "$FILE" "Bash(gradle:*)"      "allowlist: JVM (gradle)"
assert_file_contains "$FILE" "Bash(dotnet:*)"      "allowlist: .NET (dotnet)"
assert_file_contains "$FILE" "Bash(swift:*)"       "allowlist: Swift (swift)"
assert_file_contains "$FILE" "Bash(clang:*)"       "allowlist: C/C++ (clang)"
assert_file_contains "$FILE" "Bash(make:*)"        "allowlist: make"
assert_file_contains "$FILE" "Bash(mix:*)"         "allowlist: Elixir (mix)"
assert_file_contains "$FILE" "Bash(cabal:*)"       "allowlist: Haskell (cabal)"
assert_file_contains "$FILE" "Bash(php:*)"         "allowlist: PHP (php)"
assert_file_contains "$FILE" "Bash(shellcheck:*)"  "allowlist: shell (shellcheck)"

# Caveman mode: agent must honor the dispatch-prompt CAVEMAN_MODE line and
# exclude load-bearing sections (Worktree, Verification output) from caveman.
assert_file_contains "$FILE" "## Caveman mode"              "caveman mode section present"
assert_file_contains "$FILE" "CAVEMAN_MODE: on"             "caveman mode: recognizes on"
assert_file_contains "$FILE" "CAVEMAN_MODE: off"            "caveman mode: recognizes off"
assert_file_contains "$FILE" "\`### Worktree\` section"     "caveman mode: excludes Worktree section"
assert_file_contains "$FILE" "\`### Verification output\` block" "caveman mode: excludes Verification output"

# Destructive ops must stay out of the allowlist.
assert_file_not_contains "$FILE" "Bash(rm:"       "no Bash(rm:*)"
assert_file_not_contains "$FILE" "Bash(curl:"     "no Bash(curl:*)"
assert_file_not_contains "$FILE" "Bash(ssh:"      "no Bash(ssh:*)"
assert_file_not_contains "$FILE" "Bash(sudo:"     "no Bash(sudo:*)"
assert_file_not_contains "$FILE" "Bash(wget:"     "no Bash(wget:*)"
assert_file_not_contains "$FILE" "Bash(dd:"       "no Bash(dd:*)"

test_summary
