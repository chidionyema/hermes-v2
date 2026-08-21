#!/usr/bin/env bash
# Append one tool call to the audit log outside the estate.
# Usage: bin/audit-append.sh <profile> <tool> <one-line summary>
set -euo pipefail
LOG="$HOME/Documents/code/hermes-audit/toolcalls.log"
printf '%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${1:-?}" "${2:-?}" "${3:-}" >> "$LOG"
