#!/usr/bin/env bash
# Append one tool call to the audit log outside the estate.
# Usage: bin/audit-append.sh <profile> <tool> <one-line summary>
set -euo pipefail
# Derived from this script's own location, never from a path typed twice. The
# hardcoded $HOME/Documents/code here was stale from the day DECISIONS.md ruled
# that every repo lives under ~/dev/code, and it disagreed with the requirement
# that grades it, so the log was written to one path and read from another.
H="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$(cd "$H/.." && pwd)/hermes-audit"
LOG="$DIR/toolcalls.log"
mkdir -p "$DIR"
# Append-only. The flag is `uappnd`, which the owner can clear, so this raises the
# cost of a silent rewrite rather than making one impossible; `sappnd` needs root
# and a raised securelevel, which this machine does not run. What it does buy is
# that a truncation or an in-place edit fails loudly instead of succeeding.
if [ ! -e "$LOG" ]; then
  : > "$LOG"
  chflags uappnd "$LOG" 2>/dev/null || true
fi
printf '%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${1:-?}" "${2:-?}" "${3:-}" >> "$LOG"
