#!/usr/bin/env bash
# caveman-phase.sh — decide whether caveman-speak should be active for a
# given mikrós command phase. Reads .mikros/config (project-local) if
# present, otherwise uses defaults. Prints a single word: "true" or
# "false". No side effects.
#
# Usage:
#   bash .claude/lib/caveman-phase.sh active <phase>
#
# Phases: discuss | plan-slice | execute-task | sniff-test | compress
#
# Config file .mikros/config — shell-style key=value, one per line:
#   caveman_mode=on            # on|off — project-wide master switch
#   caveman_phases=execute-task,sniff-test,compress
#
# Defaults: mode=on, phases=execute-task,sniff-test,compress.
# /discuss and /plan-slice are intentionally excluded from the default
# phase list — they produce specs and plans that humans re-read.
#
# This file exists because mikrós uses the Cavekit pattern: caveman is
# applied per phase, not session-wide, so drafting/planning stays in
# normal prose and execution/review/compression phases get compressed.

set -e

ACTION="${1:-}"
PHASE="${2:-}"

if [ "$ACTION" != "active" ] || [ -z "$PHASE" ]; then
  echo "usage: $0 active <phase>" >&2
  exit 2
fi

MODE="on"
PHASES="execute-task,sniff-test,compress"

if [ -f .mikros/config ]; then
  while IFS='=' read -r key val; do
    # Strip surrounding whitespace from val.
    val="${val## }"; val="${val%% }"
    case "$key" in
      caveman_mode)   [ -n "$val" ] && MODE="$val"   ;;
      caveman_phases) [ -n "$val" ] && PHASES="$val" ;;
    esac
  done < <(grep -E '^(caveman_mode|caveman_phases)=' .mikros/config 2>/dev/null || true)
fi

if [ "$MODE" != "on" ]; then
  echo "false"
  exit 0
fi

IFS=',' read -ra LIST <<< "$PHASES"
for p in "${LIST[@]}"; do
  p="${p## }"; p="${p%% }"
  if [ "$p" = "$PHASE" ]; then
    echo "true"
    exit 0
  fi
done
echo "false"
