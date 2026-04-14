#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
source tests/lib/assert.sh

TMPL_DIR=".megalos/templates"

for tmpl in STATE.md.tmpl PROJECT.md.tmpl DECISIONS.md.tmpl ROADMAP.md.tmpl S-PLAN.md.tmpl T-PLAN.md.tmpl T-SUMMARY.md.tmpl; do
  assert_file_exists "$TMPL_DIR/$tmpl" "template $tmpl exists"
done

assert_file_contains "$TMPL_DIR/STATE.md.tmpl"     "active_milestone" "STATE has active_milestone"
assert_file_contains "$TMPL_DIR/STATE.md.tmpl"     "active_slice"     "STATE has active_slice"
assert_file_contains "$TMPL_DIR/STATE.md.tmpl"     "active_task"      "STATE has active_task"
assert_file_contains "$TMPL_DIR/STATE.md.tmpl"     "loc_budget"       "STATE has loc_budget"
assert_file_contains "$TMPL_DIR/DECISIONS.md.tmpl" "DECISIONS"        "DECISIONS header present"
assert_file_contains "$TMPL_DIR/ROADMAP.md.tmpl"   "Slices"           "ROADMAP has Slices section"
assert_file_contains "$TMPL_DIR/S-PLAN.md.tmpl"    "Must-haves"       "S-PLAN has Must-haves"
assert_file_contains "$TMPL_DIR/T-PLAN.md.tmpl"    "Truths"           "T-PLAN has Truths"
assert_file_contains "$TMPL_DIR/T-PLAN.md.tmpl"    "Artifacts"        "T-PLAN has Artifacts"
assert_file_contains "$TMPL_DIR/T-PLAN.md.tmpl"    "Key Links"        "T-PLAN has Key Links"
assert_file_contains "$TMPL_DIR/T-SUMMARY.md.tmpl" "Files modified"   "T-SUMMARY has Files modified"

test_summary
